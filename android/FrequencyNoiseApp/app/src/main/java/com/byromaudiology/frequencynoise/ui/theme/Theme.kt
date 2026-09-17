package com.byromaudiology.frequencynoise.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val AppColorScheme = lightColorScheme(
    primary = BluePrimary,
    secondary = BlueSecondary,
    background = BackgroundColor
)

@Composable
fun FrequencyNoiseTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AppColorScheme,
        typography = AppTypography,
        content = content
    )
}
