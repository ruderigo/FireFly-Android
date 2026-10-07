import io.github.ruderigo.firefly.audio.Opus
import java.io.File
import kotlin.math.sqrt
fun raw(p: String): ShortArray { val b = File(p).readBytes(); return ShortArray(b.size / 2) { ((b[2*it].toInt() and 0xff) or (b[2*it+1].toInt() shl 8)).toShort() } }
fun env(s: ShortArray, w: Int) = (0 until s.size / w).map { f -> sqrt((0 until w).sumOf { val v = s[f*w+it].toDouble(); v*v } / w) }
fun corr(a: List<Double>, b: List<Double>): Double { val n = minOf(a.size, b.size); val ma = a.take(n).average(); val mb = b.take(n).average()
    var ab = 0.0; var aa = 0.0; var bb = 0.0; for (i in 0 until n) { ab += (a[i]-ma)*(b[i]-mb); aa += (a[i]-ma)*(a[i]-ma); bb += (b[i]-mb)*(b[i]-mb) }; return ab / sqrt(aa*bb) }
fun main() {
    var ok = true
    fun check(c: Boolean, w: String) { println((if (c) "  PASS " else "  FAIL ") + w); ok = ok && c }
    for ((name, path, rate) in listOf(Triple("kristoff 8 kHz", "/home/claude/codec2-1.2.0/raw/kristoff.raw", 8000),
                                      Triple("kristoff 16 kHz", "/tmp/kristoff16.raw", 16000))) {
        val pcm = raw(path)
        val t = System.nanoTime(); val ogg = Opus.encodeOgg(pcm, rate, 6000)!!; val ms = (System.nanoTime() - t) / 1e6
        File("/tmp/ff_${rate}.opus").writeBytes(ogg)
        val out = Opus.decodeOgg(ogg, rate)!!
        val c = corr(env(pcm, rate / 50), env(out, rate / 50))
        println("      $name: 5.0 s -> ${ogg.size} B (15 s note ~${ogg.size * 3} B), encoded in %.0f ms".format(ms))
        check(out.size == pcm.size, "$name: decodes back to exactly ${pcm.size} samples (${out.size})")
        check(c > 0.9, "$name: speech contour preserved (%.2f)".format(c))
    }
    for ((name, path) in listOf("opusenc 6 kbps mono" to "/tmp/ref_wia.opus", "opusenc 12 kbps" to "/tmp/ref_wia12.opus",
                                "opusenc 16 kbps stereo" to "/tmp/ref_wia_stereo.opus")) {
        val out = Opus.decodeOgg(File(path).readBytes(), 16000)
        check(out != null && out.size == 16000, "reference file ($name) decodes: ${out?.size} samples = 1.0 s")
    }
    check(Opus.decodeOgg(ByteArray(500) { it.toByte() }, 16000) == null, "garbage refused, no crash")
    val ours = File("/tmp/ff_16000.opus").readBytes()
    val cut = Opus.decodeOgg(ours.copyOf(ours.size / 2), 16000)
    check(cut != null && cut.size in 1 until 80000, "a file cut in half plays what it has (${cut?.size} samples), no crash")
    println(if (ok) "ALL PASSED" else "SOME FAILED")
}
