package io.github.ruderigo.firefly.audio

/** A recording as captured: mono PCM at the rate the phone gave us. */
class Recorded(val pcm: ShortArray, val rate: Int)

/**
 * A recording to the bytes of a voice note in a given LXMF audio mode:
 * Opus (16) at 16 kHz when the recording allows (8 kHz otherwise), or
 * Codec 2 (3-9) at 8 kHz. Null if the mode is unknown or encoding failed.
 */
object VoiceEncoder {
    fun encode(rec: Recorded, mode: Int): ByteArray? = when {
        mode == Opus.MODE_OGG -> {
            val rate = if (rec.rate % Opus.RATE == 0) Opus.RATE else 8000
            Opus.encodeOgg(Resample.toRate(rec.pcm, rec.rate, rate), rate, Opus.BITRATE)
        }
        mode in Codec2.ALL_MODES -> Codec2.encode(mode, Resample.toCodecRate(rec.pcm, rec.rate))
        else -> null
    }

    /** PCM to play and its rate, for a received or sent note. */
    fun decode(mode: Int, bytes: ByteArray): Pair<ShortArray, Int>? = when {
        mode == Opus.MODE_OGG -> Opus.decodeOgg(bytes, Opus.RATE)?.let { it to Opus.RATE }
        mode in Codec2.ALL_MODES -> Codec2.decode(mode, bytes)?.let { it to Codec2.SAMPLE_RATE }
        else -> null
    }
}
