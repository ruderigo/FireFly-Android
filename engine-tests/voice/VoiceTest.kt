import io.github.ruderigo.firefly.audio.Codec2
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

fun readRaw(p: String): ShortArray {
    val b = File(p).readBytes(); val s = ShortArray(b.size / 2)
    ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(s); return s
}
fun wav(p: String, s: ShortArray) {
    val bb = ByteBuffer.allocate(44 + s.size * 2).order(ByteOrder.LITTLE_ENDIAN)
    bb.put("RIFF".toByteArray()).putInt(36 + s.size * 2).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
      .putInt(8000).putInt(16000).putShort(2).putShort(16).put("data".toByteArray()).putInt(s.size * 2)
    s.forEach { bb.putShort(it) }; File(p).writeBytes(bb.array())
}
fun envelope(s: ShortArray, win: Int = 320) = (0 until s.size / win).map { f ->
    sqrt((0 until win).sumOf { val v = s[f * win + it].toDouble(); v * v } / win) }
fun corr(a: List<Double>, b: List<Double>): Double {
    val n = minOf(a.size, b.size); val ma = a.take(n).average(); val mb = b.take(n).average()
    var sab = 0.0; var saa = 0.0; var sbb = 0.0
    for (i in 0 until n) { sab += (a[i]-ma)*(b[i]-mb); saa += (a[i]-ma)*(a[i]-ma); sbb += (b[i]-mb)*(b[i]-mb) }
    return sab / sqrt(saa * sbb)
}
fun main(args: Array<String>) {
    var ok = true
    fun check(c: Boolean, w: String) { println((if (c) "  PASS " else "  FAIL ") + w); ok = ok && c }
    val m = Codec2.MODE_1200
    check(Codec2.samplesPerFrame(m) == 320 && Codec2.bytesPerFrame(m) == 6, "1200 mode: 320 samples (40 ms) -> 6 bytes per frame")
    for (name in args) {
        val pcm = readRaw(name)
        val bits = Codec2.encode(m, pcm)!!
        val out = Codec2.decode(m, bits)!!
        val secs = pcm.size / 8000.0
        println("  %s: %.2f s speech -> %d bytes (%.0f bytes/s), %d ms decoded".format(File(name).name, secs, bits.size, bits.size / secs, Codec2.durationMs(m, bits.size)))
        check(Math.abs(bits.size / secs - 150) < 6, "about 150 bytes per second")
        // A vocoder doesn't preserve waveforms; it preserves the loudness contour and spectrum.
        // Codec 2 has a one-frame algorithmic delay: compare with that offset.
        val c = corr(envelope(pcm).drop(0), envelope(out).drop(1))
        check(c > 0.85, "speech loudness contour preserved (correlation %.2f)".format(c))
        wav("orig_${File(name).nameWithoutExtension}.wav", pcm); wav("c2_1200_${File(name).nameWithoutExtension}.wav", out)
        println("      one LoRa packet (285 B of audio) = %.2f s of speech".format(285 / 6 * 0.040))
    }
    val fifteen = Codec2.encode(m, ShortArray(8000 * 15))!!
    check(fifteen.size == 2250, "15 s voice note = ${fifteen.size} bytes")
    check(Codec2.decode(m, ByteArray(7))!!.size == 320, "trailing partial frame ignored on decode")
    check(Codec2.encode(99, ShortArray(10)) == null, "unknown mode refused, no crash")
    check(Codec2.durationMs(m, 2250) == 15000L, "duration of 2250 bytes = 15000 ms")
    println(if (ok) "ALL PASSED" else "SOME FAILED")
}
