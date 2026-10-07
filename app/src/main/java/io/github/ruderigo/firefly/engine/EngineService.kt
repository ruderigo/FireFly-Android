package io.github.ruderigo.firefly.engine

import android.app.Notification
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import io.github.ruderigo.firefly.FireFlyApp
import io.github.ruderigo.firefly.R
import io.github.ruderigo.firefly.ui.MainActivity
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Keeps FireFly on the mesh while the screen is off: a connectedDevice
 * foreground service (the RNode is a connected device), a partial wake lock
 * while a radio is attached (optional), and a Wi-Fi multicast lock so
 * Reticulum's AutoInterface can hear peers on the local network.
 */
class EngineService : LifecycleService() {
    private var wake: PowerManager.WakeLock? = null
    private var multicast: WifiManager.MulticastLock? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_QUIT) {
            Engine.stop()
            stopSelf()
            android.os.Process.killProcess(android.os.Process.myPid())   // Reticulum can't restart in-process
            return START_NOT_STICKY
        }
        ServiceCompat.startForeground(this, NOTIF_ID, notification(getString(R.string.notif_running)),
            if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0)
        Engine.start(this)
        multicast = getSystemService(WifiManager::class.java)?.createMulticastLock("firefly-auto")?.apply {
            setReferenceCounted(false); acquire()
        }
        lifecycleScope.launch { Engine.events.collect { onEvent(it) } }
        lifecycleScope.launch {
            while (true) {          // status for the notification and the wake lock
                Engine.refreshStatus()
                delay(5000)
            }
        }
        lifecycleScope.launch {
            Engine.status.collect { s ->
                val radio = s.optJSONObject("radio")
                val online = radio?.optString("state") == "online"
                holdWakeLock(online && s.optBoolean("keep_awake", true))
                val text = if (online) getString(R.string.notif_radio_online, radio?.optString("board") ?: "")
                           else getString(R.string.notif_running)
                NotificationManagerCompat.from(this@EngineService).let {
                    if (it.areNotificationsEnabled()) it.notify(NOTIF_ID, notification(text))
                }
            }
        }
        return START_STICKY
    }

    private fun holdWakeLock(on: Boolean) {
        if (on && wake == null) {
            wake = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "firefly:radio").apply { setReferenceCounted(false); acquire() }
        } else if (!on) {
            wake?.release(); wake = null
        }
    }

    private fun onEvent(e: org.json.JSONObject) {
        if (FireFlyApp.inForeground) return
        val open = Intent(this, MainActivity::class.java)
        val (title, text, tag) = when (e.optString("type")) {
            "message" -> {
                open.putExtra(MainActivity.EXTRA_OPEN, "chat").putExtra(MainActivity.EXTRA_PEER, e.optString("peer"))
                Triple(e.optString("name").ifBlank { e.optString("peer").take(8) },
                    if (e.optBoolean("audio")) "▶ " + getString(R.string.voice_note) else e.optString("text"),
                    notificationTag(e.optString("peer")))
            }
            "stump_dm" -> {
                open.putExtra(MainActivity.EXTRA_OPEN, "dm").putExtra(MainActivity.EXTRA_NODE, e.optString("node"))
                    .putExtra(MainActivity.EXTRA_NICK, e.optString("nick"))
                Triple("${e.optString("nick")} · ${e.optString("node_name")}", e.optString("text"),
                    notificationTag(e.optString("node"), e.optString("nick")))
            }
            else -> return
        }
        val n = NotificationCompat.Builder(this, FireFlyApp.CHANNEL_MESSAGES)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(title).setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            // One PendingIntent per conversation (request code = tag), so each
            // notification opens its own chat instead of the last one's.
            .setContentIntent(PendingIntent.getActivity(this, tag, open,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setAutoCancel(true).build()
        val nm = NotificationManagerCompat.from(this)
        if (nm.areNotificationsEnabled()) try { nm.notify(tag, n) } catch (_: SecurityException) {}
    }

    private fun openApp() = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, FireFlyApp.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true).setSilent(true)
            .setContentIntent(openApp())
            .build()

    override fun onDestroy() {
        holdWakeLock(false)
        multicast?.release()
        super.onDestroy()
    }

    companion object {
        private const val NOTIF_ID = 1
        const val ACTION_QUIT = "io.github.ruderigo.firefly.QUIT"
        fun start(c: Context) = androidx.core.content.ContextCompat.startForegroundService(c, Intent(c, EngineService::class.java))
        /** Same id when posting and when the conversation is opened (to clear it). */
        fun notificationTag(vararg parts: String) = parts.joinToString("/").hashCode().let { if (it == NOTIF_ID) it + 1 else it }
        fun quit(c: Context) = c.startService(Intent(c, EngineService::class.java).setAction(ACTION_QUIT))
    }
}
