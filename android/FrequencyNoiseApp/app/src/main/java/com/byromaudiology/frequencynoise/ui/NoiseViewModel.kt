package com.byromaudiology.frequencynoise.ui

import androidx.lifecycle.ViewModel
import com.byromaudiology.frequencynoise.audio.FrequencyMapper
import com.byromaudiology.frequencynoise.audio.NoiseAudioEngine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

private const val DEFAULT_FREQUENCY_HZ = 1000.0
private const val DEFAULT_VOLUME = 0.6f

class NoiseViewModel @JvmOverloads constructor(
    private val audioEngine: NoiseAudioEngine = NoiseAudioEngine()
) : ViewModel() {

    private val _sliderPosition = MutableStateFlow(FrequencyMapper.frequencyToSlider(DEFAULT_FREQUENCY_HZ))
    val sliderPosition: StateFlow<Float> = _sliderPosition.asStateFlow()

    private val _frequencyHz = MutableStateFlow(DEFAULT_FREQUENCY_HZ)
    val frequencyHz: StateFlow<Double> = _frequencyHz.asStateFlow()

    private val _volume = MutableStateFlow(DEFAULT_VOLUME)
    val volume: StateFlow<Float> = _volume.asStateFlow()

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    init {
        audioEngine.setFrequency(_frequencyHz.value)
        audioEngine.setVolume(_volume.value)
    }

    fun onSliderChanged(position: Float) {
        _sliderPosition.value = position
        val frequency = FrequencyMapper.sliderToFrequency(position)
        _frequencyHz.value = frequency
        audioEngine.setFrequency(frequency)
    }

    fun onVolumeChanged(volume: Float) {
        _volume.value = volume
        audioEngine.setVolume(volume)
    }

    fun togglePlayback() {
        if (_isPlaying.value) {
            audioEngine.stop()
        } else {
            audioEngine.start()
        }
        _isPlaying.value = !_isPlaying.value
    }

    fun pausePlaybackIfPlaying() {
        if (!_isPlaying.value) return
        audioEngine.stop()
        _isPlaying.value = false
    }

    override fun onCleared() {
        audioEngine.stop()
        super.onCleared()
    }
}
