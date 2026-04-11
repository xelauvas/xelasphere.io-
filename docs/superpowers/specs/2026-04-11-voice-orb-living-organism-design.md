# Voice Orb: Living Organism (Metaball Lava Lamp)

**Date:** 2026-04-11
**Status:** Design approved, pending implementation

## Goal

Replace the current voice mode orb with a lava-lamp-style living organism. The orb should look like a viscous blob that morphs, breathes, and physically deforms in response to audio. Not a sphere with patterns — a blob with a shape that is never a perfect circle.

## Visual Target

- Lava lamp blob: thick, viscous, morphing boundary
- 15-25% deformation from circular — clearly not a circle, visible bulges moving around
- Internal color currents swirling (not sky clouds, not shiny/holographic)
- Self-illuminated (no directional light), soft bioluminescent rim at edges
- Audio physically stretches and distorts the surface
- Different color palette per state

## Technical Approach: Metaball SDF

### The Field

6 invisible blob centers orbit inside a bounding area. Each has a position `(x, y)` and effective radius `r`. The fragment shader computes field strength at every pixel:

```
field = sum( blob[i].r^2 / distance(pixel, blob[i].pos)^2 )  for i = 0..5
```

Where `field > threshold`, the pixel is inside the organism. The threshold is tuned to produce ~15-25% deformation from circular.

### Blob Motion (computed in JS animate())

Each blob follows a parametric orbit:
```
blob[i].x = centerX + orbitRadius[i] * cos(time * speed[i] + phase[i])
blob[i].y = centerY + orbitRadius[i] * sin(time * speed[i] * ratio[i] + phase[i])
```

- 6 blobs with different orbit radii (0.15 to 0.45 of orb radius), speeds, and phase offsets
- Orbits are elliptical (different x/y speed ratios) for variety
- No physics sim, just parametric paths that look organic

### Audio Reaction

When `smoothedLevel` rises:
- All orbit radii expand (blobs push outward) — stretches the boundary
- Blob effective radii increase slightly — surface gets thicker/more merged
- Effect: louder audio = more dramatic surface deformation

### Breathing

A slow global sine (0.4-0.5 Hz) modulates all orbit radii together:
```
breathe = 1.0 + sin(time * 0.45) * 0.04  // ~4% size oscillation
orbitRadius[i] *= breathe
```

### Internal Color Currents

Inside the isosurface, 2 noise layers create flowing color variation:

- **Layer 1 (large, slow):** `fbm(pos * 1.5 + drift1)` — big color bands
- **Layer 2 (medium, faster):** `fbm(pos * 3.0 + drift2)` — turbulence within bands

Blend 70/30, map through 3-stop gradient (dark -> mid -> bright) within the state's hue family.

### State Colors

| State | Dark | Mid | Bright | Rim |
|-------|------|-----|--------|-----|
| Idle | deep navy `(0.04, 0.08, 0.28)` | medium blue `(0.10, 0.22, 0.55)` | soft blue `(0.22, 0.40, 0.72)` | `(0.15, 0.30, 0.65)` |
| Listening | dark cyan `(0.04, 0.14, 0.35)` | cyan `(0.08, 0.38, 0.78)` | bright cyan `(0.25, 0.60, 0.95)` | `(0.15, 0.48, 0.88)` |
| Thinking | dark indigo `(0.10, 0.04, 0.28)` | violet `(0.28, 0.14, 0.58)` | bright purple `(0.48, 0.30, 0.80)` | `(0.38, 0.20, 0.72)` |
| Speaking | blue base `(0.08, 0.14, 0.35)` | blue-white `(0.22, 0.42, 0.78)` | bright `(0.50, 0.65, 0.95)` | `(0.35, 0.52, 0.88)` |

### Edge Rendering

- Pixels near the isosurface boundary get a fresnel-like rim brightening based on distance to the isosurface edge
- Soft anti-aliased edge via `smoothstep(threshold - softness, threshold + softness, field)`
- Outer glow: pixels just outside the isosurface get a gaussian falloff glow in the rim color

### State Behavior

| State | Orbit Speed | Orbit Radius | Blob Size | Noise Speed | Character |
|-------|------------|--------------|-----------|-------------|-----------|
| Idle | 0.3x | base | base | slow | Calm, resting, gentle breathing |
| Listening | 0.6x | base + audio push | base + audio | medium | Alert, reactive to voice |
| Thinking | 1.0x | tightened (0.85x) | base | fast | Concentrated, pulsing |
| Speaking | 1.4x | base + synth audio push | base + synth audio | fastest | Energized, rhythmic deformation |

Transitions interpolate smoothly over ~0.5s via the existing `smoothedLevel` lerp.

## Scope of Changes

### Replace
- Fragment shader: new metaball SDF + noise color shader
- `animate()`: add blob position computation, pass as uniforms
- `initWebGL()`: add uniform locations for blob positions/radii array
- `drawOrb()`: pass new uniforms

### Add
- JS blob state: array of 6 blob objects with orbit params
- New uniforms: `u_blobPos` (vec2 array, 6 elements) and `u_blobRad` (float array, 6 elements). WebGL 1 supports `uniform vec2 u_blobPos[6]` — set via `gl.uniform2fv()` with a flat Float32Array of 12 values. Same for radii with `gl.uniform1fv()` and 6 floats.

### Remove
- Dead functions: `drawHaloRings()`, `drawWaveform()`, `drawRimHighlight()`
- `waveHistory` array (unused)

### Keep (untouched)
- Voice pipeline (VAD, recording, transcription, TTS, interrupts)
- Overlay DOM layout and control bar
- CSS styling
- 3-canvas architecture (bg, orb, fx)
- Background gradient canvas
- All audio/mic code
- State machine and transitions

## Performance

- 6 distance calculations per pixel (metaball field) — very cheap
- 2 FBM noise samples per interior pixel (color currents) — moderate
- No ray marching, no loops over steps
- Should run 60fps on mobile easily
