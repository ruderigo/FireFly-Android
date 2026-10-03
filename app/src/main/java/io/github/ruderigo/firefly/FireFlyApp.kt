package io.github.ruderigo.firefly

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class FireFlyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL_SERVICE, getString(R.string.channel_service),
            NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CHANNEL_MESSAGES, getString(R.string.channel_messages),
            NotificationManager.IMPORTANCE_HIGH))
    }

    companion object {
        const val CHANNEL_SERVICE = "service"
        const val CHANNEL_MESSAGES = "messages"
        @Volatile var inForeground = false
    }
}
