package com.byromaudiology.frequencynoise

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.byromaudiology.frequencynoise.ui.NoiseScreen
import com.byromaudiology.frequencynoise.ui.NoiseViewModel
import com.byromaudiology.frequencynoise.ui.theme.FrequencyNoiseTheme

class MainActivity : ComponentActivity() {

    private val viewModel: NoiseViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            FrequencyNoiseTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    NoiseScreen(viewModel = viewModel)
                }
            }
        }
    }
}
