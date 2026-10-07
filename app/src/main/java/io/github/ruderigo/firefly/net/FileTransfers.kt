package io.github.ruderigo.firefly.net

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.io.File

/**
 * Uploads to and downloads from a Stump's file shelf (CLIENT_QUICKSTART.md,
 * "Files (fservbot)"). File bodies are streamed here, never through the
 * engine: a node's shelf holds music and video, and Python would hold each
 * transfer in memory.
 *
 * One transfer at a time, on an app-wide scope, so leaving the screen doesn't
 * cancel it. The UI watches [current].
 */
object FileTransfers {
    enum class Kind { UPLOAD, DOWNLOAD }
    enum class State { RUNNING, DONE, FAILED }

    data class Transfer(
        val kind: Kind, val nodeKey: String, val name: String,
        val done: Long = 0, val total: Long = -1,
        val state: State = State.RUNNING,
        /** HTTP status of the answer (-1 when the node couldn't be reached). */
        val status: Int = 0,
        /** The node's own text ("Uploaded. Your balance: 4"), or the error. */
        val message: String = "",
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _current = MutableStateFlow<Transfer?>(null)
    val current: StateFlow<Transfer?> = _current
    lateinit var http: WifiHttp

    val busy get() = _current.value?.state == State.RUNNING

    fun clear() { if (!busy) _current.value = null }

    /** The name and size the picker gave us, if the provider knows them. */
    fun describe(context: Context, uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "file"
        var size = -1L
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val n = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val s = c.getColumnIndex(OpenableColumns.SIZE)
                if (n >= 0 && !c.isNull(n)) name = c.getString(n)
                if (s >= 0 && !c.isNull(s)) size = c.getLong(s)
            }
        }
        return name to size
    }

    /**
     * POST /upload: raw body, filename in X-Filename, optional X-Hash. The node
     * needs Content-Length, so a file of unknown size is first copied to the
     * cache to measure it.
     */
    fun upload(context: Context, nodeKey: String, url: String, uri: Uri, name: String, size: Long, slotHash: String?,
               onFinished: () -> Unit = {}) {
        if (busy) return
        _current.value = Transfer(Kind.UPLOAD, nodeKey, name, total = size)
        scope.launch {
            val cr = context.contentResolver
            var temp: File? = null
            val result = try {
                var length = size
                if (length < 0) {
                    temp = File.createTempFile("upload", null, context.cacheDir)
                    cr.openInputStream(uri)!!.use { i -> temp!!.outputStream().use { o -> i.copyTo(o) } }
                    length = temp!!.length()
                    _current.value = _current.value?.copy(total = length)
                }
                val input = temp?.inputStream() ?: cr.openInputStream(uri)!!
                input.use {
                    http.upload(url, name, slotHash?.trim()?.takeIf { h -> h.isNotEmpty() }, length, it) { done ->
                        _current.value = _current.value?.copy(done = done)
                    }
                }
            } catch (e: Exception) {
                HttpResult(-1, (e.message ?: e.javaClass.simpleName).toByteArray())
            } finally {
                temp?.delete()
            }
            finish(result, ok = result.status == 200)
            onFinished()
        }
    }

    /** GET /download?f=…, streamed into the document the user picked. */
    fun download(context: Context, nodeKey: String, url: String, name: String, size: Long, target: Uri) {
        if (busy) return
        _current.value = Transfer(Kind.DOWNLOAD, nodeKey, name, total = size)
        scope.launch {
            val cr = context.contentResolver
            val result = try {
                cr.openOutputStream(target, "w")!!.use { out ->
                    http.download(url, out, { total -> _current.value = _current.value?.copy(total = total) }) { done ->
                        _current.value = _current.value?.copy(done = done)
                    }
                }
            } catch (e: Exception) {
                HttpResult(-1, (e.message ?: e.javaClass.simpleName).toByteArray())
            }
            // Nothing usable was written: don't leave an empty or partial file behind.
            if (result.status != 200) runCatching { android.provider.DocumentsContract.deleteDocument(cr, target) }
            finish(result, ok = result.status == 200)
        }
    }

    private fun finish(r: HttpResult, ok: Boolean) {
        val text = String(r.body, Charsets.UTF_8).trim().take(300)
        _current.value = _current.value?.copy(state = if (ok) State.DONE else State.FAILED, status = r.status, message = text)
    }
}
