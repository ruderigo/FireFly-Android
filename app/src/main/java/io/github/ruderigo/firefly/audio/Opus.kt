package io.github.ruderigo.firefly.audio

/**
 * Opus voice notes (native, app/src/main/cpp/firefly_opus.c): standard Ogg
 * Opus files, LXMF audio mode AM_OPUS_OGG, as Sideband sends them. Natural
 * voices at 8 kbps, the default on every link; about six times the size of
 * Codec 2 1200, so Automatic and the Codec 2 modes remain for saving airtime.
 */
object Opus {
    const val MODE_OGG = 16              // LXMF.AM_OPUS_OGG
    const val RATE = 16000               // wideband speech
    const val BITRATE = 8000             // a 15 s note ~13.4 KB: inside a Stump's 15 KB propagation limit

    init { System.loadLibrary("firefly_opus") }

    /** Mono PCM at 8 or 16 kHz to a complete Ogg Opus file. */
    @JvmStatic external fun encodeOgg(pcm: ShortArray, rate: Int, bitrate: Int): ByteArray?
    /** An Ogg Opus file (mono or stereo) to mono PCM at [rate]. */
    @JvmStatic external fun decodeOgg(ogg: ByteArray, rate: Int): ShortArray?
}
