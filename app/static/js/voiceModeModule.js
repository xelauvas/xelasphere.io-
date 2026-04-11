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
    let _voiceBarRow = null;
    let _voiceBarLabel = null;
    let _voiceBarEnd = null;

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
        _playbackAnalyser.smoothingTimeConstant = 0.7; // smooth — flows with speech, no jitter
        _playbackAnalyser.minDecibels = -70;
        _playbackAnalyser.maxDecibels = -10;
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

    function _ensureVoiceBar() {
        if (_voiceBarRow && _voiceBarRow.parentElement === _textareaContainer) return;

        // Build: [Label] [Canvas] [End button]
        _voiceBarRow = document.createElement('div');
        _voiceBarRow.className = 'voice-bar-row';

        _voiceBarLabel = document.createElement('span');
        _voiceBarLabel.className = 'voice-bar-label';
        _voiceBarLabel.textContent = 'Listening...';

        const waveWrap = document.createElement('div');
        waveWrap.className = 'voice-wave-wrap';
        _canvas = document.createElement('canvas');
        waveWrap.appendChild(_canvas);

        _voiceBarEnd = document.createElement('button');
        _voiceBarEnd.className = 'voice-bar-end';
        _voiceBarEnd.setAttribute('aria-label', 'End voice mode');
        _voiceBarEnd.addEventListener('click', (e) => { e.stopPropagation(); exit(); });

        _voiceBarRow.appendChild(_voiceBarLabel);
        _voiceBarRow.appendChild(waveWrap);
        _voiceBarRow.appendChild(_voiceBarEnd);

        _textareaContainer.appendChild(_voiceBarRow);
        _resizeCanvas();
    }

    function _ensureTranscriptEl() {
        if (_transcriptOverlay && _voiceBarRow && _transcriptOverlay.parentElement === _voiceBarRow) return;
        _transcriptOverlay = document.createElement('span');
        _transcriptOverlay.className = 'voice-transcript';
        // Insert after canvas, before end button
        _voiceBarRow.insertBefore(_transcriptOverlay, _voiceBarEnd);
    }

    function _resizeCanvas() {
        if (!_canvas || !_canvas.parentElement) return;
        _dpr = window.devicePixelRatio || 1;
        const wrap = _canvas.parentElement;
        const rect = wrap.getBoundingClientRect();
        const w = Math.floor(rect.width);
        const h = Math.floor(rect.height);
        if (w < 10 || h < 5) return; // not laid out yet
        _canvas.width = w * _dpr;
        _canvas.height = h * _dpr;
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
        idle:      { amp: 3,  freq: 0.8, noiseAmt: 0.15, opacity: 0.25, glowMul: 0.3 },
        listening: { amp: 28, freq: 1.2, noiseAmt: 0.30, opacity: 0.40, glowMul: 0.5 },
        thinking:  { amp: 10, freq: 1.5, noiseAmt: 0.25, opacity: 0.30, glowMul: 0.4 },
        speaking:  { amp: 24, freq: 2.5, noiseAmt: 0.40, opacity: 0.45, glowMul: 0.5 },
    };

    // Waveform is always white — only label + button change color
    const STATE_COLORS = {
        idle:      [255, 255, 255],
        listening: [255, 255, 255],
        thinking:  [255, 255, 255],
        speaking:  [255, 255, 255],
    };

    let _wp = { amp: 3, freq: 0.8, noiseAmt: 0.15, opacity: 0.15, glowMul: 0.3 };


    function _lerp(a, b, t) { return a + (b - a) * t; }

    // ────────────────────────────────────────
    // Waveform — mirrored frequency bands, smooth and beautiful
    // ────────────────────────────────────────

    // 32 smoothed band values — drives the wave shape
    const NUM_BANDS = 32;
    let _bands = new Float32Array(NUM_BANDS);
    let _freqData = null;
    let _playFreqData = null;
    let _smoothEnergy = 0;

    function _analyseBands(analyserNode, dataArray) {
        if (!analyserNode || !dataArray) return;
        analyserNode.getByteFrequencyData(dataArray);
        const binCount = dataArray.length;
        const binsPerBand = Math.floor(binCount / NUM_BANDS);
        for (let b = 0; b < NUM_BANDS; b++) {
            let sum = 0;
            const start = b * binsPerBand;
            for (let i = start; i < start + binsPerBand && i < binCount; i++) {
                sum += dataArray[i];
            }
            const avg = sum / (binsPerBand * 255); // 0..1
            // Smooth: fast attack, slower release — snaps to sound, fades gracefully
            const speed = avg > _bands[b] ? 0.5 : 0.12;
            _bands[b] += (avg - _bands[b]) * speed;
        }
        // Overall energy
        let total = 0;
        for (let b = 0; b < NUM_BANDS; b++) total += _bands[b];
        const energy = total / NUM_BANDS;
        _smoothEnergy += (energy - _smoothEnergy) * 0.2;
    }

    function _drawMirrorPath(ctx, points) {
        if (points.length < 2) return;
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length - 1; i++) {
            const xc = (points[i].x + points[i + 1].x) / 2;
            const yc = (points[i].y + points[i + 1].y) / 2;
            ctx.quadraticCurveTo(points[i].x, points[i].y, xc, yc);
        }
        ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
    }

    let _resizeRetries = 0;

    function _animateWaveform() {
        if (!isActive) return;

        // Re-check canvas size on first few frames (layout may settle late)
        if (!_ctx || _resizeRetries < 10) {
            _resizeCanvas();
            _resizeRetries++;
        }
        if (!_ctx) { _animId = requestAnimationFrame(_animateWaveform); return; }

        _waveTime += 1 / 60;

        const target = STATE_PARAMS[currentState] || STATE_PARAMS.idle;
        _wp.opacity = _lerp(_wp.opacity, target.opacity, 0.08);
        _wp.glowMul = _lerp(_wp.glowMul, target.glowMul, 0.08);

        const w = _canvas.width / _dpr;
        const h = _canvas.height / _dpr;
        const cy = h / 2;
        const maxAmp = h * 0.42; // max displacement from center

        _ctx.clearRect(0, 0, w, h);

        // ── Analyse audio ──
        if (currentState === 'listening' && analyser) {
            if (!_freqData) _freqData = new Uint8Array(analyser.frequencyBinCount);
            _analyseBands(analyser, _freqData);
        } else if (currentState === 'speaking' && _playbackAnalyser) {
            if (!_playFreqData) _playFreqData = new Uint8Array(_playbackAnalyser.frequencyBinCount);
            _analyseBands(_playbackAnalyser, _playFreqData);
        } else {
            // Decay to silence
            for (let b = 0; b < NUM_BANDS; b++) _bands[b] *= 0.92;
            _smoothEnergy *= 0.92;
        }

        // ── Build wave points — frequency bands shape the amplitude at each x ──
        const numPoints = 64;
        const topPoints = [];
        const bottomPoints = [];

        for (let i = 0; i <= numPoints; i++) {
            const nx = i / numPoints; // 0..1
            const x = nx * w;

            // Map x to a band — center bands get more energy (speech lives in mids)
            const bandF = nx * (NUM_BANDS - 1);
            const bLow = Math.floor(bandF);
            const bHigh = Math.min(bLow + 1, NUM_BANDS - 1);
            const bFrac = bandF - bLow;
            const bandVal = _bands[bLow] * (1 - bFrac) + _bands[bHigh] * bFrac;

            // Base wave motion — slow sine so the shape breathes
            const wave = Math.sin(_waveTime * 2.5 + nx * Math.PI * 2) * 0.15;
            // Noise for organic movement
            const noise = _noise2D(nx * 4, _waveTime * 0.6) * 0.1;

            // Combined displacement: audio drives amplitude, wave adds organic motion
            const audioDisp = bandVal * maxAmp;
            const idleDisp = (wave + noise) * 3; // tiny breathing when silent
            const disp = audioDisp + idleDisp;

            // Edge taper — fades to zero at both ends
            const taper = Math.sin(nx * Math.PI);
            const y = disp * taper;

            topPoints.push({ x, y: cy - y });
            bottomPoints.push({ x, y: cy + y });
        }

        // ── Draw layers ──

        // Layer 1: Gradient fill between the mirrored curves
        _ctx.save();
        _drawMirrorPath(_ctx, topPoints);
        // Continue path along bottom in reverse to close the shape
        for (let i = bottomPoints.length - 1; i >= 0; i--) {
            _ctx.lineTo(bottomPoints[i].x, bottomPoints[i].y);
        }
        _ctx.closePath();
        const fillAlpha = Math.min(0.12, _smoothEnergy * 0.5 + 0.02);
        const grad = _ctx.createLinearGradient(0, cy - maxAmp, 0, cy + maxAmp);
        grad.addColorStop(0, `rgba(255, 255, 255, 0)`);
        grad.addColorStop(0.3, `rgba(255, 255, 255, ${fillAlpha.toFixed(3)})`);
        grad.addColorStop(0.5, `rgba(255, 255, 255, ${(fillAlpha * 1.5).toFixed(3)})`);
        grad.addColorStop(0.7, `rgba(255, 255, 255, ${fillAlpha.toFixed(3)})`);
        grad.addColorStop(1, `rgba(255, 255, 255, 0)`);
        _ctx.fillStyle = grad;
        _ctx.fill();
        _ctx.restore();

        // Layer 2: Soft outer glow
        _ctx.lineWidth = 4;
        _ctx.strokeStyle = `rgba(255, 255, 255, ${(0.04 * _wp.glowMul).toFixed(3)})`;
        _drawMirrorPath(_ctx, topPoints); _ctx.stroke();
        _drawMirrorPath(_ctx, bottomPoints); _ctx.stroke();

        // Layer 3: Core lines — crisp and white
        _ctx.lineWidth = 1.5;
        _ctx.strokeStyle = `rgba(255, 255, 255, ${(_wp.opacity * 0.9).toFixed(3)})`;
        _drawMirrorPath(_ctx, topPoints); _ctx.stroke();
        _drawMirrorPath(_ctx, bottomPoints); _ctx.stroke();

        // Layer 4: Bright hot center on the top line only
        _ctx.lineWidth = 0.5;
        _ctx.strokeStyle = `rgba(255, 255, 255, ${_wp.opacity.toFixed(3)})`;
        _drawMirrorPath(_ctx, topPoints); _ctx.stroke();

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
            analyser.fftSize = 512;  // more bins for detailed shape
            analyser.smoothingTimeConstant = 0.15; // snappy response
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
        if (_voiceBarRow) _voiceBarRow.classList.add('has-transcript');

        // Animate words appearing one by one
        const words = text.split(/(\s+)/); // split keeping whitespace
        const fragment = document.createDocumentFragment();
        const wordSpans = [];

        for (let i = 0; i < words.length; i++) {
            if (!words[i]) continue;
            const span = document.createElement('span');
            // Whitespace tokens stay visible, content words start ghostly
            if (/^\s+$/.test(words[i])) {
                span.className = 'voice-char-confirmed';
            } else {
                span.className = 'voice-char-pending';
                wordSpans.push(span);
            }
            span.textContent = words[i];
            fragment.appendChild(span);
        }
        // Add cursor
        const cursor = document.createElement('span');
        cursor.className = 'voice-cursor';
        fragment.appendChild(cursor);
        _transcriptOverlay.appendChild(fragment);

        // Solidify words progressively
        const wordDelay = Math.min(80, 600 / Math.max(1, wordSpans.length));
        for (let i = 0; i < wordSpans.length; i++) {
            await new Promise(r => setTimeout(r, wordDelay));
            if (!isActive) return;
            wordSpans[i].className = 'voice-char-confirmed';
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
            if (_voiceBarRow) _voiceBarRow.classList.remove('has-transcript');
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
        if (!_textareaContainer) return;
        // Use end button if available, otherwise voice mode button, otherwise center
        const btn = _voiceBarEnd || _voiceModeButton || _sendButton;
        if (!btn) return;
        const btnRect = btn.getBoundingClientRect();
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

    function setState(state) {
        const prev = currentState;
        currentState = state;

        // Update textarea container data attribute for CSS
        if (_textareaContainer) {
            _textareaContainer.dataset.voiceState = state;
        }

        // Update voice bar label
        if (_voiceBarLabel) {
            _voiceBarLabel.textContent = STATE_LABELS[state] || '';
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

        // Create playback AudioContext on user gesture
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (playbackCtx.state === 'suspended') playbackCtx.resume();

        // Setup DOM — voice bar: [Label] [Canvas] [End]
        _ensureVoiceBar();
        _ensureTranscriptEl();

        // Fire ripple
        _fireRipple(true);

        // Activate classes — hides textarea + buttons, shows voice bar
        _textareaContainer.classList.add('voice-active');
        if (_inputRow) _inputRow.classList.add('voice-active');

        // Typing escape hatch (textarea still exists, just hidden)
        _textarea.addEventListener('keydown', _onKeydown);

        // Clear textarea for when voice exits
        _textarea.value = '';

        // Init animation state
        _waveTime = 0;
        _resizeRetries = 0;
        _wp = { ...STATE_PARAMS.idle };
        _bands.fill(0);
        _smoothEnergy = 0;

        // Resize canvas after layout settles, then start animation
        requestAnimationFrame(() => {
            _resizeCanvas();
            _animateWaveform();
        });

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

        // Remove classes — restores textarea + buttons, hides voice bar
        if (_textareaContainer) {
            _textareaContainer.classList.remove('voice-active');
            delete _textareaContainer.dataset.voiceState;
        }
        if (_inputRow) _inputRow.classList.remove('voice-active');

        // Restore textarea
        if (_textarea) {
            _textarea.removeEventListener('keydown', _onKeydown);
        }

        // Clear transcript
        if (_transcriptOverlay) _transcriptOverlay.innerHTML = '';
        if (_voiceBarRow) _voiceBarRow.classList.remove('has-transcript');

        // Restore button visibility
        if (window.updateSendButtonState) window.updateSendButtonState();

        // Stop audio
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
