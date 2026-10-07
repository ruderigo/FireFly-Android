import io.github.ruderigo.firefly.audio.Resample
import kotlin.math.*
fun tone(rate: Int, secs: Double, vararg f: Pair<Double, Double>) =
    ShortArray((rate * secs).toInt()) { n -> f.sumOf { (hz, a) -> a * sin(2 * PI * hz * n / rate) }.roundToInt().toShort() }
// amplitude of a frequency in an 8 kHz signal (single-bin DFT over the middle second)
fun amp(x: ShortArray, hz: Double): Double {
    var re = 0.0; var im = 0.0; val start = 4000; val n = 8000
    for (i in 0 until n) { val v = x[start + i].toDouble(); re += v * cos(2*PI*hz*i/8000); im += v * sin(2*PI*hz*i/8000) }
    return 2 * sqrt(re*re + im*im) / n
}
fun main() {
    var ok = true
    fun check(c: Boolean, w: String) { println((if (c) "  PASS " else "  FAIL ") + w); ok = ok && c }
    for (rate in listOf(8000, 16000, 48000)) {
        // 1 kHz voice-band tone + 6 kHz tone that would alias to 2 kHz if not filtered
        val x = tone(rate, 2.0, 1000.0 to 4000.0, 6000.0 to 4000.0)
        val y = Resample.toCodecRate(x, rate)
        check(y.size == 16000, "$rate Hz: 2 s -> ${y.size} samples at 8 kHz")
        if (rate > 8000) {
            val keep = amp(y, 1000.0); val alias = amp(y, 2000.0)
            check(alias < keep / 100, "$rate Hz: 6 kHz alias suppressed %.0f dB below the voice tone".format(20 * log10(keep / alias)))
        }
        check(y.maxOf { abs(it.toInt()) } <= 32767 * 0.71, "$rate Hz: no clipping")
    }
    val quiet = Resample.toCodecRate(tone(48000, 2.0, 500.0 to 800.0), 48000)
    check(amp(quiet, 500.0) in 2900.0..3300.0, "quiet voice raised x4 (to %.0f)".format(amp(quiet, 500.0)))
    val dc = Resample.toCodecRate(ShortArray(16000) { 3000 }, 8000)
    check(abs(dc.takeLast(4000).average()) < 50, "DC offset removed")
    println(if (ok) "ALL PASSED" else "SOME FAILED")
}
