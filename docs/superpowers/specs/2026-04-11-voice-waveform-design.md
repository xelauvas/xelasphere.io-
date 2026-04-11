# Voice Mode Waveform Redesign — Addictive Canvas Visualization

**Date:** 2026-04-11
**Status:** Approved

## Problem

The current voice mode pill uses 40 DOM bar elements animated via `style.height` per frame. It's flat, predictable, and forgettable. Users should want to keep talking just to watch the waveform respond.

## Architecture

Replace DOM bars with a single `<canvas>` element. White monochrome waveform on dark background. States distinguished by motion pattern and intensity, not color. Six psychological enhancements make it feel alive.

## Core Rendering

**Canvas setup:** Single `<canvas>` filling the `.voice-pill-wave` container. Resolution matches `devicePixelRatio`. Runs at 60fps via `requestAnimationFrame`.

**Waveform shape:** Continuous bezier curve drawn with `ctx.bezierCurveTo()`, mirrored above and below center (except in speaking state — see enhancement 5). Points sampled every ~8px across the canvas width.

**Color:** `rgba(255, 255, 255, intensity)` only. Glow: same path drawn at 3x `lineWidth` with `rgba(200, 220, 255, 0.15)` for cold-light halo. No per-state color changes.

**Glow:** Draw the waveform path three times:
1. Outer glow: `lineWidth: 8`, `rgba(200, 220, 255, 0.08)`
2. Inner glow: `lineWidth: 4`, `rgba(255, 255, 255, 0.15)`
3. Core line: `lineWidth: 2`, `rgba(255, 255, 255, intensity)` where intensity varies by state

## Four States

**Idle** — dormant breathing
- Amplitude: 2-3px
- Frequency: ~0.8Hz single sine + noise
- Opacity: 0.3
- Noise intensity: low (0.15)

**Listening** — reactive to mic
- Amplitude: maps to `getAudioLevel()` (0-28px)
- Waveform surges with voice, shimmers on silence
- Opacity: 0.6-0.9 based on level
- Noise intensity: medium (0.3)
- Spring physics decay on silence (see enhancement 4)

**Thinking** — rhythmic pulse
- Amplitude: 8-12px, pulsing outward from center
- Frequency: ~1.5Hz traveling wave
- Opacity: 0.5
- Noise intensity: medium (0.25)

**Speaking** — most energetic
- Amplitude: high, multi-frequency (3 layered sines)
- Asymmetric top/bottom (see enhancement 5)
- Full opacity 1.0, brightest glow
- Noise intensity: high (0.4)
- Chromatic aberration at peak (see enhancement 6)

## State Transitions

300ms crossfade using `lerp()` on amplitude, frequency, opacity, and noise parameters. Particle burst on every transition (see enhancement 2).

## Six Enhancements

### 1. Simplex Noise

Layer lightweight 2D simplex noise on all sine wave calculations. Pure JS implementation (~20 lines, no library). The noise makes every frame slightly unpredictable — the waveform breathes irregularly like a living thing.

```
finalAmplitude = sineValue + noise2D(x * 0.02, time * 0.5) * noiseIntensity
```

`noiseIntensity` varies per state (0.15 idle, 0.3 listening, 0.25 thinking, 0.4 speaking).

### 2. Particle Burst on State Transitions

On every state change, spawn 8 particles at random positions along the waveform. Each particle:
- Small circle, 2-3px radius
- White, initial opacity 0.8
- Velocity: random outward from waveform surface (±3-8px/frame vertical, ±1-3px/frame horizontal)
- Fade: linear opacity decay over 400ms
- No allocation: particle pool of 16 objects, reused

### 3. Amplitude Trail (Ghost Lines)

Keep a ring buffer of 12 previous waveform amplitude snapshots (sampled every 2 frames = 30fps trail rate). On each render, draw all 12 as ghost waveforms behind the main line at decreasing opacity: `0.08 * (12 - i) / 12`. Creates a motion-blur ribbon effect — sound has physical momentum.

### 4. Spring Physics on Silence Decay

When listening state audio level drops (user stops speaking), don't lerp to zero. Use damped spring physics:

```
velocity += (target - current) * springForce  // springForce = 0.15
velocity *= damping                            // damping = 0.85
current += velocity
```

The waveform overshoots slightly below the target before settling. Feels weighted, not digital.

### 5. Asymmetric Speaking Waveform

In speaking state, the top and bottom halves of the waveform use different phase offsets:
- Top: base phase
- Bottom: base phase + π/6

The two halves are slightly out of sync — looks chaotic and alive but still clean. Only in speaking state; other states remain mirrored.

### 6. Chromatic Aberration at Peak

In speaking state, when amplitude > 0.7 of max:
- Draw the same waveform path offset x-1px in `rgba(255, 40, 40, 0.12)`
- Draw the same waveform path offset x+1px in `rgba(40, 40, 255, 0.12)`
- Draw main white path on top

Subtle RGB split visible only at peak energy. Looks like premium broadcast equipment.

## Pill Bar Changes

**Background:** Single dark for all states — `rgba(12, 12, 16, 0.95)`, border `rgba(255, 255, 255, 0.06)`. Remove per-state gradient backgrounds. The waveform glow is the only visual feedback.

**Label:** White at varying opacity per state (0.35 idle, 0.6 listening, 0.5 thinking, 0.7 speaking). Transition 300ms.

**End button:** Refine:
- Smaller: `padding: 6px 16px`, font 12px
- Softer red: `#ef4444`
- Phone-down icon or `x` instead of text "End"
- Subtle pulse glow on hover

## Performance

- Single canvas, no DOM manipulation per frame (was 40 elements)
- `will-change: transform` on canvas
- Canvas DPR-aware for crisp rendering on retina
- Simplex noise: pure math, no allocation
- Particle pool: pre-allocated, no GC pressure
- Trail buffer: ring buffer, fixed size 12
- Target: locked 60fps on mid-range mobile

## Files Touched

| File | Change |
|------|--------|
| `app/static/js/voiceModeModule.js` | Replace `_createPill` (DOM bars → canvas), replace `_animatePill` (bar animation → canvas renderer), add: simplex noise, particle system, trail buffer, spring physics |
| `app/static/css/styles.css` | Remove `.voice-pill-bar-segment` styles, remove per-state color themes, simplify pill to single dark background, add canvas sizing |
| `app/templates/index.html` | Bump cache version |

No new files. No backend changes.

## Success Criteria

1. Waveform feels organic and slightly unpredictable (noise)
2. State transitions feel punchy (particle burst)
3. Sound has visual momentum (ghost trails)
4. Silence decay feels physical (spring overshoot)
5. Speaking state feels alive (asymmetric + chromatic aberration)
6. Locked 60fps on desktop and mobile
7. Monochrome — no color changes between states, only intensity
