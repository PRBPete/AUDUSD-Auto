package com.byromaudiology.frequencynoise.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlin.concurrent.thread
import kotlin.math.pow
import kotlin.random.Random

/**
 * Generates narrow-band noise centered on an adjustable frequency and streams
 * it to the device speaker via [AudioTrack] in MODE_STREAM.
 *
 * Plain [AudioTrack] (rather than Oboe/AAudio over the NDK) is used here:
 * it adds tens of milliseconds of extra latency versus AAudio, which is
 * irrelevant for a steady masking-noise tone but saves a native build step.
 * If this were ever extended to something latency-sensitive (e.g. synced to
 * a visual beat), switching the write loop below to an Oboe callback would
 * be the first change.
 *
 * Thread-safety: [setFrequency] and [setVolume] are called from the UI/main
 * thread; the fields they write are `@Volatile` and read once per sample by
 * the dedicated playback thread started in [start].
 */
class NoiseAudioEngine {

    private val sampleRate = 44100
    private val filter = BandpassBiquad(sampleRate.toDouble())
    private val random = Random(System.nanoTime())

    @Volatile private var amplitude: Double = 0.0

    private var audioTrack: AudioTrack? = null
    private var playbackThread: Thread? = null
    @Volatile private var isRunning = false

    fun setFrequency(frequencyHz: Double) {
        val clamped = frequencyHz.coerceIn(FrequencyMapper.MIN_FREQUENCY_HZ, FrequencyMapper.MAX_FREQUENCY_HZ)
        filter.retune(clamped, BANDPASS_Q)
    }

    fun setVolume(volume01: Float) {
        amplitude = volumeToAmplitude(volume01)
    }

    fun start() {
        if (isRunning) return
        isRunning = true

        val minBufferSize = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val bufferSizeInBytes = maxOf(minBufferSize, sampleRate / 5) // >=200ms safety margin

        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(bufferSizeInBytes)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack = track
        track.play()

        playbackThread = thread(name = "NoiseAudioEngine", priority = Thread.MAX_PRIORITY) {
            generateAndPlayLoop(track)
        }
    }

    fun stop() {
        if (!isRunning) return
        isRunning = false
        playbackThread?.join(200)
        playbackThread = null

        audioTrack?.apply {
            stop()
            release()
        }
        audioTrack = null
    }

    private fun generateAndPlayLoop(track: AudioTrack) {
        val chunkFrames = sampleRate / 50 // 20ms chunks
        val chunk = ShortArray(chunkFrames)

        while (isRunning) {
            for (i in 0 until chunkFrames) {
                val whiteNoise = random.nextDouble(-1.0, 1.0)
                val filtered = filter.process(whiteNoise) * amplitude
                chunk[i] = (filtered.coerceIn(-1.0, 1.0) * Short.MAX_VALUE).toInt().toShort()
            }
            track.write(chunk, 0, chunkFrames)
        }
    }

    /**
     * Volume-to-amplitude uses a dB taper (-40dB at the bottom of the slider
     * up to 0dB at the top) rather than a linear one, since linear sliders
     * feel like they do "nothing" for their first half and then jump loud
     * near the top — perceived loudness is roughly logarithmic in amplitude.
     */
    private fun volumeToAmplitude(volume01: Float): Double {
        val v = volume01.coerceIn(0f, 1f)
        if (v <= 0f) return 0.0
        val db = MIN_VOLUME_DB * (1.0 - v)
        return 10.0.pow(db / 20.0)
    }

    private companion object {
        // ~15% fractional bandwidth (Q = f0 / bandwidth): narrow enough to
        // sound clearly pitched around the chosen frequency, wide enough to
        // still sound like noise rather than a pure tone.
        const val BANDPASS_Q = 1.0 / 0.15
        const val MIN_VOLUME_DB = -40.0
    }
}
