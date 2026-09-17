package com.byromaudiology.frequencynoise.audio

import org.junit.Assert.assertEquals
import org.junit.Test

class FrequencyMapperTest {

    @Test
    fun `slider extremes map to the documented frequency bounds`() {
        assertEquals(FrequencyMapper.MIN_FREQUENCY_HZ, FrequencyMapper.sliderToFrequency(0f), 0.01)
        assertEquals(FrequencyMapper.MAX_FREQUENCY_HZ, FrequencyMapper.sliderToFrequency(1f), 0.01)
    }

    @Test
    fun `mapping is monotonically increasing`() {
        val low = FrequencyMapper.sliderToFrequency(0.25f)
        val mid = FrequencyMapper.sliderToFrequency(0.5f)
        val high = FrequencyMapper.sliderToFrequency(0.75f)

        assert(low < mid)
        assert(mid < high)
    }

    @Test
    fun `frequencyToSlider is the inverse of sliderToFrequency`() {
        val original = 0.37f
        val frequency = FrequencyMapper.sliderToFrequency(original)
        val roundTrip = FrequencyMapper.frequencyToSlider(frequency)

        assertEquals(original, roundTrip, 0.001f)
    }

    @Test
    fun `out of range slider positions are clamped`() {
        assertEquals(FrequencyMapper.MIN_FREQUENCY_HZ, FrequencyMapper.sliderToFrequency(-1f), 0.01)
        assertEquals(FrequencyMapper.MAX_FREQUENCY_HZ, FrequencyMapper.sliderToFrequency(2f), 0.01)
    }
}
