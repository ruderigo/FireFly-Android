import io.github.ruderigo.firefly.audio.Codec2
import java.io.File
import kotlin.math.sqrt
fun env(s: ShortArray, w: Int = 320) = (0 until s.size / w).map { f -> sqrt((0 until w).sumOf { val v = s[f*w+it].toDouble(); v*v } / w) }
fun corr(a: List<Double>, b: List<Double>): Double { val n = minOf(a.size, b.size); val ma = a.take(n).average(); val mb = b.take(n).average()
    var ab = 0.0; var aa = 0.0; var bb = 0.0; for (i in 0 until n) { ab += (a[i]-ma)*(b[i]-mb); aa += (a[i]-ma)*(a[i]-ma); bb += (b[i]-mb)*(b[i]-mb) }; return ab / sqrt(aa*bb) }
fun main() {
    val raw = File("/home/claude/codec2-1.2.0/raw/kristoff.raw").readBytes()
    val pcm = ShortArray(raw.size / 2) { ((raw[2*it].toInt() and 0xff) or (raw[2*it+1].toInt() shl 8)).toShort() }
    var ok = true
    for ((m, name) in listOf(3 to "700C", 4 to "1200", 7 to "1600", 8 to "2400", 9 to "3200")) {
        val bits = Codec2.encode(m, pcm)!!; val out = Codec2.decode(m, bits)!!
        val c = corr(env(pcm), env(out).drop(1))
        val bps = bits.size / 5.0; val perPacket = 285 / Codec2.bytesPerFrame(m) * Codec2.samplesPerFrame(m) / 8000.0
        val good = out.size == pcm.size && c > 0.8 && Codec2.durationMs(m, bits.size) == 5000L
        ok = ok && good
        println("  %s %-5s %4.0f B/s  one packet %.2f s  15 s note %5d B  contour %.2f  decoded %d samples".format(
            if (good) "PASS" else "FAIL", name, bps, perPacket, (bps * 15).toInt(), c, out.size))
    }
    println(if (ok) "ALL PASSED" else "SOME FAILED")
}
