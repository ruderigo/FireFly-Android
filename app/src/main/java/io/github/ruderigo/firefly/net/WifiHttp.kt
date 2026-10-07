package io.github.ruderigo.firefly.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import javax.net.SocketFactory
import java.net.HttpURLConnection
import java.net.URL

/** What Python's WifiPipe reads back: fields, not getters. */
class HttpResult(@JvmField val status: Int, @JvmField val body: ByteArray)

/**
 * HTTP for the Stump Wi-Fi pipe, bound to the Wi-Fi network.
 *
 * A Stump hotspot has no internet, so Android may keep routing traffic over
 * mobile data and 192.168.4.1 becomes unreachable. Opening the connection
 * through the Wi-Fi [Network] itself avoids that without binding the whole
 * process (which would also move Reticulum's TCP peers off mobile data).
 */
class WifiHttp(context: Context) {
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    @Volatile private var wifi: Network? = null

    init {
        val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
        cm.registerNetworkCallback(req, object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) { wifi = n }
            override fun onLost(n: Network) { if (wifi == n) wifi = null }
        })
    }

    fun request(method: String, url: String, headersJson: String?, body: ByteArray?, timeoutMs: Int): HttpResult {
        return try {
            val u = URL(url)
            val conn = (wifi?.openConnection(u) ?: u.openConnection()) as HttpURLConnection
            conn.requestMethod = method
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.instanceFollowRedirects = false
            headersJson?.takeIf { it.isNotBlank() }?.let { h ->
                val o = JSONObject(h)
                o.keys().forEach { k -> if (k != "Content-Length") conn.setRequestProperty(k, o.getString(k)) }
            }
            if (body != null) {
                conn.doOutput = true
                conn.setFixedLengthStreamingMode(body.size)   // the node requires Content-Length
                conn.outputStream.use { it.write(body) }
            }
            val code = conn.responseCode
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            conn.disconnect()
            HttpResult(code, bytes)
        } catch (e: Exception) {
            HttpResult(-1, (e.message ?: e.javaClass.simpleName).toByteArray())
        }
    }
    private fun open(url: String): HttpURLConnection {
        val u = URL(url)
        return (wifi?.openConnection(u) ?: u.openConnection()) as HttpURLConnection
    }

    /**
     * POST [input] as a raw body with a fixed Content-Length to the node's
     * /upload. [onProgress] gets the bytes sent so far. Returns the node's
     * plain-text answer ("Uploaded. Your balance: 4", or why not).
     *
     * The node reads X-Filename as UTF-8 and doesn't URL-decode it, but
     * Android's HttpURLConnection refuses non-ASCII header values. So over
     * plain HTTP (the node's hotspot, 192.168.4.1) the request is written by
     * hand on a socket of the Wi-Fi network, the filename as real UTF-8 bytes.
     * Over HTTPS only an ASCII-safe name can be sent.
     */
    fun upload(url: String, filename: String, slotHash: String?, length: Long, input: InputStream,
               onProgress: (Long) -> Unit): HttpResult {
        val name = filename.map { if (it.code < 0x20 || it.code == 0x7f) ' ' else it }.joinToString("").trim()
        return try {
            val u = URL(url)
            if (u.protocol.equals("http", ignoreCase = true)) rawUpload(u, name, slotHash, length, input, onProgress)
            else connectionUpload(url, asciiName(name), slotHash, length, input, onProgress)
        } catch (e: Exception) {
            HttpResult(-1, (e.message ?: e.javaClass.simpleName).toByteArray())
        }
    }

    private fun pump(input: InputStream, out: OutputStream, length: Long, onProgress: (Long) -> Unit) {
        val buf = ByteArray(16 * 1024)
        var sent = 0L
        var lastReport = 0L
        while (sent < length) {
            val n = input.read(buf, 0, minOf(buf.size.toLong(), length - sent).toInt())
            if (n < 0) break
            out.write(buf, 0, n)
            sent += n
            if (sent - lastReport >= 64 * 1024 || sent == length) { onProgress(sent); lastReport = sent }
        }
        out.flush()
        if (sent < length) throw IOException("the file is shorter than its size")
    }

    private fun rawUpload(u: URL, name: String, slotHash: String?, length: Long, input: InputStream,
                          onProgress: (Long) -> Unit): HttpResult {
        val port = if (u.port > 0) u.port else 80
        val socket = (wifi?.socketFactory ?: SocketFactory.getDefault()).createSocket()
        return socket.use { s ->
            s.connect(InetSocketAddress(u.host, port), 8000)
            s.soTimeout = 120_000               // the node may evict old files before answering
            val head = buildString {
                append("POST ").append(u.file.ifEmpty { "/upload" }).append(" HTTP/1.1\r\n")
                append("Host: ").append(u.host).append(if (u.port > 0) ":${u.port}" else "").append("\r\n")
                append("Content-Type: application/octet-stream\r\n")
                append("X-Filename: ").append(name).append("\r\n")
                if (slotHash != null) append("X-Hash: ").append(slotHash).append("\r\n")
                append("Content-Length: ").append(length).append("\r\n")
                append("Connection: close\r\n\r\n")
            }
            val out = BufferedOutputStream(s.getOutputStream(), 16 * 1024)
            var sendError: IOException? = null
            try {
                out.write(head.toByteArray(Charsets.UTF_8))
                pump(input, out, length, onProgress)
            } catch (e: IOException) {
                sendError = e               // the node may have answered (and closed) early: read it
            }
            val answer = try { readResponse(s.getInputStream()) } catch (e: IOException) { null }
            answer ?: HttpResult(-1, (sendError?.message ?: "the node closed the connection without answering").toByteArray())
        }
    }

    /** Status line, headers, then the body by Content-Length or to the end. Null if nothing came back. */
    private fun readResponse(input: InputStream): HttpResult? {
        val bytes = BufferedInputStream(input)
        fun line(): String? {
            val b = ByteArrayOutputStream()
            while (true) {
                val c = bytes.read()
                if (c < 0) return if (b.size() == 0) null else b.toString("UTF-8")
                if (c == '\n'.code) return b.toString("UTF-8").trimEnd('\r')
                b.write(c)
            }
        }
        val status = line()?.split(' ')?.getOrNull(1)?.toIntOrNull() ?: return null
        var contentLength = -1
        while (true) {
            val h = line() ?: break
            if (h.isEmpty()) break
            val (k, v) = h.split(':', limit = 2).let { it[0].trim().lowercase() to it.getOrElse(1) { "" }.trim() }
            if (k == "content-length") contentLength = v.toIntOrNull() ?: -1
        }
        val body = ByteArrayOutputStream()
        val buf = ByteArray(4096)
        while (contentLength < 0 || body.size() < contentLength) {
            val want = if (contentLength < 0) buf.size else minOf(buf.size, contentLength - body.size())
            val n = bytes.read(buf, 0, want)
            if (n < 0) break
            body.write(buf, 0, n)
            if (body.size() > 64 * 1024) break
        }
        return HttpResult(status, body.toByteArray())
    }

    private fun connectionUpload(url: String, name: String, slotHash: String?, length: Long, input: InputStream,
                                 onProgress: (Long) -> Unit): HttpResult {
        val conn = open(url)
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 8000
            conn.readTimeout = 120_000
            conn.instanceFollowRedirects = false
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/octet-stream")
            conn.setRequestProperty("X-Filename", name)
            if (slotHash != null) conn.setRequestProperty("X-Hash", slotHash)
            conn.setFixedLengthStreamingMode(length)
            conn.outputStream.use { pump(input, it, length, onProgress) }
            val code = conn.responseCode
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            return HttpResult(code, stream?.use { it.readBytes() } ?: ByteArray(0))
        } finally {
            conn.disconnect()
        }
    }

    /**
     * GET [url] into [out]. [onLength] gets Content-Length once known,
     * [onProgress] the bytes received. On anything but 200 nothing is written
     * and the body (the node's message) is returned instead.
     */
    fun download(url: String, out: OutputStream, onLength: (Long) -> Unit, onProgress: (Long) -> Unit): HttpResult {
        val conn = open(url)
        return try {
            conn.connectTimeout = 8000
            conn.readTimeout = 30_000
            conn.instanceFollowRedirects = false
            val code = conn.responseCode
            if (code != 200) {
                val body = (if (code >= 400) conn.errorStream else conn.inputStream)?.use { it.readBytes() } ?: ByteArray(0)
                return HttpResult(code, body)
            }
            val total = conn.contentLengthLong
            if (total >= 0) onLength(total)
            var got = 0L
            var lastReport = 0L
            conn.inputStream.use { input ->
                val buf = ByteArray(16 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    got += n
                    if (got - lastReport >= 64 * 1024) { onProgress(got); lastReport = got }
                }
            }
            onProgress(got)
            if (total >= 0 && got < total) HttpResult(-1, "connection dropped at $got of $total bytes".toByteArray())
            else HttpResult(200, ByteArray(0))
        } catch (e: Exception) {
            HttpResult(-1, (e.message ?: e.javaClass.simpleName).toByteArray())
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        /** For HTTPS only: what an ASCII header can carry. Accents dropped (é -> e), anything else -> _. */
        fun asciiName(name: String): String =
            java.text.Normalizer.normalize(name, java.text.Normalizer.Form.NFD)
                .filter { it.code !in 0x300..0x36f }
                .map { if (it.code in 0x20..0x7e) it else '_' }.joinToString("")
    }
}
