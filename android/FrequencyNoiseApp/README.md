# Frequency Noise

An Android app that generates narrow-band noise centered on a
user-selected frequency, entirely on-device and in real time — no
pre-recorded audio files, no network calls. Built from the COSTAR prompt in
[`docs/android-frequency-noise-app-costar-prompt.md`](../../docs/android-frequency-noise-app-costar-prompt.md).

## What it does

- A logarithmic-scale slider selects a center frequency between **50 Hz**
  and **12,000 Hz**, with the current value shown live as it's dragged.
- Narrow-band noise (white noise passed through a bandpass filter, not a
  pure tone) plays centered on that frequency, retuning smoothly with no
  clicks or pops as the slider moves.
- A second slider controls volume on a perceptual (dB) scale.
- Play/Pause starts and stops generation; audio is released cleanly when
  paused or when the app leaves the foreground.

## Architecture

- **`audio/FrequencyMapper`** — pure, unit-testable log-scale mapping
  between slider position `[0, 1]` and frequency in Hz. Logarithmic because
  the 50 Hz-12,000 Hz range spans more than two octaves per decade; a linear
  slider would crowd the entire low end into a few pixels.
- **`audio/BandpassBiquad`** — an RBJ ("Audio EQ Cookbook") constant-peak-gain
  bandpass biquad with ~15% fractional bandwidth (`Q ≈ 6.7`). Coefficients
  are interpolated toward their target on every sample rather than swapped
  instantly, which is what makes retuning while playing glitch-free.
- **`audio/NoiseAudioEngine`** — generates white noise, runs it through the
  biquad, applies the volume-derived amplitude, and streams 16-bit PCM to a
  plain `AudioTrack` in `MODE_STREAM` from a dedicated high-priority thread.
  Plain `AudioTrack` was chosen over Oboe/AAudio: it costs some extra
  latency, which doesn't matter for a steady masking tone, but avoids an NDK
  build step. If this app ever needs tighter latency (e.g. syncing to a
  visual beat), swap the write loop for an Oboe callback.
- **`ui/NoiseViewModel`** — holds frequency/volume/playing state as
  `StateFlow`s and forwards changes to the audio engine.
- **`ui/NoiseScreen`** — the Jetpack Compose UI; observes lifecycle `ON_STOP`
  to pause playback when the app is backgrounded.

### Why pause instead of a foreground service?

This app is a foreground diagnostic/masking tool the user actively watches
and adjusts, not background music. Pausing on `ON_STOP` avoids the added
complexity of a persistent notification and the `FOREGROUND_SERVICE`/
`POST_NOTIFICATIONS` permissions a background-audio service would need.

## Building & running

This project doesn't check in the Gradle wrapper jar. Either:

1. Open `android/FrequencyNoiseApp` in Android Studio (Koala or newer) and
   let it sync — Android Studio will use its bundled Gradle automatically, or
2. From a machine with Gradle installed, run `gradle wrapper` once inside
   this directory to generate `gradlew`/`gradlew.bat`, then use those.

Requires JDK 17. `compileSdk`/`targetSdk` 34, `minSdk` 26.

## Testing checklist

- [ ] Drag the frequency slider to both extremes — confirm the readout
      shows ~50 Hz at one end and ~12,000 Hz at the other.
- [ ] Start playback, then drag the frequency slider across its full range
      — confirm the pitch of the noise audibly tracks the slider with no
      clicks, pops, or dropouts.
- [ ] Drag the volume slider from 0 to 100% while playing — confirm a smooth
      perceived loudness ramp, silence at 0%.
- [ ] Press Play, then Pause — confirm audio stops immediately and cleanly.
- [ ] Press Play, then background the app (Home button) — confirm audio
      stops; confirm returning to the app shows the Play state (not still
      "Playing").
- [ ] Rapidly drag the frequency slider back and forth while playing —
      confirm no crashes and no runaway distortion.
- [ ] Run the unit tests: `FrequencyMapperTest` (from Android Studio, or
      `./gradlew testDebugUnitTest` once the wrapper is generated).
