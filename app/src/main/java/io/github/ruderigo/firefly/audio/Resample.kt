package io.github.ruderigo.firefly.audio

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Microphone audio to what the codecs want: mono at 8 kHz (Codec 2) or 16 kHz
 * (Opus), no DC offset, a sensible level. Higher rates are low-pass filtered
 * (windowed sinc, cut-off at 45 % of the output rate) and decimated; without
 * the filter, everything above half the output rate would fold back into the
 * voice band as noise. Pure Kotlin: tested on the desktop with test tones.
 */
object Resample {
    /** To Codec 2's 8 kHz. */
    fun toCodecRate(input: ShortArray, inRate: Int): ShortArray = toRate(input, inRate, 8000)

    /** To [outRate] (8 or 16 kHz), from a whole multiple of it. */
    fun toRate(input: ShortArray, inRate: Int, outRate: Int): ShortArray {
        require(inRate % outRate == 0) { "can't go from $inRate to $outRate Hz" }
        val factor = inRate / outRate
        val x = DoubleArray(input.size) { input[it].toDouble() }
        val down = if (factor == 1) x else decimate(x, factor, inRate, cutoff = 0.45 * outRate)
        return level(removeDc(down, outRate))
    }

    private fun decimate(x: DoubleArray, factor: Int, rate: Int, cutoff: Double): DoubleArray {
        val taps = 32 * factor + 1
        val fc = cutoff / rate
        val mid = taps / 2
        val h = DoubleArray(taps) { i ->
            val n = i - mid
            val sinc = if (n == 0) 2 * fc else sin(2 * PI * fc * n) / (PI * n)
            sinc * (0.54 - 0.46 * cos(2 * PI * i / (taps - 1)))          // Hamming window
        }
        val sum = h.sum(); for (i in h.indices) h[i] /= sum               // unity gain at DC
        val out = DoubleArray(x.size / factor)
        for (o in out.indices) {
            val c = o * factor
            var acc = 0.0
            for (k in 0 until taps) {
                val j = c + k - mid
                if (j >= 0 && j < x.size) acc += h[k] * x[j]
            }
            out[o] = acc
        }
        return out
    }

    /** One-pole high-pass at ~60 Hz: removes the DC offset some microphones add. */
    private fun removeDc(x: DoubleArray, rate: Int): DoubleArray {
        val r = 1.0 - 2 * PI * 60.0 / rate
        var prevX = 0.0; var prevY = 0.0
        return DoubleArray(x.size) { i -> val y = x[i] - prevX + r * prevY; prevX = x[i]; prevY = y; y }
    }

    /** Bring quiet recordings up (at most x4) so the codec gets a usable level; never clip. */
    private fun level(x: DoubleArray): ShortArray {
        val peak = x.maxOfOrNull { abs(it) } ?: 0.0
        val gain = if (peak < 1.0) 1.0 else minOf(4.0, 0.7 * 32767 / peak)
        return ShortArray(x.size) { (x[it] * gain).roundToInt().coerceIn(-32768, 32767).toShort() }
    }
}
