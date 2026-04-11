/**
 * Voice Mode Module - Living Organism Orb
 * Metaball SDF lava lamp with audio-reactive deformation
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
    let _resizeHandler = null;
    let _keydownHandler = null;

    // Audio
    let audioContext = null;
    let analyser = null;
    let stream = null;
    let audioLevel = 0;
    let smoothedLevel = 0;
    let frequencyData = null;

    // Shared playback context — created once on user gesture, reused for all TTS
    let playbackCtx = null;

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
    const blobPosData = new Float32Array(NUM_BLOBS * 2);
    const blobRadData = new Float32Array(NUM_BLOBS);

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

        // ── FBM ──
        float fbm(vec3 p) {
            float v = 0.0, a = 0.5, f = 1.0;
            for (int i = 0; i < 5; i++) {
                v += a * snoise(p * f);
                f *= 2.1;
                a *= 0.47;
            }
            return v;
        }

        float metaballField(vec2 p) {
            float field = 0.0;
            for (int i = 0; i < 6; i++) {
                vec2 d = p - u_blobPos[i];
                float r2 = u_blobRad[i] * u_blobRad[i];
                float dist2 = dot(d, d) + 0.001;
                field += r2 / dist2;
            }
            return field;
        }

        void main() {
            vec2 fragCoord = v_uv * u_resolution;
            vec2 toOrb = fragCoord - u_orbCenter;
            vec2 p = toOrb / u_orbRadius;
            float dist = length(p);
            if (dist > 1.8) { discard; return; }

            float stateSpeed = u_state == 0 ? 0.30 : u_state == 1 ? 0.55 : u_state == 2 ? 0.95 : 1.40;
            float t = u_time * stateSpeed;

            float field = metaballField(p);
            float threshold = 1.8;
            float softness = 0.3;

            // Outside: glow
            if (field < threshold - softness) {
                float glowStrength = field / (threshold - softness);
                float glow = pow(glowStrength, 4.0) * (0.08 + u_audioLevel * 0.15);
                if (glow < 0.003) { discard; return; }
                vec3 glowColor;
                if (u_state == 0)      glowColor = vec3(0.15, 0.30, 0.65);
                else if (u_state == 1) glowColor = vec3(0.15, 0.48, 0.88);
                else if (u_state == 2) glowColor = vec3(0.38, 0.20, 0.72);
                else                   glowColor = vec3(0.35, 0.52, 0.88);
                gl_FragColor = vec4(glowColor, glow);
                return;
            }

            // Edge
            float edgeAlpha = smoothstep(threshold - softness, threshold + softness, field);

            // State palettes
            vec3 cDark, cMid, cBright, rimColor;
            if (u_state == 0) { cDark=vec3(0.04,0.08,0.28); cMid=vec3(0.10,0.22,0.55); cBright=vec3(0.22,0.40,0.72); rimColor=vec3(0.15,0.30,0.65); }
            else if (u_state == 1) { cDark=vec3(0.04,0.14,0.35); cMid=vec3(0.08,0.38,0.78); cBright=vec3(0.25,0.60,0.95); rimColor=vec3(0.15,0.48,0.88); }
            else if (u_state == 2) { cDark=vec3(0.10,0.04,0.28); cMid=vec3(0.28,0.14,0.58); cBright=vec3(0.48,0.30,0.80); rimColor=vec3(0.38,0.20,0.72); }
            else { cDark=vec3(0.08,0.14,0.35); cMid=vec3(0.22,0.42,0.78); cBright=vec3(0.50,0.65,0.95); rimColor=vec3(0.35,0.52,0.88); }

            // Color currents
            vec3 sp1 = vec3(p * 1.5, 0.0) + vec3(t*0.10, -t*0.07, t*0.05);
            sp1.xy += p * u_audioLevel * 0.8;
            float n1 = fbm(sp1);
            vec3 sp2 = vec3(p * 3.0, 0.0) + vec3(-t*0.15, t*0.12, -t*0.08) + 4.2;
            sp2.xy += p * u_audioLevel * 1.0;
            float n2 = fbm(sp2);
            float flow = n1 * 0.70 + n2 * 0.30;
            flow = flow * 0.5 + 0.5;
            flow = smoothstep(0.15, 0.85, flow); // wider range = more visible contrast
            flow *= (0.85 + u_audioLevel * 0.6);
            flow = clamp(flow, 0.0, 1.0);

            vec3 color = mix(cDark, cMid, smoothstep(0.0, 0.5, flow));
            color = mix(color, cBright, smoothstep(0.4, 1.0, flow));

            // Rim
            float edgeProximity = 1.0 - smoothstep(threshold, threshold + 2.5, field);
            color += rimColor * pow(edgeProximity, 1.8) * (0.4 + u_audioLevel * 0.5);

            gl_FragColor = vec4(color, edgeAlpha);
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

        // Event listeners (store refs for cleanup in exit())
        _resizeHandler = resize;
        _keydownHandler = onKeyDown;
        resize();
        window.addEventListener('resize', _resizeHandler);
        canvasFx.addEventListener('click', onCanvasClick);
        overlay.querySelector('.vm-close-btn').addEventListener('click', exit);
        primaryBtn.addEventListener('click', onPrimaryClick);
        muteBtn.addEventListener('click', toggleMute);
        document.addEventListener('keydown', _keydownHandler);
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
            state: gl.getUniformLocation(orbProgram, 'u_state'),
            blobPos: gl.getUniformLocation(orbProgram, 'u_blobPos'),
            blobRad: gl.getUniformLocation(orbProgram, 'u_blobRad')
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

    // Voice conversation: record → transcribe → send → TTS → loop
    let mediaRecorder = null;
    let recordedChunks = [];

    // ── Smart VAD (Voice Activity Detection) ──
    let vadTimer = null;
    let hasSpoken = false;
    let lastSpeechTime = 0;
    let speechStartTime = 0;
    let noiseFloor = 0.02;

    const VAD_MIN_SPEECH_MS = 300;
    const VAD_SILENCE_MS = 1200;
    const VAD_CALIBRATE_MS = 300;
    const VAD_INTERVAL = 50;

    function startSilenceDetection() {
        hasSpoken = false;
        lastSpeechTime = 0;
        speechStartTime = 0;
        noiseFloor = 0;
        let calibrateSamples = 0;
        const calibrateStart = Date.now();

        if (vadTimer) clearInterval(vadTimer);
        vadTimer = setInterval(() => {
            if (currentState !== 'listening' || !analyser) return;
            const level = getAudioLevel();
            const now = Date.now();

            // Calibrate: average the noise for first 300ms
            if (now - calibrateStart < VAD_CALIBRATE_MS) {
                noiseFloor += level;
                calibrateSamples++;
                if (calibrateSamples > 0) noiseFloor = (noiseFloor * (calibrateSamples - 1) + level) / calibrateSamples;
                return;
            }

            // Speech = anything above 1.8× noise floor (very sensitive)
            const threshold = Math.max(noiseFloor * 1.8, 0.015);
            const speaking = level > threshold;

            if (speaking) {
                if (!hasSpoken) {
                    hasSpoken = true;
                    speechStartTime = now;
                    console.log(`[VAD] speech! level=${level.toFixed(4)} threshold=${threshold.toFixed(4)} floor=${noiseFloor.toFixed(4)}`);
                }
                lastSpeechTime = now;
            } else if (hasSpoken && lastSpeechTime > 0) {
                const speechDuration = lastSpeechTime - speechStartTime;
                const silenceDuration = now - lastSpeechTime;

                if (silenceDuration > VAD_SILENCE_MS && speechDuration > VAD_MIN_SPEECH_MS) {
                    console.log(`[VAD] done: spoke ${speechDuration}ms, silent ${silenceDuration}ms`);
                    stopSilenceDetection();
                    stopRecordingAndProcess();
                }
            } else {
                // Slowly adapt noise floor during pre-speech silence
                noiseFloor = noiseFloor * 0.95 + level * 0.05;
            }
        }, VAD_INTERVAL);
    }

    function stopSilenceDetection() {
        if (vadTimer) {
            clearInterval(vadTimer);
            vadTimer = null;
        }
    }

    async function onCanvasClick() {
        console.log('[VoiceMode] click, state=' + currentState, 'stream=' + !!stream, 'recorder=' + (mediaRecorder?.state || 'null'));
        if (currentState === 'idle') {
            await startListening();
            console.log('[VoiceMode] after startListening, stream=' + !!stream);
            startRecording();
            startSilenceDetection();
        } else if (currentState === 'listening') {
            console.log('[VoiceMode] manual stop');
            stopSilenceDetection();
            stopRecordingAndProcess();
        } else if (currentState === 'speaking') {
            if (window._voiceTtsAudio) {
                try { window._voiceTtsAudio.pause(); } catch(_){}
                window._voiceTtsAudio = null;
            }
            await startListening();
            startRecording();
            startSilenceDetection();
        }
    }

    function startRecording() {
        recordedChunks = [];
        if (!stream) { console.error('[VoiceMode] startRecording: no stream!'); return; }
        const mimeTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
        let mimeType = '';
        for (const mt of mimeTypes) {
            if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(mt)) {
                mimeType = mt;
                break;
            }
        }
        try {
            mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
            mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) recordedChunks.push(e.data);
            };
            mediaRecorder.start(100);
        } catch (e) {
            console.error('Voice mode recording error:', e);
        }
    }

    async function stopRecordingAndProcess() {
        stopSilenceDetection();
        console.log('[VoiceMode] stopRecordingAndProcess, recorder=' + (mediaRecorder?.state || 'null'), 'chunks=' + recordedChunks.length);
        if (!mediaRecorder || mediaRecorder.state === 'inactive') {
            console.warn('[VoiceMode] recorder inactive, going to idle');
            setState('idle');
            return;
        }

        setState('thinking');

        // Stop recording and collect audio
        const audioBlob = await new Promise((resolve) => {
            mediaRecorder.onstop = () => {
                const mime = mediaRecorder.mimeType || 'audio/webm';
                resolve(new Blob(recordedChunks, { type: mime }));
            };
            mediaRecorder.stop();
        });

        console.log('Voice mode: audio blob size =', audioBlob.size, 'type =', audioBlob.type);
        if (audioBlob.size < 100) {
            console.warn('Voice mode: audio too short, restarting');
            await startListening();
            startRecording();
            startSilenceDetection();
            return;
        }

        try {
            // 1. Transcribe with Whisper
            console.log('[VoiceMode] transcribing...');
            const transcript = await transcribeVoice(audioBlob);
            console.log('[VoiceMode] transcript:', transcript);
            if (!transcript || !transcript.trim() || transcript.trim() === '.') {
                await startListening();
                startRecording();
                startSilenceDetection();
                return;
            }

            // 2. Render user message in chat + send to AI
            console.log('[VoiceMode] sending message...');

            // Show user bubble in chat
            if (window.chatManager) {
                const mgr = window.chatManager;
                const msgId = 'voice-' + Date.now();
                if (mgr.renderMessage) {
                    mgr.renderMessage({ id: msgId, sender: 'user', content: transcript, timestamp: new Date().toISOString() });
                }
            }

            const response = await sendVoiceMessage(transcript);
            console.log('[VoiceMode] response length:', response?.length || 0);

            // Show AI bubble in chat
            if (response && window.chatManager) {
                const mgr = window.chatManager;
                const aiId = 'voice-ai-' + Date.now();
                if (mgr.renderMessage) {
                    mgr.renderMessage({ id: aiId, sender: 'ai', content: response, timestamp: new Date().toISOString() });
                }
            }

            if (!response || !isActive) {
                if (isActive) { await startListening(); startRecording(); }
                return;
            }

            // 3. Speak the response with TTS
            setState('speaking');
            await speakVoiceResponse(response);

            // 4. Loop back to listening
            if (isActive) {
                if (_interrupted) {
                    // User spoke over AI — already recording, start processing what they said
                    console.log('[VoiceMode] interrupted, starting fresh recording');
                }
                await startListening();
                startRecording();
                startSilenceDetection();
            }
        } catch (e) {
            console.error('Voice mode error:', e);
            if (isActive) { await startListening(); startRecording(); startSilenceDetection(); }
        }
    }

    async function transcribeVoice(audioBlob) {
        const formData = new FormData();
        const ext = audioBlob.type.includes('mp4') ? 'mp4' : audioBlob.type.includes('ogg') ? 'ogg' : 'webm';
        formData.append('audio', audioBlob, `voice.${ext}`);
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const headers = {};
        if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;

        const resp = await fetch('/speech-to-text/groq', {
            method: 'POST', body: formData, headers, credentials: 'same-origin'
        });
        if (!resp.ok) return null;
        const data = await resp.json();
        return data.text || null;
    }

    async function sendVoiceMessage(text) {
        // Use the existing sendMessage flow but capture the response
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
        if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;

        // Use the active session from chatManager
        const sessionId = window.chatManager?.activeSessionId || '';
        const formBody = new URLSearchParams({
            prompt: text,
            model: 'auto',
            session_id: sessionId,
        });

        try {
            const resp = await fetch('/chat/stream', {
                method: 'POST', headers, body: formBody, credentials: 'same-origin'
            });
            if (!resp.ok) return null;

            // Read SSE stream, collect content
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let content = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                for (const line of chunk.split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const parsed = JSON.parse(line.slice(6));
                        if (parsed.type === 'content' && parsed.text) content += parsed.text;
                        if (parsed.type === 'done' && parsed.content) content = parsed.content;
                    } catch (_) {}
                }
            }
            return content || null;
        } catch (e) {
            console.error('Voice sendMessage error:', e);
            return null;
        }
    }

    let _interrupted = false;

    async function speakVoiceResponse(text) {
        if (!text) return;
        _interrupted = false;
        const ttsText = text.length > 3000 ? text.substring(0, 3000) : text;
        const clean = ttsText.replace(/```[\s\S]*?```/g, '').replace(/[*_#`>|]/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').trim();
        if (!clean) { console.warn('[VoiceMode] nothing to speak after cleanup'); return; }

        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const headers = { 'Content-Type': 'application/json' };
        if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;

        try {
            console.log('[VoiceMode] fetching TTS, text length:', clean.length);
            const resp = await fetch('/tts/groq', {
                method: 'POST',
                headers,
                body: JSON.stringify({ text: clean, voice: 'auto', format: 'wav' }),
                credentials: 'same-origin'
            });
            console.log('[VoiceMode] TTS response:', resp.status, resp.headers.get('content-type'));
            if (!resp.ok) { console.error('[VoiceMode] TTS failed:', resp.status); return; }

            const arrayBuffer = await resp.arrayBuffer();
            console.log('[VoiceMode] TTS audio bytes:', arrayBuffer.byteLength);

            // Use shared playback context (created on enter() user gesture)
            if (!playbackCtx || playbackCtx.state === 'closed') {
                playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (playbackCtx.state === 'suspended') await playbackCtx.resume();

            const decodedBuffer = await playbackCtx.decodeAudioData(arrayBuffer.slice(0));
            console.log('[VoiceMode] decoded audio, duration:', decodedBuffer.duration);

            const source = playbackCtx.createBufferSource();
            source.buffer = decodedBuffer;
            source.connect(playbackCtx.destination);
            window._voiceTtsAudio = { pause: () => { try { source.stop(); } catch(_){} } };

            let interruptTimer = null;
            const playStartTime = Date.now();

            // Interrupt detection — mic stays open during playback
            // Sample baseline audio level from speaker for 300ms, then detect spikes above it
            if (analyser) {
                let interruptCount = 0;
                let playbackBaseline = 0;
                let baselineSamples = 0;

                interruptTimer = setInterval(() => {
                    if (!analyser || _interrupted) return;
                    const level = getAudioLevel();
                    const elapsed = Date.now() - playStartTime;

                    // First 400ms: measure the speaker playback baseline
                    if (elapsed < 400) {
                        playbackBaseline += level;
                        baselineSamples++;
                        return;
                    }

                    if (baselineSamples > 0 && playbackBaseline > 0) {
                        playbackBaseline = playbackBaseline / baselineSamples;
                        baselineSamples = 0; // Only compute once
                        console.log(`[VoiceMode] playback baseline: ${playbackBaseline.toFixed(4)}`);
                    }

                    // Interrupt if level is significantly above the playback baseline
                    // User speaking into mic will spike well above the ambient speaker level
                    const interruptThreshold = Math.max(playbackBaseline * 1.5, 0.15);
                    if (level > interruptThreshold) {
                        interruptCount++;
                        if (interruptCount >= 4) { // 4 × 50ms = 200ms sustained spike
                            console.log(`[VoiceMode] INTERRUPT: level=${level.toFixed(3)} baseline=${playbackBaseline.toFixed(3)} threshold=${interruptThreshold.toFixed(3)}`);
                            _interrupted = true;
                            clearInterval(interruptTimer);
                            try { source.stop(); } catch(_){}
                            // playbackCtx stays open for reuse
                        }
                    } else {
                        interruptCount = Math.max(0, interruptCount - 1); // Decay slowly
                    }
                }, 50);
            }

            await new Promise((resolve) => {
                source.onended = () => {
                    if (interruptTimer) clearInterval(interruptTimer);
                    // playbackCtx stays open for reuse across turns
                    if (_interrupted) {
                        console.log('[VoiceMode] interrupted — skipping to listen');
                    } else {
                        console.log('[VoiceMode] audio ended naturally');
                    }
                    resolve();
                };
                source.start(0);
                console.log('[VoiceMode] audio playing');
            });

            window._voiceTtsAudio = null;
        } catch (e) {
            console.error('[VoiceMode] TTS error:', e);
        }
    }

    function onPrimaryClick(e) {
        e.stopPropagation();
        onCanvasClick();
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

        // Create playback AudioContext on user gesture (required for mobile)
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (playbackCtx.state === 'suspended') {
            playbackCtx.resume();
        }

        time = 0;
        lastTime = performance.now();
        smoothedLevel = 0;

        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        setState('idle');
        animate();

        // Auto-start: open mic + start recording after brief delay
        setTimeout(async () => {
            if (isActive && currentState === 'idle') {
                try {
                    await startListening();
                    startRecording();
                    startSilenceDetection();
                    console.log('[VoiceMode] auto-started listening');
                } catch (e) {
                    // Mic permission denied — stay idle, user can tap
                    console.warn('[VoiceMode] auto-start failed (mic denied?), tap to start');
                    setState('idle');
                }
            }
        }, 500);
    }

    function exit() {
        if (!isActive) return;
        isActive = false;
        stopSilenceDetection();
        stopListening();
        if (window._voiceTtsAudio) {
            try { window._voiceTtsAudio.pause(); } catch(_){}
            window._voiceTtsAudio = null;
        }
        if (playbackCtx && playbackCtx.state !== 'closed') {
            playbackCtx.close().catch(() => {});
            playbackCtx = null;
        }
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }
        if (_resizeHandler) window.removeEventListener('resize', _resizeHandler);
        if (_keydownHandler) document.removeEventListener('keydown', _keydownHandler);
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
            // Richer rhythmic simulation: primary beat + harmonics + flutter
            audioLevel = 0.45 + Math.sin(time * 4.8) * 0.25
                              + Math.sin(time * 9.2) * 0.12
                              + Math.sin(time * 17.0) * 0.06;
        } else if (currentState === 'thinking') {
            // Slower pulse with gentle undulation
            audioLevel = 0.22 + Math.sin(time * 1.8) * 0.12
                              + Math.sin(time * 4.3) * 0.05;
        } else {
            // Idle: gentle life-sign breath, never fully zero
            audioLevel = 0.08 + Math.sin(time * 0.72) * 0.04
                              + Math.sin(time * 1.51) * 0.02;
        }
        smoothedLevel += (audioLevel - smoothedLevel) * 0.20; // faster attack

        // ── Compute metaball blob positions ──
        const speedMult = currentState === 'idle' ? 0.3
                        : currentState === 'listening' ? 0.6
                        : currentState === 'thinking' ? 1.0
                        : 1.4;
        const orbitMult = currentState === 'thinking' ? 0.85 : 1.0;
        const audioPush = smoothedLevel * 0.5;
        const audioInflate = smoothedLevel * 0.12;
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

        // Pass metaball blob positions and radii
        gl.uniform2fv(orbUniforms.blobPos, blobPosData);
        gl.uniform1fv(orbUniforms.blobRad, blobRadData);

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

        // Clean orb — no extra effects, shader handles everything
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
