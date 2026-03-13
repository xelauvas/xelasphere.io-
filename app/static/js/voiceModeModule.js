/**
 * Voice Mode Module - Premium WebGL Orb UI
 * Volumetric cloud sphere with waveform visualization
 */
const VoiceModeModule = (function() {
    'use strict';

    // DOM Elements
    let overlay = null;
    let canvasBg = null;
    let canvasOrb = null;
    let canvasFx = null;
    let ctxBg = null;
    let ctxFx = null;
    let statusText = null;
    let primaryBtn = null;
    let muteBtn = null;

    // WebGL
    let gl = null;
    let orbProgram = null;
    let orbBuffer = null;
    let orbUniforms = {};

    // State
    let isActive = false;
    let currentState = 'idle';
    let isMuted = false;
    let animationId = null;

    // Audio
    let audioContext = null;
    let analyser = null;
    let stream = null;
    let audioLevel = 0;
    let smoothedLevel = 0;
    let frequencyData = null;

    // Animation
    let time = 0;
    let lastTime = 0;

    // Orb parameters
    const orb = {
        x: 0,
        y: 0,
        radius: 0,
        baseRadius: 0
    };

    // Waveform history for smooth animation
    const waveHistory = new Array(128).fill(0);

    // Status labels
    const statusLabels = {
        idle: 'Tap to speak',
        listening: 'Listening...',
        thinking: 'Thinking...',
        speaking: 'Speaking...'
    };

    // WebGL Shaders
    const vertexShaderSource = `
        attribute vec2 a_position;
        varying vec2 v_uv;
        void main() {
            v_uv = a_position * 0.5 + 0.5;
            gl_Position = vec4(a_position, 0.0, 1.0);
        }
    `;

    const fragmentShaderSource = `
        precision highp float;

        varying vec2 v_uv;
        uniform float u_time;
        uniform float u_audioLevel;
        uniform vec2 u_resolution;
        uniform vec2 u_orbCenter;
        uniform float u_orbRadius;
        uniform int u_state; // 0=idle, 1=listening, 2=thinking, 3=speaking

        // Simplex noise functions
        vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
        vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
        vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

        float snoise(vec3 v) {
            const vec2 C = vec2(1.0/6.0, 1.0/3.0);
            const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

            vec3 i = floor(v + dot(v, C.yyy));
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

            vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

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

            vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
            p0 *= norm.x;
            p1 *= norm.y;
            p2 *= norm.z;
            p3 *= norm.w;

            vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
            m = m * m;
            return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
        }

        // Fractal Brownian Motion
        float fbm(vec3 p, int octaves) {
            float value = 0.0;
            float amplitude = 0.5;
            float frequency = 1.0;
            for (int i = 0; i < 6; i++) {
                if (i >= octaves) break;
                value += amplitude * snoise(p * frequency);
                frequency *= 2.0;
                amplitude *= 0.5;
            }
            return value;
        }

        void main() {
            vec2 fragCoord = v_uv * u_resolution;
            vec2 toOrb = fragCoord - u_orbCenter;
            float dist = length(toOrb);
            float normDist = dist / u_orbRadius;

            // Outside orb - transparent
            if (normDist > 1.0) {
                discard;
            }

            // Calculate 3D position on sphere surface
            float z = sqrt(1.0 - normDist * normDist);
            vec3 spherePos = vec3(toOrb / u_orbRadius, z);

            // Time-based animation speeds
            float timeScale = u_state == 2 ? 0.8 : (u_state == 3 ? 1.2 : 0.5);
            float t = u_time * timeScale;

            // Cloud noise layers
            vec3 noisePos = spherePos * 2.0;
            noisePos.x += t * 0.15;
            noisePos.y -= t * 0.1;
            noisePos.z += sin(t * 0.2) * 0.3;

            float cloud1 = fbm(noisePos, 5);
            float cloud2 = fbm(noisePos * 1.5 + vec3(100.0, 0.0, t * 0.08), 4);
            float cloud3 = fbm(noisePos * 2.5 + vec3(0.0, 50.0, -t * 0.12), 3);

            // Combine clouds with audio reactivity
            float audioBoost = 1.0 + u_audioLevel * 0.5;
            float cloudMix = cloud1 * 0.5 + cloud2 * 0.35 + cloud3 * 0.15;
            cloudMix = cloudMix * 0.5 + 0.5; // Normalize to 0-1
            cloudMix = pow(cloudMix, 1.2) * audioBoost;

            // Color palette (blue, cyan, violet)
            vec3 colorDeep = vec3(0.08, 0.15, 0.35);    // Deep blue
            vec3 colorMid = vec3(0.2, 0.45, 0.8);       // Blue
            vec3 colorLight = vec3(0.4, 0.7, 0.95);     // Cyan
            vec3 colorHighlight = vec3(0.6, 0.5, 0.9);  // Violet
            vec3 colorPeak = vec3(0.85, 0.9, 1.0);      // White-blue

            // State-based color tinting
            if (u_state == 2) { // Thinking - more violet
                colorMid = mix(colorMid, vec3(0.4, 0.3, 0.7), 0.3);
                colorLight = mix(colorLight, vec3(0.6, 0.5, 0.9), 0.3);
            } else if (u_state == 3) { // Speaking - warmer
                colorMid = mix(colorMid, vec3(0.5, 0.4, 0.7), 0.2);
                colorHighlight = mix(colorHighlight, vec3(0.8, 0.6, 0.7), 0.25);
            }

            // Build final color from cloud density
            vec3 color = colorDeep;
            color = mix(color, colorMid, smoothstep(0.2, 0.5, cloudMix));
            color = mix(color, colorLight, smoothstep(0.5, 0.75, cloudMix));
            color = mix(color, colorHighlight, smoothstep(0.7, 0.9, cloudMix));
            color = mix(color, colorPeak, smoothstep(0.88, 1.0, cloudMix));

            // Lighting - upper right highlight
            vec3 lightDir = normalize(vec3(0.5, 0.6, 0.8));
            float lighting = dot(spherePos, lightDir);
            lighting = lighting * 0.5 + 0.5;
            lighting = pow(lighting, 0.8);

            // Apply lighting
            color = mix(color * 0.6, color * 1.3, lighting);

            // Rim lighting
            float rim = 1.0 - z;
            rim = pow(rim, 2.5);
            vec3 rimColor = vec3(0.5, 0.7, 1.0);
            color += rimColor * rim * 0.6;

            // Depth-based density (thicker in center)
            float density = z * 0.3 + 0.7;
            color *= density;

            // Edge softness
            float edgeSoft = smoothstep(1.0, 0.92, normDist);

            // Final alpha
            float alpha = edgeSoft * (0.9 + cloudMix * 0.1);

            gl_FragColor = vec4(color, alpha);
        }
    `;

    function createOverlay() {
        overlay = document.createElement('div');
        overlay.className = 'vm-overlay';
        overlay.dataset.state = 'idle';
        overlay.innerHTML = `
            <div class="vm-topbar">
                <div class="vm-window-controls">
                    <span class="vm-window-dot vm-window-dot--close"></span>
                    <span class="vm-window-dot vm-window-dot--minimize"></span>
                    <span class="vm-window-dot vm-window-dot--maximize"></span>
                </div>
                <span class="vm-logo">XelaSphere</span>
                <button class="vm-close-btn" aria-label="Close voice mode">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12"/>
                    </svg>
                </button>
            </div>

            <canvas class="vm-canvas-bg"></canvas>
            <canvas class="vm-canvas-orb"></canvas>
            <canvas class="vm-canvas-fx"></canvas>

            <div class="vm-status">
                <p class="vm-status-text">Tap to speak</p>
            </div>

            <div class="vm-controlbar">
                <button class="vm-ctrl vm-ctrl--mic" aria-label="Microphone">
                    <svg viewBox="0 0 24 24">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                        <line x1="12" y1="19" x2="12" y2="22"/>
                    </svg>
                </button>
                <button class="vm-ctrl vm-ctrl--attach" aria-label="Attach file">
                    <svg viewBox="0 0 24 24">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        <circle cx="8.5" cy="8.5" r="1.5"/>
                        <polyline points="21 15 16 10 5 21"/>
                    </svg>
                </button>
                <div class="vm-divider"></div>
                <button class="vm-ctrl vm-ctrl--primary" aria-label="Voice input">
                    <svg viewBox="0 0 24 24">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" stroke="none"/>
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                        <line x1="12" y1="19" x2="12" y2="22"/>
                        <line x1="8" y1="22" x2="16" y2="22"/>
                    </svg>
                </button>
                <div class="vm-divider"></div>
                <button class="vm-ctrl vm-ctrl--mute" aria-label="Mute">
                    <svg viewBox="0 0 24 24">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                        <line x1="23" y1="9" x2="17" y2="15"/>
                        <line x1="17" y1="9" x2="23" y2="15"/>
                    </svg>
                </button>
                <button class="vm-ctrl vm-ctrl--settings" aria-label="Settings">
                    <svg viewBox="0 0 24 24">
                        <line x1="4" y1="21" x2="4" y2="14"/>
                        <line x1="4" y1="10" x2="4" y2="3"/>
                        <line x1="12" y1="21" x2="12" y2="12"/>
                        <line x1="12" y1="8" x2="12" y2="3"/>
                        <line x1="20" y1="21" x2="20" y2="16"/>
                        <line x1="20" y1="12" x2="20" y2="3"/>
                        <line x1="1" y1="14" x2="7" y2="14"/>
                        <line x1="9" y1="8" x2="15" y2="8"/>
                        <line x1="17" y1="16" x2="23" y2="16"/>
                    </svg>
                </button>
            </div>
        `;

        document.body.appendChild(overlay);

        // Get elements
        canvasBg = overlay.querySelector('.vm-canvas-bg');
        canvasOrb = overlay.querySelector('.vm-canvas-orb');
        canvasFx = overlay.querySelector('.vm-canvas-fx');
        ctxBg = canvasBg.getContext('2d');
        ctxFx = canvasFx.getContext('2d');
        statusText = overlay.querySelector('.vm-status-text');
        primaryBtn = overlay.querySelector('.vm-ctrl--primary');
        muteBtn = overlay.querySelector('.vm-ctrl--mute');

        // Initialize WebGL
        initWebGL();

        // Event listeners
        resize();
        window.addEventListener('resize', resize);
        canvasFx.addEventListener('click', onCanvasClick);
        overlay.querySelector('.vm-close-btn').addEventListener('click', exit);
        primaryBtn.addEventListener('click', onPrimaryClick);
        muteBtn.addEventListener('click', toggleMute);
        document.addEventListener('keydown', onKeyDown);
    }

    function initWebGL() {
        gl = canvasOrb.getContext('webgl', {
            alpha: true,
            premultipliedAlpha: false,
            antialias: true
        });

        if (!gl) {
            console.warn('WebGL not supported, falling back to 2D');
            return;
        }

        // Create shaders
        const vertexShader = createShader(gl, gl.VERTEX_SHADER, vertexShaderSource);
        const fragmentShader = createShader(gl, gl.FRAGMENT_SHADER, fragmentShaderSource);

        if (!vertexShader || !fragmentShader) return;

        // Create program
        orbProgram = gl.createProgram();
        gl.attachShader(orbProgram, vertexShader);
        gl.attachShader(orbProgram, fragmentShader);
        gl.linkProgram(orbProgram);

        if (!gl.getProgramParameter(orbProgram, gl.LINK_STATUS)) {
            console.error('Program link error:', gl.getProgramInfoLog(orbProgram));
            return;
        }

        // Get uniform locations
        orbUniforms = {
            time: gl.getUniformLocation(orbProgram, 'u_time'),
            audioLevel: gl.getUniformLocation(orbProgram, 'u_audioLevel'),
            resolution: gl.getUniformLocation(orbProgram, 'u_resolution'),
            orbCenter: gl.getUniformLocation(orbProgram, 'u_orbCenter'),
            orbRadius: gl.getUniformLocation(orbProgram, 'u_orbRadius'),
            state: gl.getUniformLocation(orbProgram, 'u_state')
        };

        // Create vertex buffer (full-screen quad)
        orbBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, orbBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
            -1, -1, 1, -1, -1, 1,
            -1, 1, 1, -1, 1, 1
        ]), gl.STATIC_DRAW);

        // Enable blending
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    }

    function createShader(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);

        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            console.error('Shader compile error:', gl.getShaderInfoLog(shader));
            gl.deleteShader(shader);
            return null;
        }

        return shader;
    }

    function resize() {
        const dpr = window.devicePixelRatio || 1;
        const w = window.innerWidth;
        const h = window.innerHeight;

        // Resize all canvases
        [canvasBg, canvasOrb, canvasFx].forEach(canvas => {
            canvas.width = w * dpr;
            canvas.height = h * dpr;
            canvas.style.width = w + 'px';
            canvas.style.height = h + 'px';
        });

        // Scale 2D contexts
        ctxBg.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctxFx.setTransform(dpr, 0, 0, dpr, 0, 0);

        // Update WebGL viewport
        if (gl) {
            gl.viewport(0, 0, canvasOrb.width, canvasOrb.height);
        }

        // Calculate orb dimensions
        const minDim = Math.min(w, h);
        orb.baseRadius = minDim * 0.14;
        orb.radius = orb.baseRadius;
        orb.x = w / 2;
        orb.y = h / 2;
    }

    function onKeyDown(e) {
        if (!isActive) return;
        if (e.key === 'Escape') exit();
        if (e.key === ' ') {
            e.preventDefault();
            onCanvasClick();
        }
    }

    function onCanvasClick() {
        if (currentState === 'idle') {
            startListening();
        } else if (currentState === 'listening') {
            // Demo: cycle through states
            setState('thinking');
            setTimeout(() => {
                if (currentState === 'thinking' && isActive) {
                    setState('speaking');
                    setTimeout(() => {
                        if (currentState === 'speaking' && isActive) {
                            startListening();
                        }
                    }, 2500);
                }
            }, 1800);
        }
    }

    function onPrimaryClick(e) {
        e.stopPropagation();
        if (currentState === 'idle') {
            startListening();
        } else {
            setState('idle');
        }
    }

    function toggleMute(e) {
        e.stopPropagation();
        isMuted = !isMuted;
        muteBtn.classList.toggle('muted', isMuted);
        if (stream) {
            stream.getAudioTracks().forEach(t => t.enabled = !isMuted);
        }
    }

    function enter() {
        if (isActive) return;
        isActive = true;
        if (!overlay) createOverlay();

        time = 0;
        lastTime = performance.now();
        smoothedLevel = 0;
        waveHistory.fill(0);

        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        setState('idle');
        animate();

        setTimeout(() => {
            if (isActive && currentState === 'idle') {
                startListening();
            }
        }, 700);
    }

    function exit() {
        if (!isActive) return;
        isActive = false;
        stopListening();
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }
        overlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    function setState(state) {
        currentState = state;
        if (overlay) overlay.dataset.state = state;
        if (statusText) statusText.textContent = statusLabels[state] || statusLabels.idle;
        if (primaryBtn) primaryBtn.classList.toggle('active', state === 'listening');
        if (state !== 'listening') stopListening();
    }

    async function startListening() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.8;
            frequencyData = new Uint8Array(analyser.frequencyBinCount);

            const source = audioContext.createMediaStreamSource(stream);
            source.connect(analyser);

            if (isMuted) {
                stream.getAudioTracks().forEach(t => t.enabled = false);
            }

            setState('listening');
        } catch (e) {
            console.error('Microphone error:', e);
            setState('idle');
        }
    }

    function stopListening() {
        if (stream) {
            stream.getTracks().forEach(t => t.stop());
            stream = null;
        }
        if (audioContext) {
            audioContext.close().catch(() => {});
            audioContext = null;
            analyser = null;
        }
    }

    function getAudioLevel() {
        if (!analyser || isMuted) return 0;
        analyser.getByteFrequencyData(frequencyData);
        let sum = 0;
        for (let i = 0; i < frequencyData.length; i++) {
            sum += frequencyData[i];
        }
        return sum / (frequencyData.length * 255);
    }

    function animate(timestamp) {
        if (!isActive) return;

        const now = timestamp || performance.now();
        const delta = Math.min(0.05, (now - lastTime) / 1000);
        lastTime = now;
        time += delta;

        // Update audio level
        if (currentState === 'listening') {
            audioLevel = getAudioLevel();
        } else if (currentState === 'speaking') {
            audioLevel = 0.35 + Math.sin(time * 5) * 0.2 + Math.sin(time * 8) * 0.1;
        } else if (currentState === 'thinking') {
            audioLevel = 0.15 + Math.sin(time * 2) * 0.08;
        } else {
            audioLevel = 0.05 + Math.sin(time * 0.8) * 0.03;
        }
        smoothedLevel += (audioLevel - smoothedLevel) * 0.12;

        // Update wave history
        waveHistory.shift();
        waveHistory.push(smoothedLevel);

        // Update orb size with breathing
        const breathSpeed = currentState === 'idle' ? 0.5 : currentState === 'thinking' ? 0.9 : 1.3;
        const breathAmp = currentState === 'idle' ? 0.02 : 0.04;
        const breath = Math.sin(time * breathSpeed) * breathAmp;
        const audioScale = smoothedLevel * 0.15;
        orb.radius = orb.baseRadius * (1 + breath + audioScale);

        // Render
        drawBackground();
        drawOrb();
        drawEffects();

        animationId = requestAnimationFrame(animate);
    }

    function drawBackground() {
        const w = window.innerWidth;
        const h = window.innerHeight;

        ctxBg.clearRect(0, 0, w, h);

        // Subtle radial gradient enhancement
        const gradient = ctxBg.createRadialGradient(orb.x, orb.y, 0, orb.x, orb.y, Math.max(w, h) * 0.6);
        gradient.addColorStop(0, 'rgba(20, 40, 80, 0.15)');
        gradient.addColorStop(0.4, 'rgba(10, 20, 40, 0.08)');
        gradient.addColorStop(1, 'transparent');
        ctxBg.fillStyle = gradient;
        ctxBg.fillRect(0, 0, w, h);
    }

    function drawOrb() {
        if (!gl || !orbProgram) return;

        const dpr = window.devicePixelRatio || 1;

        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);

        gl.useProgram(orbProgram);

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

        // Draw
        const posLoc = gl.getAttribLocation(orbProgram, 'a_position');
        gl.bindBuffer(gl.ARRAY_BUFFER, orbBuffer);
        gl.enableVertexAttribArray(posLoc);
        gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    function drawEffects() {
        const w = window.innerWidth;
        const h = window.innerHeight;

        ctxFx.clearRect(0, 0, w, h);

        drawHaloRings();
        drawWaveform();
        drawOrbGlow();
        drawRimHighlight();
    }

    function drawHaloRings() {
        const numRings = 4;
        const baseAlpha = currentState === 'idle' ? 0.06 : 0.1;

        for (let i = 0; i < numRings; i++) {
            const progress = (time * 0.15 + i * 0.25) % 1;
            const ringRadius = orb.radius * (1.3 + progress * 1.2);
            const alpha = baseAlpha * (1 - progress) * (1 + smoothedLevel * 0.5);

            ctxFx.beginPath();
            ctxFx.arc(orb.x, orb.y, ringRadius, 0, Math.PI * 2);
            ctxFx.strokeStyle = `rgba(100, 160, 255, ${alpha})`;
            ctxFx.lineWidth = 1.5 - progress;
            ctxFx.stroke();
        }
    }

    function drawWaveform() {
        const w = window.innerWidth;
        const waveWidth = w * 0.9;
        const waveHeight = 60;
        const startX = (w - waveWidth) / 2;
        const segments = 128;

        ctxFx.save();
        ctxFx.globalCompositeOperation = 'screen';

        // Multiple wave layers
        for (let layer = 0; layer < 3; layer++) {
            const layerOffset = layer * 0.3;
            const layerAlpha = (0.15 - layer * 0.04) * (1 + smoothedLevel * 2);

            ctxFx.beginPath();

            for (let i = 0; i <= segments; i++) {
                const x = startX + (i / segments) * waveWidth;
                const t = i / segments;

                // Combine multiple sine waves
                const wave1 = Math.sin(t * Math.PI * 4 + time * 2 + layerOffset) * 0.4;
                const wave2 = Math.sin(t * Math.PI * 6 - time * 1.5 + layerOffset) * 0.25;
                const wave3 = Math.sin(t * Math.PI * 10 + time * 3 + layerOffset) * 0.15;

                // Audio reactivity from history
                const histIndex = Math.floor(t * (waveHistory.length - 1));
                const audioWave = waveHistory[histIndex] * 2;

                const amplitude = waveHeight * (0.3 + audioWave * 0.7);
                const y = orb.y + (wave1 + wave2 + wave3) * amplitude;

                // Fade at edges
                const edgeFade = Math.sin(t * Math.PI);
                const finalY = orb.y + (y - orb.y) * edgeFade;

                if (i === 0) ctxFx.moveTo(x, finalY);
                else ctxFx.lineTo(x, finalY);
            }

            const gradient = ctxFx.createLinearGradient(startX, 0, startX + waveWidth, 0);
            gradient.addColorStop(0, 'transparent');
            gradient.addColorStop(0.2, `rgba(80, 150, 255, ${layerAlpha})`);
            gradient.addColorStop(0.5, `rgba(100, 180, 255, ${layerAlpha * 1.2})`);
            gradient.addColorStop(0.8, `rgba(80, 150, 255, ${layerAlpha})`);
            gradient.addColorStop(1, 'transparent');

            ctxFx.strokeStyle = gradient;
            ctxFx.lineWidth = 2 - layer * 0.5;
            ctxFx.stroke();
        }

        ctxFx.restore();
    }

    function drawOrbGlow() {
        const glowRadius = orb.radius * 2.5;
        const intensity = 0.25 + smoothedLevel * 0.2;

        const gradient = ctxFx.createRadialGradient(
            orb.x, orb.y, orb.radius * 0.8,
            orb.x, orb.y, glowRadius
        );

        // State-based glow color
        let glowColor;
        if (currentState === 'thinking') {
            glowColor = `rgba(140, 100, 200, ${intensity})`;
        } else if (currentState === 'speaking') {
            glowColor = `rgba(150, 120, 200, ${intensity})`;
        } else {
            glowColor = `rgba(80, 140, 220, ${intensity})`;
        }

        gradient.addColorStop(0, glowColor);
        gradient.addColorStop(0.4, glowColor.replace(/[\d.]+\)$/, `${intensity * 0.4})`));
        gradient.addColorStop(1, 'transparent');

        ctxFx.fillStyle = gradient;
        ctxFx.beginPath();
        ctxFx.arc(orb.x, orb.y, glowRadius, 0, Math.PI * 2);
        ctxFx.fill();
    }

    function drawRimHighlight() {
        // Bright rim on upper right
        const rimGradient = ctxFx.createRadialGradient(
            orb.x - orb.radius * 0.4,
            orb.y - orb.radius * 0.4,
            orb.radius * 0.6,
            orb.x,
            orb.y,
            orb.radius * 1.1
        );

        rimGradient.addColorStop(0, 'rgba(200, 230, 255, 0.35)');
        rimGradient.addColorStop(0.5, 'rgba(150, 200, 255, 0.15)');
        rimGradient.addColorStop(1, 'transparent');

        ctxFx.save();
        ctxFx.beginPath();
        ctxFx.arc(orb.x, orb.y, orb.radius * 1.05, 0, Math.PI * 2);
        ctxFx.fillStyle = rimGradient;
        ctxFx.fill();
        ctxFx.restore();

        // Thin rim outline
        ctxFx.beginPath();
        ctxFx.arc(orb.x, orb.y, orb.radius, 0, Math.PI * 2);
        ctxFx.strokeStyle = 'rgba(180, 220, 255, 0.4)';
        ctxFx.lineWidth = 1.5;
        ctxFx.stroke();
    }

    // Public API
    return {
        enter,
        exit,
        setState,
        isActive: () => isActive,
        getState: () => currentState
    };
})();

window.VoiceModeModule = VoiceModeModule;
