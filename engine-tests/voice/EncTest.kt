import io.github.ruderigo.firefly.audio.*
import java.io.File
fun raw(p: String): ShortArray { val b = File(p).readBytes(); return ShortArray(b.size / 2) { ((b[2*it].toInt() and 0xff) or (b[2*it+1].toInt() shl 8)).toShort() } }
fun main() {
    var ok = true
    fun check(c: Boolean, w: String) { println((if (c) "  PASS " else "  FAIL ") + w); ok = ok && c }
    val r16 = Recorded(raw("/tmp/kristoff16.raw"), 16000)
    val r8 = Recorded(raw("/home/claude/codec2-1.2.0/raw/kristoff.raw"), 8000)
    val o16 = VoiceEncoder.encode(r16, Opus.MODE_OGG)!!
    val (p16, rate16) = VoiceEncoder.decode(Opus.MODE_OGG, o16)!!
    check(rate16 == 16000 && p16.size == 80000, "16 kHz recording -> Opus -> 5.0 s at 16 kHz (${o16.size} B)")
    val c16 = VoiceEncoder.encode(r16, Codec2.MODE_1200)!!
    check(c16.size == 750 && VoiceEncoder.decode(4, c16)!!.second == 8000, "16 kHz recording -> Codec 2 1200: 750 B, plays at 8 kHz")
    val o8 = VoiceEncoder.encode(r8, Opus.MODE_OGG)!!
    check(VoiceEncoder.decode(Opus.MODE_OGG, o8)!!.first.size == 80000, "8 kHz recording -> Opus still works (narrowband), plays at 16 kHz")
    check(VoiceEncoder.encode(r16, 99) == null && VoiceEncoder.decode(99, o16) == null, "unknown mode: nothing, no crash")
    println(if (ok) "ALL PASSED" else "SOME FAILED")
}
