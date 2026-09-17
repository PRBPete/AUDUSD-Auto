package com.byromaudiology.frequencynoise.audio

import kotlin.math.exp
import kotlin.math.ln

/**
 * Maps a UI slider position in [0, 1] to a center frequency in Hz, and back.
 *
 * The mapping is logarithmic rather than linear: pitch perception and the
 * 50 Hz-12,000 Hz range span more than two octaves per decade, so a linear
 * slider would leave the entire low end (where most narrow-band masking
 * tones live) compressed into a few unusable pixels.
 */
object FrequencyMapper {

    const val MIN_FREQUENCY_HZ = 50.0
    const val MAX_FREQUENCY_HZ = 12000.0

    private val logMin = ln(MIN_FREQUENCY_HZ)
    private val logMax = ln(MAX_FREQUENCY_HZ)

    fun sliderToFrequency(sliderPosition: Float): Double {
        val t = sliderPosition.coerceIn(0f, 1f).toDouble()
        return exp(logMin + t * (logMax - logMin))
    }

    fun frequencyToSlider(frequencyHz: Double): Float {
        val f = frequencyHz.coerceIn(MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ)
        return ((ln(f) - logMin) / (logMax - logMin)).toFloat()
    }
}
