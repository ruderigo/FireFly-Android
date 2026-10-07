package io.github.ruderigo.firefly.engine

import android.content.Context
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import io.github.ruderigo.firefly.net.FileTransfers
import io.github.ruderigo.firefly.net.WifiHttp
import io.github.ruderigo.firefly.radio.RadioLinks
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** Python calls this from its own threads. */
interface EngineListener { fun onEvent(json: String) }

/**
 * The bridge to the Python engine (app/src/main/python/firefly/api.py).
 *
 * Python is started on a dedicated, never-ending thread, and the engine is
 * started on that same thread. That thread is then Python's "main thread",
 * the only one where Reticulum and LXMF may install their signal handlers.
 * (The prototype patched signal.signal() instead.) Later calls come from any
 * thread; Chaquopy handles the GIL.
 */
object Engine {
    private const val TAG = "FireFly/Engine"
    private val thread = HandlerThread("firefly-python").apply { start() }
    private val handler = Handler(thread.looper)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var api: PyObject? = null

    sealed interface State {
        data object Stopped : State
        data object Starting : State
        data class Running(val address: String) : State
        data class Failed(val error: String) : State
    }

    private val _state = MutableStateFlow<State>(State.Stopped)
    val state: StateFlow<State> = _state
    /** Bumped whenever the engine's store changes: screens re-read. */
    private val _revision = MutableStateFlow(0L)
    val revision: StateFlow<Long> = _revision
    private val _status = MutableStateFlow(JSONObject())
    val status: StateFlow<JSONObject> = _status
    private val _events = MutableSharedFlow<JSONObject>(extraBufferCapacity = 64)
    val events: SharedFlow<JSONObject> = _events

    lateinit var links: RadioLinks; private set

    fun start(context: Context) {
        if (_state.value is State.Running || _state.value is State.Starting) return
        _state.value = State.Starting
        val app = context.applicationContext
        links = RadioLinks(app) { searchRadio() }
        val http = WifiHttp(app)
        FileTransfers.http = http
        val listener = object : EngineListener {
            override fun onEvent(json: String) {
                val e = try { JSONObject(json) } catch (_: Exception) { return }
                when (e.optString("type")) {
                    "changed" -> _revision.value = e.optLong("version")
                    "status" -> refreshStatus()
                    else -> _events.tryEmit(e)
                }
            }
        }
        handler.post {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(app))
                val module = Python.getInstance().getModule("firefly.api")
                val address = module.callAttr("start", app.filesDir.absolutePath + "/firefly", links, http, listener)
                api = module
                links.register()
                _state.value = State.Running(address.toString())
                refreshStatus()
            } catch (t: Throwable) {
                Log.e(TAG, "engine failed to start", t)
                _state.value = State.Failed(t.message ?: t.toString())
            }
        }
    }

    fun stop() {
        val a = api ?: return
        try { a.callAttr("stop") } catch (_: Throwable) {}
        links.unregister()
    }

    val running get() = api != null && _state.value is State.Running

    /** Call an api.py function; returns its result as a String ("" on error). */
    fun call(fn: String, vararg args: Any?): String {
        val a = api ?: return ""
        return try { a.callAttr(fn, *args)?.toString() ?: "" } catch (t: Throwable) {
            Log.w(TAG, "$fn failed: ${t.message}")
            ""
        }
    }

    /** True when the call completed, whatever it returned (None included). */
    fun callOk(fn: String, vararg args: Any?): Boolean {
        val a = api ?: return false
        return try { a.callAttr(fn, *args); true } catch (t: Throwable) { Log.w(TAG, "$fn failed: ${t.message}"); false }
    }

    fun callBytes(fn: String, vararg args: Any?): ByteArray? = try {
        api?.callAttr(fn, *args)?.toJava(ByteArray::class.java)
    } catch (t: Throwable) { Log.w(TAG, "$fn failed: ${t.message}"); null }

    suspend fun obj(fn: String, vararg args: Any?): JSONObject = withContext(Dispatchers.IO) {
        call(fn, *args).let { if (it.startsWith("{")) JSONObject(it) else JSONObject() }
    }

    suspend fun arr(fn: String, vararg args: Any?): JSONArray = withContext(Dispatchers.IO) {
        call(fn, *args).let { if (it.startsWith("[")) JSONArray(it) else JSONArray() }
    }

    fun fire(fn: String, vararg args: Any?) { scope.launch { call(fn, *args) } }

    fun refreshStatus() {
        scope.launch {
            val s = call("status")
            if (s.startsWith("{")) _status.value = JSONObject(s)
        }
    }

    fun searchRadio() = fire("radio_search_now")
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).map { getString(it) }
