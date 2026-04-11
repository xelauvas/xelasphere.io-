/**
 * Voice Mode Module — Living Input
 * Transforms the textarea in place: waveform canvas behind it,
 * ghostly text materialization, button morphing, state colors.
 */
const VoiceModeModule = (function() {
    'use strict';

    // ── State ──
    let isActive = false;
    let currentState = 'idle'; // idle | listening | thinking | speaking

    // ── DOM refs (cached on first enter) ──
    let _textareaContainer = null;
    let _textarea = null;
    let _inputRow = null;
    let _sendButton = null;
    let _voiceModeButton = null;
    let _actionsMenu = null;
    let _canvas = null;
    let _ctx = null;
    let _dpr = 1;
    let _transcriptOverlay = null;

    // ── Audio capture ──
    let audioContext = null;
    let analyser = null;
    let stream = null;
    let frequencyData = null;

    // ── Shared playback context ──
    let playbackCtx = null;
    let _playbackAnalyser = null;
    let _playbackFreqData = null;

    function _ensurePlaybackAnalyser() {
        if (!playbackCtx || _playbackAnalyser) return;
        _playbackAnalyser = playbackCtx.createAnalyser();
        _playbackAnalyser.fftSize = 256;
        _playbackAnalyser.smoothingTimeConstant = 0.6;
        _playbackFreqData = new Uint8Array(_playbackAnalyser.frequencyBinCount);
        _playbackAnalyser.connect(playbackCtx.destination);
    }

    function _getSpeakingLevel() {
        if (!_playbackAnalyser || !_playbackFreqData) return 0;
        _playbackAnalyser.getByteFrequencyData(_playbackFreqData);
        let sum = 0;
        for (let i = 0; i < _playbackFreqData.length; i++) sum += _playbackFreqData[i];
        return sum / (_playbackFreqData.length * 255);
    }

    // ── Recording ──
    let mediaRecorder = null;
    let recordedChunks = [];

    // ── Smart VAD ──
    let vadTimer = null;
    let hasSpoken = false;
    let lastSpeechTime = 0;
    let speechStartTime = 0;
    let noiseFloor = 0.02;
    const VAD_MIN_SPEECH_MS = 300;
    const VAD_SILENCE_MS = 1200;
    const VAD_CALIBRATE_MS = 300;
    const VAD_INTERVAL = 50;

    // ── Voice shortcuts ──
    let _preferredVoice = 'auto';
    let _detectedLang = 'en';

    // ── Streaming TTS Pipeline ──
    let _interrupted = false;
    let _audioQueue = [];
    let _currentSource = null;
    let _ttsInflight = 0;
    let _streamDone = false;
    let _interruptTimer = null;
    let _playbackBaseline = 0;

    // ── Animation ──
    let _animId = null;
    let _waveTime = 0;
    let _springAmp = 0;
    let _springVel = 0;
    let _color = [255, 255, 255];

    // ── Transcript state ──
    let _confirmedText = '';
    let _pendingText = '';

    // ────────────────────────────────────────
    // DOM Setup (called once on first enter, reused after)
    // ────────────────────────────────────────

    function _cacheDom() {
        _textareaContainer = document.querySelector('.textarea-container');
        _textarea = document.getElementById('prompt');
        _inputRow = document.querySelector('.input-row');
        _sendButton = document.querySelector('.send-button');
        _voiceModeButton = document.getElementById('voice-mode-button');
        _actionsMenu = document.getElementById('actions-menu');
    }

    function _ensureCanvas() {
        if (_canvas && _canvas.parentElement === _textareaContainer) return;
        _canvas = document.createElement('canvas');
        _canvas.className = 'voice-waveform-bg';
        // Insert as first child so it's behind textarea
        _textareaContainer.insertBefore(_canvas, _textareaContainer.firstChild);
        _resizeCanvas();
    }

    function _ensureTranscriptOverlay() {
        if (_transcriptOverlay && _transcriptOverlay.parentElement === _textareaContainer) return;
        _transcriptOverlay = document.createElement('div');
        _transcriptOverlay.className = 'voice-transcript-overlay';
        _textareaContainer.appendChild(_transcriptOverlay);
    }

    function _resizeCanvas() {
        if (!_canvas || !_textareaContainer) return;
        _dpr = window.devicePixelRatio || 1;
        const rect = _textareaContainer.getBoundingClientRect();
        _canvas.width = rect.width * _dpr;
        _canvas.height = rect.height * _dpr;
        _canvas.style.width = rect.width + 'px';
        _canvas.style.height = rect.height + 'px';
        _ctx = _canvas.getContext('2d');
        _ctx.scale(_dpr, _dpr);
    }

    // ────────────────────────────────────────
    // Simplex Noise (unchanged from original)
    // ────────────────────────────────────────

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
        return 70 * (n0 + n1 + n2);
    }

    // ────────────────────────────────────────
    // Waveform State Parameters (lower opacity for subtle bg)
    // ────────────────────────────────────────

    const STATE_PARAMS = {
        idle:      { amp: 3,  freq: 0.8, noiseAmt: 0.15, opacity: 0.15, glowMul: 0.3 },
        listening: { amp: 28, freq: 1.2, noiseAmt: 0.30, opacity: 0.25, glowMul: 0.5 },
        thinking:  { amp: 10, freq: 1.5, noiseAmt: 0.25, opacity: 0.20, glowMul: 0.4 },
        speaking:  { amp: 24, freq: 2.5, noiseAmt: 0.40, opacity: 0.25, glowMul: 0.5 },
    };

    const STATE_COLORS = {
        idle:      [255, 255, 255],
        listening: [96, 165, 250],
        thinking:  [251, 191, 36],
        speaking:  [74, 222, 128],
    };

    let _wp = { amp: 3, freq: 0.8, noiseAmt: 0.15, opacity: 0.15, glowMul: 0.3 };

    // Ghost trail ring buffer
    const TRAIL_LEN = 12;
    let _trailBuf = [];
    let _trailFrame = 0;

    // Particle pool
    const _particles = Array.from({ length: 16 }, () => ({ x: 0, y: 0, vx: 0, vy: 0, life: 0, maxLife: 0 }));
    let _particleIdx = 0;

    function _lerp(a, b, t) { return a + (b - a) * t; }

    // ────────────────────────────────────────
    // Waveform Drawing (same algorithms, subtle rendering)
    // ────────────────────────────────────────

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
            p.life = 400;
            p.maxLife = 400;
        }
    }

    function _buildWavePath(w, cy, time, amp, phaseOffset) {
        const points = [];
        const step = 8;
        for (let x = 0; x <= w; x += step) {
            const nx = x / w;
            const target = _wp;
            let val = Math.sin(2 * Math.PI * target.freq * time + nx * Math.PI * 2.5 + phaseOffset);
            if (currentState === 'speaking' || currentState === 'thinking') {
                val += 0.4 * Math.sin(2 * Math.PI * target.freq * 1.7 * time + nx * Math.PI * 3.5 + phaseOffset);
                val += 0.2 * Math.sin(2 * Math.PI * target.freq * 3.1 * time + nx * Math.PI * 1.8 + phaseOffset);
                val /= 1.6;
            }
            val += _noise2D(nx * 3, time * 0.8 + phaseOffset) * target.noiseAmt;
            const taper = Math.sin(nx * Math.PI);
            points.push({ x, y: cy + val * amp * taper });
        }
        return points;
    }

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

    function _animateWaveform() {
        if (!isActive || !_ctx) return;

        const dt = 1 / 60;
        _waveTime += dt;

        const target = STATE_PARAMS[currentState] || STATE_PARAMS.idle;
        const tc = STATE_COLORS[currentState] || STATE_COLORS.idle;
        const lerpSpeed = 0.08;

        _wp.freq = _lerp(_wp.freq, target.freq, lerpSpeed);
        _wp.noiseAmt = _lerp(_wp.noiseAmt, target.noiseAmt, lerpSpeed);
        _wp.opacity = _lerp(_wp.opacity, target.opacity, lerpSpeed);
        _wp.glowMul = _lerp(_wp.glowMul, target.glowMul, lerpSpeed);
        _color[0] = _lerp(_color[0], tc[0], lerpSpeed);
        _color[1] = _lerp(_color[1], tc[1], lerpSpeed);
        _color[2] = _lerp(_color[2], tc[2], lerpSpeed);
        const r = Math.round(_color[0]), g = Math.round(_color[1]), b = Math.round(_color[2]);

        let ampTarget = target.amp;
        if (currentState === 'listening') {
            const raw = getAudioLevel();
            ampTarget = Math.min(28, raw * 4 * 28);
            const springForce = 0.15;
            const damping = 0.85;
            _springVel += (ampTarget - _springAmp) * springForce;
            _springVel *= damping;
            _springAmp += _springVel;
            _wp.amp = Math.max(2, _springAmp);
            _wp.opacity = _lerp(0.15, 0.25, Math.min(1, raw * 4));
        } else if (currentState === 'speaking') {
            const raw = _getSpeakingLevel();
            ampTarget = 6 + raw * 22;
            const springForce = 0.2;
            const damping = 0.82;
            _springVel += (ampTarget - _springAmp) * springForce;
            _springVel *= damping;
            _springAmp += _springVel;
            _wp.amp = Math.max(4, _springAmp);
            _wp.opacity = _lerp(0.18, 0.25, Math.min(1, raw * 2.5));
        } else {
            _wp.amp = _lerp(_wp.amp, ampTarget, lerpSpeed);
            _springAmp = _wp.amp;
            _springVel = 0;
        }

        const w = _canvas.width / _dpr;
        const h = _canvas.height / _dpr;
        const cy = h / 2;
        _ctx.clearRect(0, 0, w, h);

        const topPhase = 0;
        const bottomPhase = (currentState === 'speaking') ? Math.PI / 6 : 0;
        const topPoints = _buildWavePath(w, cy, _waveTime, -_wp.amp, topPhase);
        const bottomPoints = _buildWavePath(w, cy, _waveTime, _wp.amp, bottomPhase);

        // Ghost trail
        _trailFrame++;
        if (_trailFrame % 2 === 0) {
            _trailBuf.push({ top: topPoints.map(p => ({ ...p })), bottom: bottomPoints.map(p => ({ ...p })) });
            if (_trailBuf.length > TRAIL_LEN) _trailBuf.shift();
        }

        for (let t = 0; t < _trailBuf.length; t++) {
            const ghostAlpha = 0.035 * (t + 1) / _trailBuf.length * _wp.opacity; // 60% of original
            _ctx.lineWidth = 0.5;
            _ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${ghostAlpha.toFixed(3)})`;
            _drawCurve(_ctx, _trailBuf[t].top);
            _drawCurve(_ctx, _trailBuf[t].bottom);
        }

        // Chromatic aberration (speaking at high amplitude)
        if (currentState === 'speaking' && _wp.amp > 24 * 0.7) {
            const abIntensity = (_wp.amp - 24 * 0.7) / (24 * 0.3);
            const abAlpha = 0.08 * abIntensity; // subtler than pill

            _ctx.save();
            _ctx.translate(-1, 0);
            _ctx.lineWidth = 1;
            _ctx.strokeStyle = `rgba(255, 100, 80, ${abAlpha.toFixed(3)})`;
            _drawCurve(_ctx, topPoints);
            _drawCurve(_ctx, bottomPoints);
            _ctx.restore();

            _ctx.save();
            _ctx.translate(1, 0);
            _ctx.lineWidth = 1;
            _ctx.strokeStyle = `rgba(80, 160, 255, ${abAlpha.toFixed(3)})`;
            _drawCurve(_ctx, topPoints);
            _drawCurve(_ctx, bottomPoints);
            _ctx.restore();
        }

        // 3-layer glow
        _ctx.lineWidth = 4;
        _ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${(0.03 * _wp.glowMul).toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        _ctx.lineWidth = 2;
        _ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${(0.08 * _wp.glowMul).toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        _ctx.lineWidth = 1;
        _ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${_wp.opacity.toFixed(3)})`;
        _drawCurve(_ctx, topPoints);
        _drawCurve(_ctx, bottomPoints);

        // Particles (half alpha for subtlety)
        for (const p of _particles) {
            if (p.life <= 0) continue;
            p.x += p.vx;
            p.y += p.vy;
            p.vy *= 0.96;
            p.vx *= 0.98;
            p.life -= 16.67;
            const alpha = Math.max(0, (p.life / p.maxLife) * 0.4); // half of original 0.8
            _ctx.beginPath();
            _ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
            _ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
            _ctx.fill();
        }

        _animId = requestAnimationFrame(_animateWaveform);
    }

    // ────────────────────────────────────────
    // Audio helpers (unchanged)
    // ────────────────────────────────────────

    function getAudioLevel() {
        if (!analyser || !frequencyData) return 0;
        analyser.getByteTimeDomainData(frequencyData);
        let sumSquares = 0;
        for (let i = 0; i < frequencyData.length; i++) {
            const normalized = (frequencyData[i] - 128) / 128;
            sumSquares += normalized * normalized;
        }
        return Math.sqrt(sumSquares / frequencyData.length);
    }

    async function startListening() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.3;
            analyser.minDecibels = -90;
            analyser.maxDecibels = -10;
            frequencyData = new Uint8Array(analyser.frequencyBinCount);
            const source = audioContext.createMediaStreamSource(stream);
            source.connect(analyser);
            setState('listening');
        } catch (e) {
            console.error('[VoiceMode] Microphone error:', e);
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

    // ────────────────────────────────────────
    // VAD (unchanged)
    // ────────────────────────────────────────

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
            if (now - calibrateStart < VAD_CALIBRATE_MS) {
                noiseFloor += level;
                calibrateSamples++;
                if (calibrateSamples > 0) noiseFloor = (noiseFloor * (calibrateSamples - 1) + level) / calibrateSamples;
                return;
            }
            const threshold = Math.max(noiseFloor * 1.8, 0.015);
            const speaking = level > threshold;
            if (speaking) {
                if (!hasSpoken) {
                    hasSpoken = true;
                    speechStartTime = now;
                    console.log(`[VAD] speech! level=${level.toFixed(4)} threshold=${threshold.toFixed(4)}`);
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
                noiseFloor = noiseFloor * 0.95 + level * 0.05;
            }
        }, VAD_INTERVAL);
    }

    function stopSilenceDetection() {
        if (vadTimer) { clearInterval(vadTimer); vadTimer = null; }
    }

    // ────────────────────────────────────────
    // Recording + processing
    // ────────────────────────────────────────

    function startRecording() {
        recordedChunks = [];
        if (!stream) { console.error('[VoiceMode] no stream'); return; }
        const mimeTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
        let mimeType = '';
        for (const mt of mimeTypes) {
            if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(mt)) {
                mimeType = mt; break;
            }
        }
        try {
            mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
            mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) recordedChunks.push(e.data);
            };
            mediaRecorder.start(100);
        } catch (e) {
            console.error('[VoiceMode] recording error:', e);
        }
    }

    async function stopRecordingAndProcess() {
        stopSilenceDetection();
        if (!mediaRecorder || mediaRecorder.state === 'inactive') {
            setState('idle');
            return;
        }

        setState('thinking');

        const audioBlob = await new Promise((resolve) => {
            mediaRecorder.onstop = () => {
                const mime = mediaRecorder.mimeType || 'audio/webm';
                resolve(new Blob(recordedChunks, { type: mime }));
            };
            mediaRecorder.stop();
        });

        if (audioBlob.size < 100) {
            await startListening(); startRecording(); startSilenceDetection();
            return;
        }

        try {
            // 1. Transcribe
            const transcript = await transcribeVoice(audioBlob);
            if (!transcript || !transcript.trim() || transcript.trim() === '.') {
                await startListening(); startRecording(); startSilenceDetection();
                return;
            }

            // 1.5 Voice shortcuts
            if (_handleVoiceShortcut(transcript)) {
                await startListening(); startRecording(); startSilenceDetection();
                return;
            }

            // 2. Show transcript materializing in textarea
            await _showTranscript(transcript);

            // 3. Animate text send (fade up) + render user bubble (fade in)
            await _sendTranscriptToChat(transcript);

            // 4. Stream LLM response + pipeline TTS
            const response = await streamAndSpeak(transcript);

            // 5. Show AI bubble (if not already streamed into chat)
            if (response && window.chatManager?.renderMessage) {
                window.chatManager.renderMessage({
                    id: 'voice-ai-' + Date.now(),
                    sender: 'ai',
                    content: response,
                    timestamp: new Date().toISOString()
                });
            }

            // 6. Loop back to listening
            if (isActive) {
                await startListening();
                startRecording();
                startSilenceDetection();
            }
        } catch (e) {
            console.error('[VoiceMode] error:', e);
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
        if (data.language) _detectedLang = data.language;
        return data.text || null;
    }

    // ────────────────────────────────────────
    // Voice Shortcuts (unchanged)
    // ────────────────────────────────────────

    function _handleVoiceShortcut(transcript) {
        const t = transcript.toLowerCase().trim();
        if (t === 'stop' || t === 'be quiet' || t === 'exit' || t === 'end') {
            console.log('[VoiceMode] shortcut: stop');
            exit();
            return true;
        }
        if (t.includes('go faster') || t.includes('speak faster') || t.includes('speed up')) {
            _preferredVoice = 'auto';
            if (window.Toast) window.Toast.success('Speaking faster');
            return true;
        }
        if (t.includes('slow down') || t.includes('speak slower') || t.includes('go slower')) {
            _preferredVoice = 'auto';
            if (window.Toast) window.Toast.success('Speaking slower');
            return true;
        }
        const voiceMap = {
            'british': 'bf_emma', 'british voice': 'bf_emma',
            'male': 'am_michael', 'male voice': 'am_michael',
            'female': 'af_heart', 'female voice': 'af_heart',
            'deep': 'bm_george', 'deep voice': 'bm_george',
            'soft': 'af_nicole', 'soft voice': 'af_nicole',
            'default': 'auto', 'normal voice': 'auto', 'reset voice': 'auto',
        };
        for (const [phrase, voice] of Object.entries(voiceMap)) {
            if (t.includes('switch to ' + phrase) || t.includes('use ' + phrase) || t === phrase) {
                _preferredVoice = voice;
                const label = voice === 'auto' ? 'default' : phrase;
                if (window.Toast) window.Toast.success(`Switched to ${label} voice`);
                return true;
            }
        }
        const langMap = {
            'french': 'fr', 'français': 'fr',
            'japanese': 'ja', '日本語': 'ja',
            'chinese': 'zh', 'mandarin': 'zh', '中文': 'zh',
            'spanish': 'es', 'español': 'es',
            'hindi': 'hi', 'हिंदी': 'hi',
            'italian': 'it', 'italiano': 'it',
            'english': 'en',
        };
        for (const [phrase, lang] of Object.entries(langMap)) {
            if (t.includes('switch to ' + phrase) || t.includes('speak ' + phrase) || t.includes('in ' + phrase)) {
                _detectedLang = lang;
                if (window.Toast) window.Toast.success(`Switched to ${phrase}`);
                return true;
            }
        }
        return false;
    }

    // ────────────────────────────────────────
    // Streaming TTS Pipeline (unchanged)
    // ────────────────────────────────────────

    function _cleanTextForTTS(text) {
        return text
            .replace(/```[\s\S]*?```/g, '')
            .replace(/[*_#`>|]/g, '')
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
            .trim();
    }

    function _splitAtSentenceBoundary(buffer) {
        const minChunk = 40;
        if (buffer.length < minChunk) return [null, buffer];
        let lastSplit = -1;
        for (let i = minChunk; i < buffer.length; i++) {
            const ch = buffer[i];
            if ((ch === '.' || ch === '?' || ch === '!') &&
                (i + 1 >= buffer.length || buffer[i + 1] === ' ' || buffer[i + 1] === '\n')) {
                lastSplit = i + 1;
            }
            if (ch === '\n' && i > minChunk) lastSplit = i + 1;
        }
        if (lastSplit > 0) return [buffer.substring(0, lastSplit).trim(), buffer.substring(lastSplit)];
        if (buffer.length > 200) {
            const lastSpace = buffer.lastIndexOf(' ', 200);
            if (lastSpace > minChunk) return [buffer.substring(0, lastSpace).trim(), buffer.substring(lastSpace)];
        }
        return [null, buffer];
    }

    async function _fetchTTSChunk(text, lang) {
        const clean = _cleanTextForTTS(text);
        if (!clean || clean.length < 3) return null;
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const headers = { 'Content-Type': 'application/json' };
        if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;
        try {
            const resp = await fetch('/tts/groq', {
                method: 'POST', headers,
                body: JSON.stringify({ text: clean, voice: _preferredVoice, format: 'wav', lang: lang || _detectedLang }),
                credentials: 'same-origin'
            });
            if (!resp.ok) return null;
            return await resp.arrayBuffer();
        } catch (e) {
            console.error('[VoiceMode] TTS chunk error:', e);
            return null;
        }
    }

    function _flushAudioQueue() {
        _audioQueue = [];
        _ttsInflight = 0;
        _streamDone = true;
        if (_currentSource) { try { _currentSource.stop(); } catch (_) {} _currentSource = null; }
        if (_interruptTimer) { clearInterval(_interruptTimer); _interruptTimer = null; }
    }

    async function _playNextInQueue() {
        if (_interrupted || !isActive) return;
        if (_audioQueue.length === 0) {
            if (_streamDone && _ttsInflight === 0) {
                window._voiceTtsAudio = null;
            }
            return;
        }
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (playbackCtx.state === 'suspended') await playbackCtx.resume();
        const buffer = _audioQueue.shift();
        const source = playbackCtx.createBufferSource();
        source.buffer = buffer;
        _ensurePlaybackAnalyser();
        if (_playbackAnalyser) {
            source.connect(_playbackAnalyser);
        } else {
            source.connect(playbackCtx.destination);
        }
        _currentSource = source;
        window._voiceTtsAudio = { pause: () => { try { source.stop(); } catch(_){} } };
        source.onended = () => {
            _currentSource = null;
            if (!_interrupted) _playNextInQueue();
        };
        source.start(0);
    }

    async function _enqueueAudio(arrayBuffer) {
        if (_interrupted || !isActive || !arrayBuffer || arrayBuffer.byteLength < 100) return;
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        try {
            const decoded = await playbackCtx.decodeAudioData(arrayBuffer.slice(0));
            _audioQueue.push(decoded);
            if (!_currentSource) _playNextInQueue();
        } catch (e) {
            console.error('[VoiceMode] audio decode error:', e);
        }
    }

    function _startInterruptDetection() {
        if (!analyser) return;
        let interruptCount = 0;
        _playbackBaseline = 0;
        let baselineSamples = 0;
        const detectStart = Date.now();
        _interruptTimer = setInterval(() => {
            if (!analyser || _interrupted) return;
            const level = getAudioLevel();
            const elapsed = Date.now() - detectStart;
            if (elapsed < 400) { _playbackBaseline += level; baselineSamples++; return; }
            if (baselineSamples > 0 && _playbackBaseline > 0) {
                _playbackBaseline = _playbackBaseline / baselineSamples;
                baselineSamples = 0;
            }
            const threshold = Math.max(_playbackBaseline * 1.5, 0.15);
            if (level > threshold) {
                interruptCount++;
                if (interruptCount >= 4) {
                    console.log(`[VoiceMode] INTERRUPT`);
                    _interrupted = true;
                    _flushAudioQueue();
                }
            } else {
                interruptCount = Math.max(0, interruptCount - 1);
            }
        }, 50);
    }

    async function streamAndSpeak(transcript) {
        _interrupted = false;
        _audioQueue = [];
        _currentSource = null;
        _ttsInflight = 0;
        _streamDone = false;
        _preferredVoice = 'auto';

        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const chatHeaders = { 'Content-Type': 'application/x-www-form-urlencoded' };
        if (csrfMeta) chatHeaders['X-CSRFToken'] = csrfMeta.content;

        const sessionId = window.chatManager?.activeSessionId || '';
        const formBody = new URLSearchParams({
            prompt: transcript, model: 'auto', session_id: sessionId,
        });

        let fullContent = '';
        let ttsBuffer = '';
        let chunkIndex = 0;
        let firstChunkTime = null;
        let _responseLang = null;
        const streamStart = Date.now();
        const FIRST_CHUNK_MIN = 80;
        const MAX_TTS_INFLIGHT = 2;
        const _pendingSentences = [];

        function _fireTTS(text) {
            const lang = _responseLang || _detectedLang;
            _ttsInflight++;
            _fetchTTSChunk(text, lang).then(audio => {
                _ttsInflight--;
                if (audio) _enqueueAudio(audio);
                _drainPending();
            });
        }

        function _drainPending() {
            while (_pendingSentences.length > 0 && _ttsInflight < MAX_TTS_INFLIGHT && !_interrupted) {
                _fireTTS(_pendingSentences.shift());
            }
        }

        function _dispatchTTS(text) {
            if (_interrupted) return;
            if (_ttsInflight < MAX_TTS_INFLIGHT) {
                _fireTTS(text);
            } else {
                _pendingSentences.push(text);
            }
        }

        try {
            const resp = await fetch('/chat/stream', {
                method: 'POST', headers: chatHeaders, body: formBody, credentials: 'same-origin'
            });
            if (!resp.ok) return null;

            setState('speaking');
            _startInterruptDetection();

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                if (_interrupted) { reader.cancel(); break; }
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                for (const line of chunk.split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const parsed = JSON.parse(line.slice(6));
                        if (parsed.type === 'tts_voice') {
                            _preferredVoice = parsed.voice;
                            _responseLang = parsed.lang || _detectedLang;
                            continue;
                        }
                        if (parsed.type === 'content' && parsed.text) {
                            fullContent += parsed.text;
                            ttsBuffer += parsed.text;
                            if (chunkIndex === 0 && ttsBuffer.length < FIRST_CHUNK_MIN) continue;
                            const [sentence, remainder] = _splitAtSentenceBoundary(ttsBuffer);
                            if (sentence) {
                                ttsBuffer = remainder;
                                if (chunkIndex === 0) {
                                    firstChunkTime = Date.now() - streamStart;
                                }
                                chunkIndex++;
                                _dispatchTTS(sentence);
                            }
                        }
                        if (parsed.type === 'done' && parsed.content) fullContent = parsed.content;
                    } catch (_) {}
                }
            }

            if (ttsBuffer.trim() && !_interrupted) _dispatchTTS(ttsBuffer);
            _streamDone = true;

            await new Promise((resolve) => {
                const check = setInterval(() => {
                    if (_interrupted || (!_currentSource && _audioQueue.length === 0 && _ttsInflight === 0)) {
                        clearInterval(check); resolve();
                    }
                }, 100);
            });

            if (_interruptTimer) { clearInterval(_interruptTimer); _interruptTimer = null; }
            return fullContent || null;
        } catch (e) {
            console.error('[VoiceMode] streamAndSpeak error:', e);
            _flushAudioQueue();
            return fullContent || null;
        }
    }

    // ────────────────────────────────────────
    // Transcript Materialization
    // ────────────────────────────────────────

    async function _showTranscript(text) {
        if (!_transcriptOverlay) return;
        _confirmedText = '';
        _pendingText = text;
        _transcriptOverlay.innerHTML = '';

        // Animate characters appearing one by one
        const chars = text.split('');
        const fragment = document.createDocumentFragment();
        const spans = [];

        for (let i = 0; i < chars.length; i++) {
            const span = document.createElement('span');
            span.className = 'voice-char-pending';
            span.textContent = chars[i];
            fragment.appendChild(span);
            spans.push(span);
        }
        // Add cursor
        const cursor = document.createElement('span');
        cursor.className = 'voice-cursor';
        fragment.appendChild(cursor);
        _transcriptOverlay.appendChild(fragment);

        // Solidify characters progressively
        const charDelay = Math.min(30, 600 / chars.length); // max 600ms total
        for (let i = 0; i < spans.length; i++) {
            await new Promise(r => setTimeout(r, charDelay));
            if (!isActive) return;
            spans[i].className = 'voice-char-confirmed';
        }

        // Remove cursor after all confirmed
        cursor.remove();
        _confirmedText = text;
        _pendingText = '';
    }

    async function _sendTranscriptToChat(transcript) {
        // 1. Fade out the transcript overlay upward
        if (_transcriptOverlay) {
            _transcriptOverlay.classList.add('sending');
            await new Promise(r => setTimeout(r, 200));
            _transcriptOverlay.classList.remove('sending');
            _transcriptOverlay.innerHTML = '';
        }

        // 2. Render user bubble with fade-in class
        if (window.chatManager?.renderMessage) {
            window.chatManager.renderMessage({
                id: 'voice-' + Date.now(),
                sender: 'user',
                content: transcript,
                timestamp: new Date().toISOString()
            });
            // Add voice-fade-in class to the most recently added message
            const chatDisplay = document.getElementById('chat-display');
            if (chatDisplay) {
                const lastMsg = chatDisplay.lastElementChild;
                if (lastMsg) lastMsg.classList.add('voice-fade-in');
            }
        }
    }

    // ────────────────────────────────────────
    // Ripple Effect
    // ────────────────────────────────────────

    function _fireRipple(expanding) {
        if (!_textareaContainer || !_sendButton) return;
        const btnRect = _sendButton.getBoundingClientRect();
        const containerRect = _textareaContainer.getBoundingClientRect();
        const ripple = document.createElement('div');
        ripple.className = 'voice-ripple';
        // Position at center of send button relative to container
        const cx = btnRect.left + btnRect.width / 2 - containerRect.left;
        const cy = btnRect.top + btnRect.height / 2 - containerRect.top;
        const size = Math.max(containerRect.width, containerRect.height) * 0.5;
        ripple.style.width = size + 'px';
        ripple.style.height = size + 'px';
        ripple.style.left = (cx - size / 2) + 'px';
        ripple.style.top = (cy - size / 2) + 'px';
        _textareaContainer.appendChild(ripple);
        requestAnimationFrame(() => {
            ripple.classList.add(expanding ? 'expanding' : 'collapsing');
        });
        setTimeout(() => ripple.remove(), 500);
    }

    // ────────────────────────────────────────
    // Typing Escape Hatch
    // ────────────────────────────────────────

    function _onKeydown(e) {
        if (!isActive) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        // User started typing — exit voice mode, let keystroke land in textarea
        exit();
    }

    // ────────────────────────────────────────
    // State Management
    // ────────────────────────────────────────

    const STATE_LABELS = {
        idle: '',
        listening: 'Listening...',
        thinking: 'Thinking...',
        speaking: 'Speaking...'
    };

    const STATE_PLACEHOLDERS = {
        idle: null, // restore original
        listening: 'Listening...',
        thinking: 'Thinking...',
        speaking: 'Speaking...'
    };

    let _originalPlaceholder = '';

    function setState(state) {
        const prev = currentState;
        currentState = state;

        if (prev !== state && isActive) _burstParticles();

        // Update textarea container data attribute for CSS
        if (_textareaContainer) {
            _textareaContainer.dataset.voiceState = state;
        }

        // Update send button data attribute for CSS
        if (_sendButton) {
            _sendButton.dataset.voiceState = state;
        }

        // Update placeholder
        if (_textarea) {
            const ph = STATE_PLACEHOLDERS[state];
            if (ph) {
                _textarea.placeholder = ph;
            } else if (_originalPlaceholder) {
                _textarea.placeholder = _originalPlaceholder;
            }
        }

        // Thinking sounds
        if (state === 'thinking') {
            if (window.ThinkingSounds) window.ThinkingSounds.start();
        } else {
            if (window.ThinkingSounds) window.ThinkingSounds.stop();
        }

        if (state !== 'listening') stopListening();
    }

    // ────────────────────────────────────────
    // Enter / Exit
    // ────────────────────────────────────────

    function enter() {
        if (isActive) return;
        isActive = true;

        _cacheDom();
        if (!_textareaContainer || !_textarea) {
            console.error('[VoiceMode] textarea not found');
            isActive = false;
            return;
        }

        // Save original placeholder
        _originalPlaceholder = _textarea.placeholder;

        // Create playback AudioContext on user gesture
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (playbackCtx.state === 'suspended') playbackCtx.resume();

        // Setup DOM
        _ensureCanvas();
        _ensureTranscriptOverlay();
        _resizeCanvas();

        // Fire ripple
        _fireRipple(true);

        // Activate classes
        _textareaContainer.classList.add('voice-active');
        if (_inputRow) _inputRow.classList.add('voice-active');

        // Morph send button → end button
        if (_sendButton) {
            _sendButton.classList.add('voice-end');
            _sendButton.style.display = 'flex';
        }
        // Hide voice mode button
        if (_voiceModeButton) {
            _voiceModeButton.classList.add('hidden');
        }

        // Typing escape hatch
        _textarea.addEventListener('keydown', _onKeydown);

        // Clear textarea
        _textarea.value = '';
        _textarea.style.color = 'transparent'; // hide native text

        // Init animation state
        _waveTime = 0;
        _springAmp = 0;
        _springVel = 0;
        _trailBuf = [];
        _trailFrame = 0;
        _wp = { ...STATE_PARAMS.idle };
        _color = [...STATE_COLORS.idle];

        // Start waveform animation
        _animateWaveform();

        // Auto-start mic
        setTimeout(async () => {
            if (isActive && currentState === 'idle') {
                try {
                    await startListening();
                    startRecording();
                    startSilenceDetection();
                } catch (e) {
                    console.warn('[VoiceMode] mic denied');
                    setState('idle');
                }
            }
        }, 300);
    }

    function exit() {
        if (!isActive) return;
        isActive = false;
        _interrupted = true;
        _flushAudioQueue();

        // Fire reverse ripple
        _fireRipple(false);

        // Stop animation
        if (_animId) { cancelAnimationFrame(_animId); _animId = null; }

        // Remove classes
        if (_textareaContainer) {
            _textareaContainer.classList.remove('voice-active');
            delete _textareaContainer.dataset.voiceState;
        }
        if (_inputRow) _inputRow.classList.remove('voice-active');

        // Unmorph send button
        if (_sendButton) {
            _sendButton.classList.remove('voice-end');
            delete _sendButton.dataset.voiceState;
        }

        // Restore textarea
        if (_textarea) {
            _textarea.style.color = '';
            _textarea.removeEventListener('keydown', _onKeydown);
            if (_originalPlaceholder) _textarea.placeholder = _originalPlaceholder;
        }

        // Clear transcript overlay
        if (_transcriptOverlay) _transcriptOverlay.innerHTML = '';

        // Restore button visibility
        if (_voiceModeButton) _voiceModeButton.classList.remove('hidden');
        if (window.updateSendButtonState) window.updateSendButtonState();

        // Stop sounds and audio
        if (window.ThinkingSounds) window.ThinkingSounds.stop();
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
        _playbackAnalyser = null;
        _playbackFreqData = null;
        setState('idle');
    }

    return {
        enter,
        exit,
        setState,
        isActive: () => isActive,
        getState: () => currentState
    };
})();

window.VoiceModeModule = VoiceModeModule;
