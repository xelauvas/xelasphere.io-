# Voice Waveform Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat DOM-bar voice mode animation with an organic, monochrome Canvas 2D waveform that feels alive — with simplex noise, particle bursts, ghost trails, spring physics, asymmetric speaking, and chromatic aberration.

**Architecture:** A single `<canvas>` element replaces 40 DOM `<span>` bars inside the voice pill. A self-contained waveform renderer handles all four states (idle, listening, thinking, speaking) with smooth lerp transitions. Six enhancement systems (noise, particles, trails, spring, asymmetry, chromatic) layer on top. All rendering is monochrome white — states are distinguished by motion intensity, not color.

**Tech Stack:** Canvas 2D API, requestAnimationFrame, pure-JS simplex noise (~20 lines)

**Spec:** `docs/superpowers/specs/2026-04-11-voice-waveform-design.md`

---

### Task 1: Simplify pill CSS — remove per-state colors

**Files:**
- Modify: `app/static/css/styles.css` (lines 1900-2053)

- [ ] **Step 1: Replace pill state themes with single dark background**

In `app/static/css/styles.css`, replace the entire block from `.voice-pill-bar {` (line 1900) through `.voice-pill-end:active {` (line 2053) with:

```css
/* ── Voice Pill Bar ── */
.voice-pill-bar {
    display: none;
    position: absolute;
    inset: 0;
    align-items: center;
    gap: 10px;
    border-radius: inherit;
    padding: 8px 8px 8px 16px;
    z-index: 5;
    transform: scaleX(0.6);
    opacity: 0;
    transform-origin: right center;
    transition: transform 300ms cubic-bezier(0.34, 1.56, 0.64, 1),
                opacity 200ms ease;
    background: rgba(12, 12, 16, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.06);
}

.voice-pill-bar.active {
    transform: scaleX(1);
    opacity: 1;
}

.voice-pill-bar.exiting {
    transform: scaleX(0.6);
    opacity: 0;
    transition: transform 200ms cubic-bezier(0.4, 0, 1, 1),
                opacity 150ms ease;
}

/* ── Label — opacity varies by state via JS ── */
.voice-pill-label {
    font-size: 12px;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.4);
    white-space: nowrap;
    letter-spacing: 0.03em;
    transition: opacity 300ms ease;
    flex-shrink: 0;
    user-select: none;
    text-transform: uppercase;
}

/* ── Canvas Waveform ── */
.voice-pill-wave {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 36px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    position: relative;
}

.voice-pill-wave canvas {
    width: 100%;
    height: 100%;
    display: block;
    will-change: transform;
}

/* ── End Button ── */
.voice-pill-end {
    background: #ef4444;
    color: white;
    border: none;
    border-radius: 20px;
    padding: 6px 16px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    line-height: 1;
    flex-shrink: 0;
    letter-spacing: 0.02em;
    transition: background 150ms ease, transform 100ms ease, box-shadow 200ms ease;
    box-shadow: 0 2px 8px rgba(239, 68, 68, 0.2);
}

.voice-pill-end:hover {
    background: #dc2626;
    box-shadow: 0 4px 16px rgba(239, 68, 68, 0.3), 0 0 12px rgba(239, 68, 68, 0.15);
}

.voice-pill-end:active {
    background: #b91c1c;
    transform: scale(0.96);
    box-shadow: 0 1px 4px rgba(239, 68, 68, 0.15);
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/styles.css
git commit -m "style: simplify voice pill to monochrome dark — remove per-state colors

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Replace DOM bars with canvas + simplex noise foundation

**Files:**
- Modify: `app/static/js/voiceModeModule.js` (lines 612-766)

This task replaces the pill creation and core animation loop. It adds the simplex noise implementation and basic waveform rendering for all four states.

- [ ] **Step 1: Replace the Pill Bar UI section**

In `app/static/js/voiceModeModule.js`, replace everything from the comment `// Pill Bar UI` (line 612) through the end of `_animatePill` (line 766, ending at `_pillAnimId = requestAnimationFrame((ts) => _animatePill(ts));` and the closing `}`) with the new implementation below.

Find and replace the block starting with:
```javascript
    // ────────────────────────────────────────
    // Pill Bar UI
    // ────────────────────────────────────────

    let _pillLabel = null;
    let _inputRow = null;
    const NUM_BARS = 40;
```

And ending with (inclusive):
```javascript
        _pillAnimId = requestAnimationFrame((ts) => _animatePill(ts));
    }
```

Replace with:

```javascript
    // ────────────────────────────────────────
    // Pill Bar UI — Canvas Waveform
    // ────────────────────────────────────────

    let _pillLabel = null;
    let _inputRow = null;
    let _canvas = null;
    let _ctx = null;
    let _dpr = 1;

    // ── Simplex Noise (lightweight 2D, ~20 lines) ──
    const _noiseP = new Uint8Array(512);
    const _noiseG = [[1,1],[-1,1],[1,-1],[-1,-1],[1,0],[-1,0],[0,1],[0,-1]];
    (function _initNoise() {
        const p = new Uint8Array(256);
        for (let i = 0; i < 256; i++) p[i] = i;
        for (let i = 255; i > 0; i--) { const j = (Math.random() * (i + 1)) | 0; [p[i], p[j]] = [p[j], p[i]]; }
        for (let i = 0; i < 512; i++) _noiseP[i] = p[i & 255];
    })();
    function _noise2D(x, y) {
        const F2 = 0.5 * (Math.sqrt(3) - 1), G2 = (3 - Math.sqrt(3)) / 6;
        const s = (x + y) * F2, i = Math.floor(x + s), j = Math.floor(y + s);
        const t = (i + j) * G2, x0 = x - (i - t), y0 = y - (j - t);
        const i1 = x0 > y0 ? 1 : 0, j1 = x0 > y0 ? 0 : 1;
        const x1 = x0 - i1 + G2, y1 = y0 - j1 + G2, x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
        const ii = i & 255, jj = j & 255;
        const dot = (gi, dx, dy) => { const g = _noiseG[gi % 8]; return g[0] * dx + g[1] * dy; };
        let n0 = 0, n1 = 0, n2 = 0;
        let t0 = 0.5 - x0 * x0 - y0 * y0;
        if (t0 > 0) { t0 *= t0; n0 = t0 * t0 * dot(_noiseP[ii + _noiseP[jj]], x0, y0); }
        let t1 = 0.5 - x1 * x1 - y1 * y1;
        if (t1 > 0) { t1 *= t1; n1 = t1 * t1 * dot(_noiseP[ii + i1 + _noiseP[jj + j1]], x1, y1); }
        let t2 = 0.5 - x2 * x2 - y2 * y2;
        if (t2 > 0) { t2 *= t2; n2 = t2 * t2 * dot(_noiseP[ii + 1 + _noiseP[jj + 1]], x2, y2); }
        return 70 * (n0 + n1 + n2); // returns -1..1
    }

    // ── Waveform State Parameters (lerp targets) ──
    const STATE_PARAMS = {
        idle:      { amp: 3,  freq: 0.8, noiseAmt: 0.15, opacity: 0.3, glowMul: 0.5 },
        listening: { amp: 28, freq: 1.2, noiseAmt: 0.30, opacity: 0.9, glowMul: 0.8 },
        thinking:  { amp: 10, freq: 1.5, noiseAmt: 0.25, opacity: 0.5, glowMul: 0.6 },
        speaking:  { amp: 24, freq: 2.5, noiseAmt: 0.40, opacity: 1.0, glowMul: 1.0 },
    };

    // Current interpolated params
    let _wp = { amp: 3, freq: 0.8, noiseAmt: 0.15, opacity: 0.3, glowMul: 0.5 };
    let _waveTime = 0;

    // Spring physics for listening decay
    let _springAmp = 0;
    let _springVel = 0;

    // Particle system (pool of 16)
    const _particles = Array.from({ length: 16 }, () => ({ x: 0, y: 0, vx: 0, vy: 0, life: 0, maxLife: 0 }));
    let _particleIdx = 0;

    // Ghost trail ring buffer (12 frames)
    const TRAIL_LEN = 12;
    let _trailBuf = [];
    let _trailFrame = 0;

    function _lerp(a, b, t) { return a + (b - a) * t; }

    function _createPill() {
        if (_pillEl) return;
        _inputRow = document.querySelector('.input-row');
        if (!_inputRow) return;

        const pill = document.createElement('div');
        pill.className = 'voice-pill-bar';
        pill.dataset.state = 'idle';

        // Left: status label
        const label = document.createElement('span');
        label.className = 'voice-pill-label';
        label.textContent = 'Listening...';
        _pillLabel = label;

        // Center: canvas waveform
        const waveContainer = document.createElement('div');
        waveContainer.className = 'voice-pill-wave';
        _canvas = document.createElement('canvas');
        waveContainer.appendChild(_canvas);

        // Right: End button
        const endBtn = document.createElement('button');
        endBtn.className = 'voice-pill-end';
        endBtn.innerHTML = '<i class="fas fa-xmark"></i>';
        endBtn.addEventListener('click', (e) => { e.stopPropagation(); exit(); });

        pill.appendChild(label);
        pill.appendChild(waveContainer);
        pill.appendChild(endBtn);

        _inputRow.appendChild(pill);
        _pillEl = pill;

        // Size canvas to container
        _resizeCanvas();
    }

    function _resizeCanvas() {
        if (!_canvas || !_canvas.parentElement) return;
        _dpr = window.devicePixelRatio || 1;
        const rect = _canvas.parentElement.getBoundingClientRect();
        _canvas.width = rect.width * _dpr;
        _canvas.height = rect.height * _dpr;
        _ctx = _canvas.getContext('2d');
        _ctx.scale(_dpr, _dpr);
    }

    // ── Spawn particles along waveform on state change ──
    function _burstParticles() {
        if (!_canvas) return;
        const w = _canvas.width / _dpr;
        const cy = (_canvas.height / _dpr) / 2;
        for (let i = 0; i < 8; i++) {
            const p = _particles[_particleIdx % _particles.length];
            _particleIdx++;
            p.x = Math.random() * w;
            p.y = cy + (Math.random() - 0.5) * _wp.amp * 1.5;
            p.vx = (Math.random() - 0.5) * 4;
            p.vy = (Math.random() - 0.5) * 12;
            p.life = 400; // ms
            p.maxLife = 400;
        }
    }

    // ── Build waveform points for one half ──
    function _buildWavePath(w, cy, time, amp, phaseOffset) {
        const points = [];
        const step = 8;
        for (let x = 0; x <= w; x += step) {
            const nx = x / w; // 0..1
            const target = _wp;

            // Base sine layers
            let val = Math.sin(2 * Math.PI * target.freq * time + nx * Math.PI * 2.5 + phaseOffset);
            if (currentState === 'speaking' || currentState === 'thinking') {
                val += 0.4 * Math.sin(2 * Math.PI * target.freq * 1.7 * time + nx * Math.PI * 3.5 + phaseOffset);
                val += 0.2 * Math.sin(2 * Math.PI * target.freq * 3.1 * time + nx * Math.PI * 1.8 + phaseOffset);
                val /= 1.6;
            }

            // Layer simplex noise
            val += _noise2D(nx * 3, time * 0.8 + phaseOffset) * target.noiseAmt;

            // Edge taper — fade to 0 at left/right edges
            const taper = Math.sin(nx * Math.PI);

            points.push({ x, y: cy + val * amp * taper });
        }
        return points;
    }

    // ── Draw smooth bezier curve through points ──
    function _drawCurve(ctx, points) {
        if (points.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length - 1; i++) {
            const xc = (points[i].x + points[i + 1].x) / 2;
            const yc = (points[i].y + points[i + 1].y) / 2;
            ctx.quadraticCurveTo(points[i].x, points[i].y, xc, yc);
        }
        const last = points[points.length - 1];
        ctx.lineTo(last.x, last.y);
        ctx.stroke();
    }

    // ── Main animation loop ──
    function _animatePill(timestamp) {
        if (!isActive || !_pillEl || !_ctx) return;

        const dt = 1 / 60;
        _waveTime += dt;

        // ── Lerp params toward current state target ──
        const target = STATE_PARAMS[currentState] || STATE_PARAMS.idle;
        const lerpSpeed = 0.08; // ~300ms to settle
        _wp.freq = _lerp(_wp.freq, target.freq, lerpSpeed);
        _wp.noiseAmt = _lerp(_wp.noiseAmt, target.noiseAmt, lerpSpeed);
        _wp.opacity = _lerp(_wp.opacity, target.opacity, lerpSpeed);
        _wp.glowMul = _lerp(_wp.glowMul, target.glowMul, lerpSpeed);

        // ── Amplitude: spring physics for listening, lerp for others ──
        let ampTarget = target.amp;
        if (currentState === 'listening') {
            const raw = getAudioLevel();
            ampTarget = Math.min(28, raw * 4 * 28);
            // Spring physics
            const springForce = 0.15;
            const damping = 0.85;
            _springVel += (ampTarget - _springAmp) * springForce;
            _springVel *= damping;
            _springAmp += _springVel;
            _wp.amp = Math.max(2, _springAmp);
            // Opacity scales with level
            _wp.opacity = _lerp(0.6, 0.9, Math.min(1, raw * 4));
        } else {
            _wp.amp = _lerp(_wp.amp, ampTarget, lerpSpeed);
            _springAmp = _wp.amp;
            _springVel = 0;
        }

        // ── Canvas dimensions ──
        const w = _canvas.width / _dpr;
        const h = _canvas.height / _dpr;
        const cy = h / 2;
        _ctx.clearRect(0, 0, w, h);

        // ── Build waveform paths ──
        const topPhase = 0;
        const bottomPhase = (currentState === 'speaking') ? Math.PI / 6 : 0;
        const topPoints = _buildWavePath(w, cy, _waveTime, -_wp.amp, topPhase);
        const bottomPoints = _buildWavePath(w, cy, _waveTime, _wp.amp, bottomPhase);

        // ── Ghost trail (12 frames, sampled every 2 frames) ──
        _trailFrame++;
        if (_trailFrame % 2 === 0) {
            _trailBuf.push({ top: topPoints.map(p => ({ ...p })), bottom: bottomPoints.map(p => ({ ...p })) });
            if (_trailBuf.length > TRAIL_LEN) _trailBuf.shift();
        }

        // Draw ghost trails
        for (let t = 0; t < _trailBuf.length; t++) {
            const ghostAlpha = 0.08 * (t + 1) / _trailBuf.length * _wp.opacity;
            _ctx.lineWidth = 1.5;
            _ctx.strokeStyle = `rgba(255, 255, 255, ${ghostAlpha.toFixed(3)})`;
            _drawCurve(_ctx, _trailBuf[t].top);
            _drawCurve(_ctx, _trailBuf[t].bottom);
        }

        // ── Chromatic aberration (speaking, amp > 0.7 of max) ──
        if (currentState === 'speaking' && _wp.amp > 24 * 0.7) {
            const abIntensity = (_wp.amp - 24 * 0.7) / (24 * 0.3); // 0..1
            const abAlpha = 0.12 * abIntensity;

            // Red shift (-1px)
            _ctx.save();
            _ctx.translate(-1, 0);
            _ctx.lineWidth = 2;
            _ctx.strokeStyle = `rgba(255, 40, 40, ${abAlpha.toFixed(3)})`;
            _drawCurve(_ctx, topPoints);
            _drawCurve(_ctx, bottomPoints);
            _ctx.restore();

            // Blue shift (+1px)
            _ctx.save();
            _ctx.translate(1, 0);
            _ctx.lineWidth = 2;
            _ctx.strokeStyle = `rgba(40, 40, 255, ${abAlpha.toFixed(3)})`;
            _drawCurve(_ctx, topPoints);
            _drawCurve(_ctx, bottomPoints);
            _ctx.restore();
        }

        // ── Draw main waveform (3-layer glow) ──
        // Outer glow
        _ctx.lineWidth = 8;
        _ctx.strokeStyle = `rgba(200, 220, 255, ${(0.08 * _wp.glowMul).toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        // Inner glow
        _ctx.lineWidth = 4;
        _ctx.strokeStyle = `rgba(255, 255, 255, ${(0.15 * _wp.glowMul).toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        // Core line
        _ctx.lineWidth = 2;
        _ctx.strokeStyle = `rgba(255, 255, 255, ${_wp.opacity.toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        // ── Particles ──
        const now = performance.now();
        for (const p of _particles) {
            if (p.life <= 0) continue;
            p.x += p.vx;
            p.y += p.vy;
            p.vy *= 0.96; // drag
            p.vx *= 0.98;
            p.life -= 16.67; // ~1 frame at 60fps
            const alpha = Math.max(0, (p.life / p.maxLife) * 0.8);
            _ctx.beginPath();
            _ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
            _ctx.fillStyle = `rgba(255, 255, 255, ${alpha.toFixed(3)})`;
            _ctx.fill();
        }

        // ── Label opacity per state ──
        if (_pillLabel) {
            const labelOpacity = { idle: 0.35, listening: 0.6, thinking: 0.5, speaking: 0.7 }[currentState] || 0.35;
            _pillLabel.style.opacity = labelOpacity;
        }

        _pillAnimId = requestAnimationFrame((ts) => _animatePill(ts));
    }
```

- [ ] **Step 2: Update `_showPill` to resize canvas and reset trail**

Find the `_showPill` function and replace it:

```javascript
    function _showPill() {
        _createPill();
        if (!_pillEl) return;

        if (_inputRow) _inputRow.classList.add('voice-active');

        _pillEl.style.display = 'flex';
        _pillEl.offsetHeight; // reflow
        requestAnimationFrame(() => { _pillEl.classList.add('active'); });

        // Reset state
        _waveTime = 0;
        _springAmp = 0;
        _springVel = 0;
        _trailBuf = [];
        _trailFrame = 0;
        _wp = { ...STATE_PARAMS.idle };

        // Resize canvas after it's visible
        requestAnimationFrame(() => { _resizeCanvas(); _animatePill(); });
    }
```

- [ ] **Step 3: Update `_hidePill` — no bar references**

Find the `_hidePill` function and replace it:

```javascript
    function _hidePill() {
        if (_pillAnimId) { cancelAnimationFrame(_pillAnimId); _pillAnimId = null; }

        if (_pillEl) {
            _pillEl.classList.remove('active');
            _pillEl.classList.add('exiting');
            setTimeout(() => {
                if (_pillEl) { _pillEl.style.display = 'none'; _pillEl.classList.remove('exiting'); }
            }, 200);
        }

        setTimeout(() => {
            if (_inputRow) _inputRow.classList.remove('voice-active');
            if (window.updateSendButtonState) window.updateSendButtonState();
        }, 200);
    }
```

- [ ] **Step 4: Remove old bar/wave variables**

Search for and remove these lines if they still exist after the replacement:
- `const NUM_BARS = 40;`
- `let _pillBars = [];`
- `const WAVE_HISTORY_LEN = NUM_BARS;`
- `let _waveHistory = new Array(...).fill(0);`
- `let _lastWaveUpdate = 0;`
- `const WAVE_UPDATE_MS = 80;`
- `const MAX_BAR_H = 28;`
- `const MIN_BAR_H = 2;`

These were defined outside the replaced block and may need manual cleanup.

- [ ] **Step 5: Wire particle burst into setState**

Find the `setState` function (around line 779 after edits). Add the particle burst call right after `currentState = state;`:

Change:
```javascript
    function setState(state) {
        currentState = state;

        // Update pill visuals
```

To:
```javascript
    function setState(state) {
        const prev = currentState;
        currentState = state;

        // Particle burst on state transition
        if (prev !== state && isActive) _burstParticles();

        // Update pill visuals
```

- [ ] **Step 6: Commit**

```bash
git add app/static/js/voiceModeModule.js
git commit -m "feat: canvas waveform with simplex noise, particles, trails, spring physics

Replaced 40 DOM bars with Canvas 2D waveform renderer.
Six enhancements: simplex noise, particle bursts, ghost trails,
spring physics decay, asymmetric speaking, chromatic aberration.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Bump cache version and test

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Bump voiceModeModule.js cache version**

In `app/templates/index.html`, find:
```html
js/voiceModeModule.js') }}?v=16"
```

Change to:
```html
js/voiceModeModule.js') }}?v=17"
```

- [ ] **Step 2: Restart and test**

```bash
sudo systemctl restart xelasphere
```

Open the browser with hard refresh (`Ctrl+Shift+R`). Enter voice mode. Verify:

1. **Idle**: gentle breathing waveform, low opacity, subtle noise
2. **Listening**: waveform surges with voice input, spring decay on silence
3. **Thinking**: rhythmic traveling pulse
4. **Speaking**: energetic multi-frequency wave, asymmetric top/bottom, chromatic aberration flicker at peaks
5. **Transitions**: particle burst (8 white dots exploding outward) on every state change
6. **Trails**: ghost lines trailing behind the waveform, creating motion blur
7. **Performance**: smooth 60fps, no jank
8. **Pill**: dark background for all states, no color changes, white waveform only

- [ ] **Step 3: Commit**

```bash
git add app/templates/index.html
git commit -m "chore: bump voiceModeModule cache version for waveform redesign

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
