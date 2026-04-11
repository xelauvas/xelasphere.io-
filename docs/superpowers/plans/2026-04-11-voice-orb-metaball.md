# Voice Orb: Living Organism Metaball Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the voice mode orb with a lava-lamp metaball organism that morphs, breathes, and deforms in response to audio.

**Architecture:** 6 metaball centers orbit parametrically inside a bounding area. A fragment shader computes the metaball SDF field per pixel, colors the interior with domain-warped FBM noise currents, and renders a soft bioluminescent edge. JS `animate()` computes blob positions each frame and passes them as uniforms.

**Tech Stack:** WebGL 1 fragment shader, vanilla JS, existing canvas architecture.

**Spec:** `docs/superpowers/specs/2026-04-11-voice-orb-living-organism-design.md`

---

### Task 1: Remove dead code and waveHistory

**Files:**
- Modify: `app/static/js/voiceModeModule.js:57` (waveHistory), `:1029-1031` (waveHistory usage), `:1110-1265` (dead draw functions)

- [ ] **Step 1: Remove `waveHistory` declaration (line 57)**

Replace:
```js
    // Waveform history for smooth animation
    const waveHistory = new Array(128).fill(0);
```
With nothing (delete both lines).

- [ ] **Step 2: Remove waveHistory usage in animate() (lines 1029-1031)**

Delete these lines from `animate()`:
```js
        // Update wave history
        waveHistory.shift();
        waveHistory.push(smoothedLevel);
```

- [ ] **Step 3: Remove dead draw functions (lines 1110-1265)**

Delete the entire bodies of these functions (they are never called since `drawEffects` was gutted):
- `drawHaloRings()` (line 1110)
- `drawWaveform()` (line 1127)
- `drawOrbGlow()` (line 1183)
- `drawRimHighlight()` (line 1212)

Delete from `function drawHaloRings()` through the closing `}` of `drawRimHighlight()`.

- [ ] **Step 4: Verify no JS errors**

Run in browser console or check that the module loads without errors. The voice mode overlay should still open (it will show the old shader, which we replace next).

- [ ] **Step 5: Commit**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "chore: remove dead waveHistory, haloRings, waveform, glow functions from voice mode"
```

---

### Task 2: Add blob state and orbit computation in JS

**Files:**
- Modify: `app/static/js/voiceModeModule.js:44-54` (add blob state after orb params), `:1002-1046` (animate function)

- [ ] **Step 1: Add blob orbit definitions after the `orb` object (after line 54)**

Insert after the `orb` object closing brace and before the waveHistory line (which is now deleted, so after `};` of orb):

```js
    // Metaball blob orbit parameters — 6 blobs
    const NUM_BLOBS = 6;
    const blobs = [
        { orbitR: 0.18, speed: 0.70, ratio: 1.30, phase: 0.0,  baseRad: 0.38 },
        { orbitR: 0.30, speed: 0.55, ratio: 0.80, phase: 1.05, baseRad: 0.35 },
        { orbitR: 0.40, speed: 0.40, ratio: 1.10, phase: 2.10, baseRad: 0.32 },
        { orbitR: 0.25, speed: 0.85, ratio: 0.65, phase: 3.14, baseRad: 0.36 },
        { orbitR: 0.35, speed: 0.50, ratio: 1.25, phase: 4.19, baseRad: 0.33 },
        { orbitR: 0.22, speed: 0.65, ratio: 0.90, phase: 5.24, baseRad: 0.37 },
    ];
    // Flat arrays for passing to WebGL uniforms
    const blobPosData = new Float32Array(NUM_BLOBS * 2); // x,y pairs
    const blobRadData = new Float32Array(NUM_BLOBS);
```

- [ ] **Step 2: Replace the orb breathing + waveHistory section in animate() (lines 1029-1046)**

Replace the section from `// Update wave history` through `orb.radius = orb.baseRadius * (1 + breath + breath2 + audioScale);` with blob orbit computation:

```js
        // ── Compute metaball blob positions ──
        // State multipliers for orbit speed and radius
        const speedMult = currentState === 'idle' ? 0.3
                        : currentState === 'listening' ? 0.6
                        : currentState === 'thinking' ? 1.0
                        : 1.4;
        const orbitMult = currentState === 'thinking' ? 0.85 : 1.0;
        // Audio pushes blobs outward + inflates them
        const audioPush = smoothedLevel * 0.5;
        const audioInflate = smoothedLevel * 0.12;
        // Global breathing
        const breathe = 1.0 + Math.sin(time * 0.45) * 0.04
                            + Math.sin(time * 0.45 * 1.73 + 0.8) * 0.015;

        for (let i = 0; i < NUM_BLOBS; i++) {
            const b = blobs[i];
            const t_blob = time * b.speed * speedMult;
            const r = (b.orbitR * orbitMult + audioPush) * breathe;
            blobPosData[i * 2]     = r * Math.cos(t_blob + b.phase);
            blobPosData[i * 2 + 1] = r * Math.sin(t_blob * b.ratio + b.phase);
            blobRadData[i] = b.baseRad + audioInflate;
        }

        // Keep orb.radius for layout (status text positioning, etc.)
        orb.radius = orb.baseRadius * (1.0 + smoothedLevel * 0.2);
```

- [ ] **Step 3: Verify animate still runs**

Open voice mode in browser. The orb will render with the old shader (wrong visuals) but should not crash. Console should be free of errors.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "feat: add metaball blob orbit computation in animate()"
```

---

### Task 3: Write the metaball fragment shader

**Files:**
- Modify: `app/static/js/voiceModeModule.js:77-273` (replace entire fragmentShaderSource)

- [ ] **Step 1: Replace the fragment shader**

Replace everything from `const fragmentShaderSource = \`` through the closing `` \`; `` (lines 77-273) with:

```js
    const fragmentShaderSource = `
        precision highp float;

        varying vec2 v_uv;
        uniform float u_time;
        uniform float u_audioLevel;
        uniform vec2 u_resolution;
        uniform vec2 u_orbCenter;
        uniform float u_orbRadius;
        uniform int u_state;
        uniform vec2 u_blobPos[6];
        uniform float u_blobRad[6];

        // ── Simplex noise (3D) ──
        vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
        vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

        float snoise(vec3 v) {
            const vec2 C = vec2(1.0/6.0, 1.0/3.0);
            const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
            vec3 i  = floor(v + dot(v, C.yyy));
            vec3 x0 = v - i + dot(i, C.xxx);
            vec3 g = step(x0.yzx, x0.xyz);
            vec3 l = 1.0 - g;
            vec3 i1 = min(g.xyz, l.zxy);
            vec3 i2 = max(g.xyz, l.zxy);
            vec3 x1 = x0 - i1 + C.xxx;
            vec3 x2 = x0 - i2 + C.yyy;
            vec3 x3 = x0 - D.yyy;
            i = mod289(i);
            vec4 p = permute(permute(permute(
                i.z + vec4(0.0, i1.z, i2.z, 1.0))
                + i.y + vec4(0.0, i1.y, i2.y, 1.0))
                + i.x + vec4(0.0, i1.x, i2.x, 1.0));
            float n_ = 0.142857142857;
            vec3 ns = n_ * D.wyz - D.xzx;
            vec4 j  = p - 49.0 * floor(p * ns.z * ns.z);
            vec4 x_ = floor(j * ns.z);
            vec4 y_ = floor(j - 7.0 * x_);
            vec4 x = x_ * ns.x + ns.yyyy;
            vec4 y = y_ * ns.x + ns.yyyy;
            vec4 h = 1.0 - abs(x) - abs(y);
            vec4 b0 = vec4(x.xy, y.xy);
            vec4 b1 = vec4(x.zw, y.zw);
            vec4 s0 = floor(b0) * 2.0 + 1.0;
            vec4 s1 = floor(b1) * 2.0 + 1.0;
            vec4 sh = -step(h, vec4(0.0));
            vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
            vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
            vec3 p0 = vec3(a0.xy, h.x);
            vec3 p1 = vec3(a0.zw, h.y);
            vec3 p2 = vec3(a1.xy, h.z);
            vec3 p3 = vec3(a1.zw, h.w);
            vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
            p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
            vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
            m = m * m; m = m * m;
            return 42.0 * dot(m * m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
        }

        float fbm(vec3 p) {
            float v = 0.0, a = 0.5, f = 1.0;
            for (int i = 0; i < 5; i++) {
                v += a * snoise(p * f);
                f *= 2.1;
                a *= 0.47;
            }
            return v;
        }

        // ── Metaball field: sum of blob contributions ──
        float metaballField(vec2 p) {
            float field = 0.0;
            for (int i = 0; i < 6; i++) {
                vec2 d = p - u_blobPos[i];
                float r2 = u_blobRad[i] * u_blobRad[i];
                float dist2 = dot(d, d) + 0.001; // avoid div-by-zero
                field += r2 / dist2;
            }
            return field;
        }

        void main() {
            vec2 fragCoord = v_uv * u_resolution;
            vec2 toOrb = fragCoord - u_orbCenter;
            // Normalize so the orb bounding area is ~1.0 radius
            vec2 p = toOrb / u_orbRadius;
            float dist = length(p);

            // Early discard well outside
            if (dist > 1.8) { discard; return; }

            // ── Per-state animation speed ──
            float stateSpeed = u_state == 0 ? 0.30
                             : u_state == 1 ? 0.55
                             : u_state == 2 ? 0.95
                             : 1.40;
            float t = u_time * stateSpeed;

            // ── Compute metaball field ──
            float field = metaballField(p);
            float threshold = 1.8;
            float softness = 0.3;

            // ── Outside the organism: outer glow ──
            if (field < threshold - softness) {
                // Gaussian glow that follows the metaball shape
                float glowStrength = field / (threshold - softness);
                float glow = pow(glowStrength, 3.0) * (0.15 + u_audioLevel * 0.3);
                if (glow < 0.003) { discard; return; }

                vec3 glowColor;
                if (u_state == 0)      glowColor = vec3(0.15, 0.30, 0.65);
                else if (u_state == 1) glowColor = vec3(0.15, 0.48, 0.88);
                else if (u_state == 2) glowColor = vec3(0.38, 0.20, 0.72);
                else                   glowColor = vec3(0.35, 0.52, 0.88);

                gl_FragColor = vec4(glowColor, glow);
                return;
            }

            // ── Edge zone: soft anti-aliased boundary ──
            float edgeAlpha = smoothstep(threshold - softness, threshold + softness, field);

            // ── State color palettes ──
            vec3 cDark, cMid, cBright, rimColor;
            if (u_state == 0) {
                cDark    = vec3(0.04, 0.08, 0.28);
                cMid     = vec3(0.10, 0.22, 0.55);
                cBright  = vec3(0.22, 0.40, 0.72);
                rimColor = vec3(0.15, 0.30, 0.65);
            } else if (u_state == 1) {
                cDark    = vec3(0.04, 0.14, 0.35);
                cMid     = vec3(0.08, 0.38, 0.78);
                cBright  = vec3(0.25, 0.60, 0.95);
                rimColor = vec3(0.15, 0.48, 0.88);
            } else if (u_state == 2) {
                cDark    = vec3(0.10, 0.04, 0.28);
                cMid     = vec3(0.28, 0.14, 0.58);
                cBright  = vec3(0.48, 0.30, 0.80);
                rimColor = vec3(0.38, 0.20, 0.72);
            } else {
                cDark    = vec3(0.08, 0.14, 0.35);
                cMid     = vec3(0.22, 0.42, 0.78);
                cBright  = vec3(0.50, 0.65, 0.95);
                rimColor = vec3(0.35, 0.52, 0.88);
            }

            // ── Internal color currents ──
            // Layer 1: large slow-moving bands
            vec3 sp1 = vec3(p * 1.5, 0.0) + vec3(t * 0.10, -t * 0.07, t * 0.05);
            sp1.xy += p * u_audioLevel * 0.8;
            float n1 = fbm(sp1);

            // Layer 2: medium turbulence, different drift
            vec3 sp2 = vec3(p * 3.0, 0.0) + vec3(-t * 0.15, t * 0.12, -t * 0.08) + 4.2;
            sp2.xy += p * u_audioLevel * 1.0;
            float n2 = fbm(sp2);

            // Blend 70/30, remap to 0-1
            float flow = n1 * 0.70 + n2 * 0.30;
            flow = flow * 0.5 + 0.5;
            flow = smoothstep(0.25, 0.75, flow);
            // Audio brightens
            flow *= (0.9 + u_audioLevel * 0.5);
            flow = clamp(flow, 0.0, 1.0);

            // Map through 3-stop palette
            vec3 color = mix(cDark, cMid, smoothstep(0.0, 0.5, flow));
            color = mix(color, cBright, smoothstep(0.4, 1.0, flow));

            // ── Bioluminescent rim ──
            // How close to the isosurface edge (0 = deep inside, 1 = at edge)
            float edgeProximity = 1.0 - smoothstep(threshold, threshold + 2.5, field);
            color += rimColor * pow(edgeProximity, 1.8) * (0.4 + u_audioLevel * 0.5);

            gl_FragColor = vec4(color, edgeAlpha);
        }
    `;
```

- [ ] **Step 2: Verify the shader string has no syntax errors**

Check that the backtick-delimited template string is valid JS (matching backticks, no unescaped characters). The shader itself will be validated at WebGL compile time in the next task.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "feat: metaball SDF fragment shader with color currents and bioluminescent rim"
```

---

### Task 4: Wire up new uniforms in initWebGL() and drawOrb()

**Files:**
- Modify: `app/static/js/voiceModeModule.js:422-430` (initWebGL uniform locations), `:1071-1098` (drawOrb uniform passing)

- [ ] **Step 1: Add blob uniform locations in initWebGL() (replace lines 422-430)**

Replace the `orbUniforms = { ... }` block:

```js
        // Get uniform locations
        orbUniforms = {
            time: gl.getUniformLocation(orbProgram, 'u_time'),
            audioLevel: gl.getUniformLocation(orbProgram, 'u_audioLevel'),
            resolution: gl.getUniformLocation(orbProgram, 'u_resolution'),
            orbCenter: gl.getUniformLocation(orbProgram, 'u_orbCenter'),
            orbRadius: gl.getUniformLocation(orbProgram, 'u_orbRadius'),
            state: gl.getUniformLocation(orbProgram, 'u_state'),
            blobPos: gl.getUniformLocation(orbProgram, 'u_blobPos'),
            blobRad: gl.getUniformLocation(orbProgram, 'u_blobRad')
        };
```

- [ ] **Step 2: Pass blob uniforms in drawOrb() (replace lines 1081-1091)**

Replace the uniform-setting section of `drawOrb()`:

```js
        // Set uniforms
        gl.uniform1f(orbUniforms.time, time);
        gl.uniform1f(orbUniforms.audioLevel, smoothedLevel);
        gl.uniform2f(orbUniforms.resolution, canvasOrb.width, canvasOrb.height);
        gl.uniform2f(orbUniforms.orbCenter, orb.x * dpr, (window.innerHeight - orb.y) * dpr);
        gl.uniform1f(orbUniforms.orbRadius, orb.radius * dpr);

        const stateIndex = currentState === 'listening' ? 1 :
                          currentState === 'thinking' ? 2 :
                          currentState === 'speaking' ? 3 : 0;
        gl.uniform1i(orbUniforms.state, stateIndex);

        // Pass metaball blob positions and radii
        gl.uniform2fv(orbUniforms.blobPos, blobPosData);
        gl.uniform1fv(orbUniforms.blobRad, blobRadData);
```

- [ ] **Step 3: Test in browser**

Open voice mode. The metaball organism should now render:
- Verify the orb is NOT a perfect circle — visible bulges should move around
- Verify internal color patterns are visible (flowing blue tones, not flat)
- Verify the orb animates (blobs orbit, patterns drift)
- Check browser console for WebGL shader compile errors — fix any if found

- [ ] **Step 4: Commit**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "feat: wire metaball blob uniforms to shader — living organism orb complete"
```

---

### Task 5: Bump cache version and update module header

**Files:**
- Modify: `app/templates/index.html:908` (cache bust)
- Modify: `app/static/js/voiceModeModule.js:1-4` (module header comment)

- [ ] **Step 1: Update cache version in index.html (line 908)**

Replace:
```html
    <script src="{{ url_for('static', filename='js/voiceModeModule.js') }}?v=7"></script>
```
With:
```html
    <script src="{{ url_for('static', filename='js/voiceModeModule.js') }}?v=8"></script>
```

- [ ] **Step 2: Update module header comment (lines 1-4)**

Replace:
```js
/**
 * Voice Mode Module - Premium WebGL Orb UI
 * Volumetric cloud sphere with waveform visualization
 */
```
With:
```js
/**
 * Voice Mode Module - Living Organism Orb
 * Metaball SDF lava lamp with audio-reactive deformation
 */
```

- [ ] **Step 3: Restart server and test**

```bash
sudo systemctl restart xelasphere
```

Open voice mode in a fresh browser tab (hard refresh). Verify:
1. Orb renders as a morphing blob, not a circle
2. Blobs merge and separate smoothly
3. Internal color flows are visible
4. Speaking into mic causes visible surface deformation
5. State color changes work (tap to start listening, watch color shift)
6. No console errors

- [ ] **Step 4: Commit**

```bash
git add app/static/js/voiceModeModule.js app/templates/index.html
git commit -m "feat: living organism voice orb — metaball lava lamp with audio-reactive deformation"
```

---

### Task 6: Tune and polish (visual iteration)

This task has no pre-written code — it's tuning constants based on visual feedback.

**Files:**
- Modify: `app/static/js/voiceModeModule.js` (blob params, shader constants)

- [ ] **Step 1: Test idle state breathing**

Watch the orb at idle for 10+ seconds. The breathing should be visible but gentle (~4% size oscillation). If too subtle, increase the `0.04` in the breathe formula in `animate()`. If too fast, decrease `0.45` Hz.

- [ ] **Step 2: Test audio reactivity**

Speak into the mic while in listening mode. The surface should visibly deform — bigger bumps pushing outward. If too subtle, increase `audioPush` (the `0.5` multiplier on `smoothedLevel`). If too much, decrease it.

- [ ] **Step 3: Test blob deformation amount**

The boundary should be 15-25% off circular. If too blobby, reduce `orbitR` values in the blobs array. If too circular, increase them. The `threshold` value in the shader (currently `1.8`) controls how merged the blobs are — lower = more blobby, higher = more merged/circular.

- [ ] **Step 4: Test state transitions**

Cycle through states (idle -> listening -> thinking -> speaking). Colors should shift smoothly. The organism should feel "calm" at idle, "alert" when listening, "concentrated" when thinking, "excited" when speaking. Adjust `speedMult` values if needed.

- [ ] **Step 5: Test on mobile**

Open on a phone. Verify 60fps (no jank). If performance is bad, reduce FBM octaves from 5 to 4 in the shader (change the loop bound).

- [ ] **Step 6: Commit final tuning**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "polish: tune metaball organism — blob orbits, audio reactivity, state transitions"
```
