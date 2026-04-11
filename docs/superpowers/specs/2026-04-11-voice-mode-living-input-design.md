# Voice Mode: Living Input

**Date:** 2026-04-11
**Status:** Approved

## Overview

Voice mode transforms the existing textarea in place rather than overlaying a separate pill bar. The waveform renders as a subtle ambient canvas behind the textarea. Transcribed words materialize as ghostly text that solidifies, then fade out upward while the chat bubble fades in simultaneously. No new UI chrome — everything morphs from what already exists.

## Entry Transition

1. User taps mic button
2. A CSS ripple expands from the mic button across the input area (radial `clip-path` or pseudo-element scale, ~400ms)
3. As the ripple passes each element:
   - Textarea placeholder crossfades from "Message..." to "Listening..." (~200ms)
   - Send button morphs into end button (stop square icon, same position, border-radius + icon crossfade, ~300ms)
   - Waveform canvas fades in behind textarea text layer (opacity 0 → target, ~300ms)
   - Textarea border begins state-colored glow (blue for listening)
4. Controls fade to background (see Controls Behavior below)

## Controls Behavior During Voice Mode

The following controls fade to 0.15 opacity with `pointer-events: none` — visible but inert:

- Actions menu button (hamburger/plus)
- Attachment button (if present)
- Model selector dropdown

Fade duration: 200ms ease-out, staggered 50ms apart.

Tapping any of these during voice mode does nothing — no accidental triggers, no tooltips. They remain visually present so the user knows the UI hasn't broken.

The **mic button position** becomes the **end button** — it does not fade out, it morphs.

## Textarea Typing Escape Hatch

The textarea is not locked during voice mode. If the user starts typing, voice mode exits immediately and text input resumes.

**Trigger: `keydown`, not `focus`.**

- Tap alone (focus gained) — nothing happens, voice mode continues
- First keystroke (non-modifier) — voice mode exits, character lands in the now-active textarea
- Cmd/Ctrl shortcuts — ignored, don't accidentally exit on copy-paste gestures

```javascript
textarea.addEventListener('keydown', (e) => {
    if (voiceActive && !e.metaKey && !e.ctrlKey && !e.altKey) {
        exitVoice();
        // let the keydown proceed naturally into the now-active textarea
    }
});
```

The exit follows the standard reverse-ripple transition. The typed character appears as the first character in the field — no interruption, no confirmation dialog.

## Listening State

- Waveform canvas renders behind textarea at low opacity (~0.15–0.25), reacting to mic input via existing spring physics
- Textarea border glows soft blue (`rgba(96, 165, 250, 0.4)`)
- Placeholder text shows "Listening..." in blue-tinted color
- **Text materialization:** As Whisper returns transcript, text appears in the textarea:
  - Characters render starting at ~0.3 opacity
  - Solidify to 1.0 as the transcript is confirmed
  - Partial/in-progress words have a subtle shimmer (CSS animation, opacity oscillation 0.3–0.5)
  - A soft pulsing glow marks the insertion point (replaces blinking cursor)
  - Text uses the textarea's existing font, size, and padding — it looks like normal text that's fading in

## Text Send Animation

Once transcript is confirmed and the message sends:

1. The solidified text in the textarea fades out upward: `translateY(-20px)`, `opacity: 0` over 200ms ease-out
2. Simultaneously, the user chat bubble fades in at the top of the chat scroll: `opacity: 0 → 1`, `translateY(8px) → 0` over 200ms ease-out
3. Two separate animations, same emotional beat — no coordinate calculation needed
4. Textarea clears, waveform continues breathing, placeholder returns to "Listening..."

## Thinking State

- Waveform slows to a gentle ambient pulse
- Border shifts to amber (`rgba(251, 191, 36, 0.35)`)
- Placeholder shows "Thinking..." in amber-tinted color
- Textarea is empty — just the ambient waveform background
- Thinking sounds play (existing `ThinkingSounds` module)

## Speaking State

- Waveform reacts to TTS audio output via existing `_getSpeakingLevel()` spring physics
- Border shifts to green (`rgba(74, 222, 128, 0.35)`)
- Placeholder shows "Speaking..." in green-tinted color
- Textarea remains empty — AI's words appear only in the chat area
- Interrupt detection remains active (existing logic)

## Exit Transition

1. Reverse ripple collapses from edges back to mic button position (~400ms)
2. As the ripple recedes:
   - Waveform canvas fades out (~200ms)
   - End button morphs back to send button (~300ms)
   - Border returns to default styling
   - Placeholder crossfades back to "Message..."
3. Faded controls restore to full opacity (200ms ease-out, staggered 50ms)
4. Input area is fully normal — ready for typing

## Waveform Rendering

The waveform canvas sits behind the textarea text layer. Architecture:

```
.textarea-container (relative)
  canvas.voice-waveform-bg (absolute, inset 0, z-index 0, pointer-events none)
  textarea (relative, z-index 1, background transparent when voice active)
```

The canvas reuses all existing waveform logic from `voiceModeModule.js`:
- Simplex noise generation
- Spring physics for amplitude
- State color interpolation (RGB lerp)
- Ghost trails (12-frame ring buffer)
- Chromatic aberration on high speaking amplitude
- Particle bursts on state transitions

The only change: render at lower opacity. The `STATE_PARAMS` opacity values scale down:
- idle: 0.15
- listening: 0.25
- thinking: 0.20
- speaking: 0.25

Particles render at half their current alpha. Ghost trails at 60% current alpha.

## What Changes vs Current Implementation

### Remove
- The pill bar overlay (`voice-pill-bar` element and `.voice-pill-bar` CSS)
- The `_createPill()`, `_showPill()`, `_hidePill()` functions
- The `voice-active` class that hides textarea/actions (replaced by opacity fade)
- The fullscreen overlay CSS (`vm-overlay` and related classes in `voice-mode.css`) — unused, dead code

### Modify
- `voiceModeModule.js`: Canvas renders inside `.textarea-container` instead of pill
- `voiceModeModule.js`: `enter()` and `exit()` drive textarea transformation instead of pill show/hide
- `voiceModeModule.js`: `_animatePill()` renamed to `_animateWaveform()`, targets new canvas
- `voiceModeModule.js`: `setState()` updates textarea border/placeholder instead of pill dataset
- `styles.css`: Textarea gets voice-active styling (transparent bg, colored border, transitions)
- `styles.css`: Send button gets morph keyframes to/from end button

### Add
- Canvas element inside `.textarea-container` (created by JS on first voice entry, reused)
- Text materialization renderer: writes transcript into textarea with per-character opacity
- Text fade-out-up animation (CSS class applied on send)
- Chat bubble fade-in animation (CSS class applied on render)
- Ripple transition effect (CSS pseudo-element or dedicated element)
- `keydown` listener for typing escape hatch
- Send-to-end button morph CSS transitions

### Keep (unchanged)
- All audio capture, VAD, recording, transcription logic
- All TTS streaming pipeline
- Voice shortcuts and language detection
- Interrupt detection
- `speechSynthesisModule.js` and `dictationModule.js`
- `ThinkingSounds` integration
- All backend routes
