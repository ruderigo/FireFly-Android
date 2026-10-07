package io.github.ruderigo.firefly.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log

/**
 * Records one voice note from the microphone, at most [MAX_MS].
 * Tries 16 kHz first (wideband, for Opus; Codec 2 gets it halved), then 48
 * and 8 kHz. [VoiceEncoder] converts to whatever the chosen codec needs.
 * Uses the VOICE_COMMUNICATION source: the phone's own echo cancelling,
 * noise suppression and gain control, tuned for speech.
 */
class VoiceRecorder {
    private var rec: AudioRecord? = null
    private var worker: Thread? = null
    private var rate = 8000
    private var buffer = ShortArray(0)
    @Volatile private var count = 0
    @Volatile private var running = false

    val elapsedMs: Long get() = count * 1000L / rate
    val full: Boolean get() = elapsedMs >= MAX_MS

    @SuppressLint("MissingPermission")    // the caller checks RECORD_AUDIO first
    fun start(): Boolean {
        for (r in intArrayOf(16000, 48000, 8000)) {
            val min = AudioRecord.getMinBufferSize(r, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (min <= 0) continue
            val candidate = try {
                AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, r, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, maxOf(min, r / 5 * 2))
            } catch (e: Exception) { null } ?: continue
            if (candidate.state != AudioRecord.STATE_INITIALIZED) { candidate.release(); continue }
            rate = r
            rec = candidate
            break
        }
        val r = rec ?: return false
        buffer = ShortArray(rate * (MAX_MS / 1000).toInt())
        count = 0
        return try {
            r.startRecording()
            running = true
            worker = Thread({
                val chunk = ShortArray(rate / 20)
                while (running && count < buffer.size) {
                    val n = r.read(chunk, 0, minOf(chunk.size, buffer.size - count))
                    if (n <= 0) break
                    System.arraycopy(chunk, 0, buffer, count, n)
                    count += n
                }
            }, "voice-recorder").apply { start() }
            true
        } catch (e: Exception) {
            Log.w("FireFly/Voice", "could not record: ${e.message}")
            release(); false
        }
    }

    /** Stops and returns the note, or null if nothing usable was recorded. */
    fun stop(): Recorded? {
        release()
        if (count < rate * MIN_MS / 1000) return null
        return Recorded(buffer.copyOf(count), rate)
    }

    fun cancel() { release() }

    private fun release() {
        running = false
        worker?.join(500)
        worker = null
        rec?.let { try { it.stop() } catch (_: Exception) {}; it.release() }
        rec = null
    }

    companion object {
        const val MAX_MS = 15_000L     // ~2.2 KB at 1200 bps: about 8 LoRa packets
        const val MIN_MS = 600L
    }
}
