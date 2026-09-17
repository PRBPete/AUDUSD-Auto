# COSTAR Prompt: Android Narrow-Band Frequency Noise App

Use this prompt to brief an AI assistant or engineer to design/build the app.

---

**Context**

You are an expert Android app developer with deep experience in Kotlin, Jetpack
Compose, and the Android audio stack (`AudioTrack`, `Oboe`/`AAudio` for
low-latency playback, and DSP fundamentals). The goal is a simple utility app
used for sound-therapy / tinnitus-masking / hearing-test style purposes: the
user picks a target frequency and the app generates and plays narrow-band
noise centered on that frequency in real time, entirely on-device (no
pre-recorded audio files, no network calls). The app must run reliably on a
typical modern Android phone (API 26+) with low latency and no audible
glitches, clicks, or dropouts when parameters change.

**Objective**

Design and implement an Android app with exactly this behavior:

1. A single primary screen with a horizontal slider (or equivalent control)
   that selects a **center frequency between 50 Hz and 12,000 Hz**.
   - Show the currently selected frequency numerically next to the slider,
     updating live as the user drags.
   - Use a logarithmic (not linear) mapping from slider position to
     frequency, since pitch perception and this frequency range span over
     two orders of magnitude — a linear slider would make the low end
     unusably coarse.
2. Real-time **narrow-band noise generation** centered on the selected
   frequency:
   - Generate noise (e.g., filtered white/pink noise, or additive/randomized
     phase noise within a band) rather than a pure tone, band-limited around
     the center frequency with a sensible, configurable bandwidth (e.g., a
     fraction of the center frequency, or a fixed Q).
   - Regenerate/retune the filter smoothly and immediately when the slider
     moves, without stopping playback or causing pops/clicks (e.g., via
     coefficient smoothing, crossfading, or a resumable audio pipeline).
   - Implement the DSP with a bandpass filter (e.g., a biquad or FFT-based
     spectral shaping approach) applied to a white-noise source, streamed
     through `AudioTrack` in streaming mode with a background thread/coroutine
     feeding the audio buffer continuously.
3. A **volume control** (separate slider or vertical fader) that adjusts
   playback loudness in real time, independent of the frequency control,
   using a perceptually-appropriate (e.g., logarithmic/dB) scale rather than
   raw linear amplitude.
4. Playback controls: a Play/Pause (or Start/Stop) toggle so noise generation
   only runs when the user wants it, and the app cleanly releases audio
   resources when paused, backgrounded, or closed.
5. Correct Android lifecycle handling: pause audio on `onStop`/backgrounding
   (or use a foreground service with a notification if playback should
   continue in the background — decide and justify which), no memory leaks,
   and no crashes on rapid slider movement or rapid pause/resume.
6. Sensible project structure: Kotlin, Jetpack Compose for UI, a
   ViewModel holding frequency/volume/playing state, and a separate
   audio-engine class isolating the DSP/`AudioTrack` code from the UI layer.

Deliver: the full Android Studio project (Gradle files, manifest, Compose UI,
ViewModel, audio engine class with the noise generation + bandpass filter
implementation, and unit-testable frequency-mapping logic), plus a short
README explaining how to build/run it and the DSP approach used.

**Style**

Write idiomatic, modern Kotlin (null-safety, coroutines/`Flow` for state,
Compose best practices — state hoisting, `remember`/`mutableStateOf`). Keep
the audio engine framework-agnostic where possible so the filter/generation
logic is unit-testable without instrumentation tests. Comment only where the
DSP math or a non-obvious Android audio API constraint needs explaining;
otherwise let clear naming carry the code.

**Tone**

Precise, engineering-focused, and pragmatic — like a senior Android/audio
engineer explaining implementation decisions to a peer. Call out any
trade-offs explicitly (e.g., biquad filter simplicity vs. FFT filter
precision, `AudioTrack` vs. Oboe for latency) rather than glossing over them.

**Audience**

A mobile developer (or another AI coding assistant) who will read this prompt
and implement the app directly — assume Android/Kotlin familiarity but do not
assume prior audio DSP experience, so briefly justify DSP choices (e.g., why
narrow-band noise instead of a pure sine tone, why log-scale sliders).

**Response**

Respond with:
1. A brief chosen-architecture summary (audio API choice, filter type,
   threading model) and why, in 1–2 short paragraphs.
2. The complete set of source files needed to build and run the app.
3. A short "how to test" section covering: frequency range at both slider
   extremes (50 Hz and 12,000 Hz), smooth retuning while noise is playing,
   volume range, and pause/resume/background behavior.
