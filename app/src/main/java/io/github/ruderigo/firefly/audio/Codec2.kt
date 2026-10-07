package io.github.ruderigo.firefly.audio

/**
 * Codec 2 speech codec (native, app/src/main/cpp). Modes are LXMF's
 * audio-mode numbers, the same number sent in the message's audio field,
 * so voice notes play in Sideband and any other LXMF client that reads it.
 * Audio is 8 kHz mono 16-bit PCM.
 */
object Codec2 {
    /** LXMF.AM_CODEC2_1200: ~2 s of speech per LoRa packet. The default. */
    const val MODE_1200 = 4

    /**
     * The modes offered for sending, LXMF audio-mode number to bit rate.
     * Any Codec 2 mode received plays, whichever is chosen here.
     */
    val SEND_MODES = linkedMapOf(3 to 700, 4 to 1200, 7 to 1600, 8 to 2400, 9 to 3200)
    /** Every Codec 2 mode that plays (1.2 dropped the 450 modes). */
    val ALL_MODES = setOf(3, 4, 5, 6, 7, 8, 9)

    /** Seconds of speech that fit one LoRa packet (~285 bytes of audio), and a 15 s note's size. */
    fun perPacketSeconds(mode: Int): Double = 285.0 * 8 / (SEND_MODES[mode] ?: 1200)
    fun fifteenSecondBytes(mode: Int): Int = (SEND_MODES[mode] ?: 1200) * 15 / 8
    const val SAMPLE_RATE = 8000

    init { System.loadLibrary("firefly_codec2") }

    @JvmStatic external fun samplesPerFrame(mode: Int): Int
    @JvmStatic external fun bytesPerFrame(mode: Int): Int
    @JvmStatic external fun encode(mode: Int, pcm: ShortArray): ByteArray?
    @JvmStatic external fun decode(mode: Int, bits: ByteArray): ShortArray?

    /** Length of encoded audio in milliseconds, or 0 for an unknown mode. */
    fun durationMs(mode: Int, bytes: Int): Long {
        val bpf = bytesPerFrame(mode); val spf = samplesPerFrame(mode)
        if (bpf <= 0 || spf <= 0) return 0
        return (bytes / bpf).toLong() * spf * 1000 / SAMPLE_RATE
    }
}
