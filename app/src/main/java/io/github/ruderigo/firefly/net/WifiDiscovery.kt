package io.github.ruderigo.firefly.net

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject

/**
 * Stump hotspots around the phone: a Wi-Fi scan (with signal strength), and
 * joining one for FireFly alone.
 *
 * Joining uses WifiNetworkSpecifier (Android 10+): Android asks the user once
 * per hotspot, connects it for this app only, and gives us its [Network], which
 * [WifiHttp] then uses for Stump traffic. It's made for local devices without
 * internet, which is what a Stump is. Some phones can keep their usual Wi-Fi
 * at the same time; others drop it while FireFly holds the Stump's. Leaving
 * gives it back.
 */
object WifiDiscovery {
    enum class Join { IDLE, ASKING, JOINED, FAILED }

    data class Joined(val ssid: String, val state: Join, val network: Network? = null)

    private val _joined = MutableStateFlow(Joined("", Join.IDLE))
    val joined: StateFlow<Joined> = _joined
    private var callback: ConnectivityManager.NetworkCallback? = null

    /** The permission a scan needs: "Nearby devices" on Android 13+, location before. */
    fun scanPermission(): String =
        if (Build.VERSION.SDK_INT >= 33) Manifest.permission.NEARBY_WIFI_DEVICES else Manifest.permission.ACCESS_FINE_LOCATION

    fun canScan(ctx: Context) =
        ContextCompat.checkSelfPermission(ctx, scanPermission()) == PackageManager.PERMISSION_GRANTED

    /**
     * Scan, wait for fresh results (up to 10 s), and return them as JSON:
     * [{"ssid", "bssid", "rssi", "freq", "secured"}]. Android allows an app about
     * four scans in two minutes; past that, the last results come back.
     */
    @Suppress("DEPRECATION")      // startScan/SSID: still the only way for an ordinary app
    suspend fun scan(ctx: Context): String {
        val wm = ctx.applicationContext.getSystemService(WifiManager::class.java) ?: return "[]"
        if (!canScan(ctx)) return "[]"
        val done = CompletableDeferred<Unit>()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) { done.complete(Unit) }
        }
        ContextCompat.registerReceiver(ctx, receiver, IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION),
            ContextCompat.RECEIVER_NOT_EXPORTED)
        try {
            if (runCatching { wm.startScan() }.getOrDefault(false)) withTimeoutOrNull(10_000) { done.await() }
        } finally {
            runCatching { ctx.unregisterReceiver(receiver) }
        }
        val out = JSONArray()
        val results = try { wm.scanResults } catch (_: SecurityException) { emptyList() }
        for (r in results) {
            val ssid = r.SSID?.removeSurrounding("\"")?.takeIf { it.isNotBlank() && it != "<unknown ssid>" } ?: continue
            val caps = r.capabilities ?: ""
            out.put(JSONObject().put("ssid", ssid).put("bssid", r.BSSID).put("rssi", r.level).put("freq", r.frequency)
                .put("secured", listOf("WPA", "WEP", "SAE", "PSK", "EAP").any { caps.contains(it) }))
        }
        return out.toString()
    }

    /** Join a hotspot for FireFly only. Android shows its own prompt; the result arrives in [joined]. */
    fun join(ctx: Context, ssid: String, password: String?): Boolean =
        if (Build.VERSION.SDK_INT >= 29) join29(ctx, ssid, password)
        else false                                          // Android 8-9: join it in the system's Wi-Fi settings

    @RequiresApi(29)
    private fun join29(ctx: Context, ssid: String, password: String?): Boolean {
        leave(ctx)
        val spec = WifiNetworkSpecifier.Builder().setSsid(ssid).apply {
            if (!password.isNullOrEmpty()) setWpa2Passphrase(password)
        }.build()
        val req = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)      // a Stump has none
            .setNetworkSpecifier(spec)
            .build()
        val cm = ctx.applicationContext.getSystemService(ConnectivityManager::class.java)
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) {
                WifiHttp.instance?.useJoined(n)
                _joined.value = Joined(ssid, Join.JOINED, n)
            }
            override fun onUnavailable() { _joined.value = Joined(ssid, Join.FAILED) }
            override fun onLost(n: Network) {
                WifiHttp.instance?.useJoined(null)
                _joined.value = Joined(ssid, Join.IDLE)
            }
        }
        callback = cb
        _joined.value = Joined(ssid, Join.ASKING)
        return runCatching { cm.requestNetwork(req, cb); true }.getOrElse {
            _joined.value = Joined(ssid, Join.FAILED); false
        }
    }

    /** Give the hotspot back: Android returns to the phone's usual Wi-Fi. */
    fun leave(ctx: Context) {
        val cb = callback ?: return
        callback = null
        val cm = ctx.applicationContext.getSystemService(ConnectivityManager::class.java)
        runCatching { cm.unregisterNetworkCallback(cb) }
        WifiHttp.instance?.useJoined(null)
        _joined.value = Joined("", Join.IDLE)
    }
}
