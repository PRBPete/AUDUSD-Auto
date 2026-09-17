package com.byromaudiology.frequencynoise.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import java.util.Locale

@Composable
fun NoiseScreen(viewModel: NoiseViewModel) {
    val sliderPosition by viewModel.sliderPosition.collectAsStateWithLifecycle()
    val frequencyHz by viewModel.frequencyHz.collectAsStateWithLifecycle()
    val volume by viewModel.volume.collectAsStateWithLifecycle()
    val isPlaying by viewModel.isPlaying.collectAsStateWithLifecycle()

    // This app is a foreground diagnostic/masking tool, not background audio,
    // so playback is simply paused when the app leaves the foreground rather
    // than promoted to a foreground service with a persistent notification.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) {
                viewModel.pausePlaybackIfPlaying()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(text = "Narrow-Band Frequency Noise", style = MaterialTheme.typography.titleLarge)
        Spacer(modifier = Modifier.height(32.dp))

        Text(text = formatFrequency(frequencyHz), style = MaterialTheme.typography.titleLarge)
        Slider(
            value = sliderPosition,
            onValueChange = viewModel::onSliderChanged,
            modifier = Modifier.fillMaxWidth()
        )
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(text = "50 Hz", style = MaterialTheme.typography.labelSmall)
            Text(text = "12,000 Hz", style = MaterialTheme.typography.labelSmall)
        }

        Spacer(modifier = Modifier.height(40.dp))

        Text(text = "Volume: ${(volume * 100).toInt()}%", style = MaterialTheme.typography.bodyLarge)
        Slider(
            value = volume,
            onValueChange = viewModel::onVolumeChanged,
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(40.dp))

        Button(onClick = viewModel::togglePlayback) {
            Text(text = if (isPlaying) "Pause" else "Play")
        }
    }
}

private fun formatFrequency(frequencyHz: Double): String {
    return if (frequencyHz >= 1000.0) {
        String.format(Locale.US, "%.2f kHz", frequencyHz / 1000.0)
    } else {
        String.format(Locale.US, "%.0f Hz", frequencyHz)
    }
}
