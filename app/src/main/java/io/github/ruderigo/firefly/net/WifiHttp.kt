package io.github.ruderigo.firefly.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import org.json.JSONObject
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
}
