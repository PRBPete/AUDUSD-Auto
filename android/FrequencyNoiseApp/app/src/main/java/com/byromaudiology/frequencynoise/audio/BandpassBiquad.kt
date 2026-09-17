package com.byromaudiology.frequencynoise.audio

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * A single RBJ ("Audio EQ Cookbook") constant-0dB-peak-gain bandpass biquad.
 *
 * [retune] only updates the *target* coefficients; [process] nudges the live
 * coefficients toward that target by a small step every sample. That
 * per-sample interpolation is what lets the center frequency slide while
 * noise is playing without producing the clicks/pops a hard coefficient
 * swap would cause.
 */
class BandpassBiquad(private val sampleRateHz: Double) {

    private var b0 = 0.0
    private var b1 = 0.0
    private var b2 = 0.0
    private var a1 = 0.0
    private var a2 = 0.0

    private var targetB0 = 0.0
    private var targetB1 = 0.0
    private var targetB2 = 0.0
    private var targetA1 = 0.0
    private var targetA2 = 0.0

    private var x1 = 0.0
    private var x2 = 0.0
    private var y1 = 0.0
    private var y2 = 0.0

    // Per-sample interpolation factor toward the target coefficients.
    // Small enough to smooth out clicks at 44.1kHz, fast enough that the
    // filter catches up to a slider drag within a few tens of milliseconds.
    private val coefficientSmoothing = 0.002

    init {
        computeTargetCoefficients(1000.0, 4.0)
        snapToTarget()
    }

    @Synchronized
    fun retune(centerFrequencyHz: Double, q: Double) {
        computeTargetCoefficients(centerFrequencyHz, q)
    }

    @Synchronized
    fun process(input: Double): Double {
        b0 += (targetB0 - b0) * coefficientSmoothing
        b1 += (targetB1 - b1) * coefficientSmoothing
        b2 += (targetB2 - b2) * coefficientSmoothing
        a1 += (targetA1 - a1) * coefficientSmoothing
        a2 += (targetA2 - a2) * coefficientSmoothing

        val output = b0 * input + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1
        x1 = input
        y2 = y1
        y1 = output
        return output
    }

    private fun computeTargetCoefficients(centerFrequencyHz: Double, q: Double) {
        val w0 = 2.0 * PI * centerFrequencyHz / sampleRateHz
        val cosW0 = cos(w0)
        val alpha = sin(w0) / (2.0 * q)

        val a0 = 1.0 + alpha
        targetB0 = alpha / a0
        targetB1 = 0.0
        targetB2 = -alpha / a0
        targetA1 = (-2.0 * cosW0) / a0
        targetA2 = (1.0 - alpha) / a0
    }

    private fun snapToTarget() {
        b0 = targetB0
        b1 = targetB1
        b2 = targetB2
        a1 = targetA1
        a2 = targetA2
    }
}
