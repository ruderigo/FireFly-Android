package io.github.ruderigo.firefly.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Plays one voice note at a time; [playing] is the message id being played. */
object VoicePlayer {
    private val _playing = MutableStateFlow<Long?>(null)
    val playing: StateFlow<Long?> = _playing
    private var track: AudioTrack? = null

    @Synchronized
    fun play(id: Long, pcm: ShortArray, rate: Int = Codec2.SAMPLE_RATE) {
        stop()
        if (pcm.isEmpty()) return
        val t = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder()
                .setSampleRate(rate)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(pcm.size * 2)
            .build()
        t.write(pcm, 0, pcm.size)
        t.notificationMarkerPosition = pcm.size
        t.setPlaybackPositionUpdateListener(object : AudioTrack.OnPlaybackPositionUpdateListener {
            override fun onMarkerReached(track: AudioTrack) { if (this@VoicePlayer.track === track) stop() }
            override fun onPeriodicNotification(track: AudioTrack) {}
        }, Handler(Looper.getMainLooper()))
        t.play()
        track = t
        _playing.value = id
    }

    @Synchronized
    fun stop() {
        track?.let { try { it.stop() } catch (_: Exception) {}; it.release() }
        track = null
        _playing.value = null
    }
}
