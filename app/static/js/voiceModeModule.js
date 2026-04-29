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
    // Initial preferred voice from Settings → General → Voice; falls back to
    // 'auto' (lets the backend pick based on content). This default is what
    // applies on a fresh voice-mode session before the LLM locks a voice mid-stream.
    let _preferredVoice = (typeof window.getPreferredVoice === 'function')
        ? (window.getPreferredVoice() || 'auto')
        : 'auto';
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
        // Fallback to voice bar row width if wrap has no size yet
        let w = Math.floor(rect.width);
        let h = Math.floor(rect.height);
        if (w < 10 && _voiceBarRow) {
            const rowRect = _voiceBarRow.getBoundingClientRect();
            w = Math.max(100, Math.floor(rowRect.width) - 140); // minus label + button
        }
        if (h < 5) h = 32; // default height
        if (w < 10) w = 200; // absolute fallback
        _canvas.width = w * _dpr;
        _canvas.height = h * _dpr;
        _canvas.style.width = w + 'px';
        _canvas.style.height = h + 'px';
        _ctx = _canvas.getContext('2d');
        _ctx.scale(_dpr, _dpr);
        console.log('[VoiceMode] canvas sized:', w, 'x', h);
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
            let avg = sum / (binsPerBand * 255); // 0..1
            // Boost low signals so quiet speech still dances
            avg = Math.min(1, Math.pow(avg, 0.7) * 1.4);
            // Fast attack for instant reaction, quick release for springiness
            const speed = avg > _bands[b] ? 0.65 : 0.18;
            _bands[b] += (avg - _bands[b]) * speed;
        }
        // Overall energy
        let total = 0;
        for (let b = 0; b < NUM_BANDS; b++) total += _bands[b];
        const energy = total / NUM_BANDS;
        _smoothEnergy += (energy - _smoothEnergy) * 0.3;
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
        const maxAmp = h * 0.48; // max displacement — uses almost full height

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

            // Exponential curve makes loud sounds HUGE while quiet stays small
            // bandVal^1.5 = dramatic peak scaling
            const peakScale = Math.pow(bandVal, 1.4);
            // Neighbor-aware boost: if adjacent bands are also loud, amplify more
            const bLeft = _bands[Math.max(0, bLow - 1)] || 0;
            const bRight = _bands[Math.min(NUM_BANDS - 1, bHigh + 1)] || 0;
            const neighborBoost = (bLeft + bRight) * 0.5; // 0..1
            const audioAmp = peakScale * maxAmp * (1.0 + neighborBoost * 0.8);

            // Flowing motion patterns — multiple sines at different speeds
            const flow1 = Math.sin(_waveTime * 5.0 + nx * Math.PI * 7);
            const flow2 = Math.sin(_waveTime * 3.0 + nx * Math.PI * 4);
            const flow3 = Math.sin(_waveTime * 1.8 + nx * Math.PI * 2);
            const flow = (flow1 * 0.5 + flow2 * 0.8 + flow3 * 0.5) / 1.8;

            // Per-position jitter modulated by band intensity for extra dance
            const jitter = _noise2D(nx * 8, _waveTime * 2) * bandVal;

            let disp;
            if (_smoothEnergy > 0.015) {
                // Active: dramatic peaks from exponential scaling
                disp = audioAmp * flow + jitter * maxAmp * 0.7;
            } else {
                // Silent: gentle organic breathing
                const idleWave = Math.sin(_waveTime * 1.5 + nx * Math.PI * 2) * 0.5;
                const idleNoise = _noise2D(nx * 3, _waveTime * 0.5) * 0.3;
                disp = (idleWave + idleNoise) * maxAmp * 0.15;
            }

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

    // Rolling short buffer of recent mic samples (for intent classification)
    let _shortBuffer = null;
    let _shortBufferSize = 12800; // 800ms at 16kHz
    function _captureShortBuffer() {
        if (!analyser) return null;
        if (!_shortBuffer) _shortBuffer = new Float32Array(_shortBufferSize);
        // Grab current analyser frame as Float32 and shift into ring
        const frame = new Uint8Array(analyser.fftSize);
        analyser.getByteTimeDomainData(frame);
        const fLen = frame.length;
        // Shift existing left by fLen
        _shortBuffer.copyWithin(0, fLen);
        // Write new samples at tail
        for (let i = 0; i < fLen; i++) {
            _shortBuffer[_shortBufferSize - fLen + i] = (frame[i] - 128) / 128;
        }
        return _shortBuffer;
    }

    // Predictive pipeline: pre-warm backend context when user starts speaking
    const PredictivePipeline = (() => {
        let primerPromise = null;
        let primerController = null;
        return {
            onSpeechDetected() {
                try {
                    const sid = window.chatManager?.activeSessionId || '';
                    if (!sid) return;
                    primerController = new AbortController();
                    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
                    const headers = { 'Content-Type': 'application/json' };
                    if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;
                    primerPromise = fetch('/chat/prime', {
                        method: 'POST', headers,
                        signal: primerController.signal,
                        body: JSON.stringify({ session_id: sid }),
                        credentials: 'same-origin'
                    }).catch(() => null);
                    console.log('[Predictive] priming session context');
                } catch (_) {}
            },
            async onTranscriptReady() {
                if (primerPromise) {
                    try { await primerPromise; } catch (_) {}
                    primerPromise = null;
                }
            },
            cancel() {
                if (primerController) { try { primerController.abort(); } catch(_){} }
                primerPromise = null;
            }
        };
    })();

    // Intent classifier: decides if mic activity is backchannel/noise/question/correction
    const IntentClassifier = (() => {
        const BACKCHANNELS = [
            'yeah','yes','ok','okay','sure','right','mm','hmm','uh huh',
            'got it','i see','of course','absolutely','exactly','mhm',
            'da','bine','sigur','în regulă', // Romanian
            'sí','claro','vale','ajá',       // Spanish
            'oui','d\'accord','bien sûr',    // French
            'sim','claro','certo','tá',      // Portuguese
        ];
        const CORRECTION_PREFIX = /^(no|wait|actually|stop|hold on|but|sorry|nu|aștept|espera|attend|pera)/i;

        function energy(buf) {
            let s = 0;
            for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
            return s / buf.length;
        }
        function rising(buf) {
            const half = Math.floor(buf.length / 2);
            return energy(buf.subarray(half)) > energy(buf.subarray(0, half)) * 1.3;
        }

        return {
            classify(samples, partialTranscript = '') {
                const lower = (partialTranscript || '').toLowerCase().trim();
                if (lower) {
                    if (BACKCHANNELS.some(b => lower === b || lower.startsWith(b + ' '))) return 'backchannel';
                    if (CORRECTION_PREFIX.test(lower)) return 'correction';
                }
                if (!samples) return 'noise';
                const e = energy(samples);
                const durApprox = samples.length / 16000; // assume 16kHz
                const r = rising(samples);
                if (durApprox < 0.4 && e < 0.002) return 'noise';
                if (durApprox < 0.6 && e < 0.01) return 'backchannel';
                if (r && durApprox < 1.0) return 'question';
                if (e > 0.005 && durApprox > 0.4) return 'correction';
                return 'noise';
            }
        };
    })();

    // Legacy AEC node — no longer used (browser echoCancellation handles it)
    let _aecNode = null;

    // Current streamAndSpeak AbortController — lets gracefulYield kill
    // the SSE fetch cleanly so buffered chunks don't leak past interrupt.
    let _streamAbort = null;

    // Currently playing TTS text + last completed (for interrupt context)
    let _currentTTSText = null;
    let _lastCompletedTTSText = '';

    // When true, startListening will NOT transition state to 'listening'.
    // Used by interrupt detection to open the mic during speaking state.
    let _startListeningSilent = false;

    async function startListening() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    // echoCancellation ON: without it, the AI's speaker output
                    // leaks into the mic and triggers false interrupts
                    // constantly. With it on, user voice is somewhat suppressed
                    // during TTS but still detectable above the low echo floor.
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 16000,
                }
            });
            audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });

            const micSource = audioContext.createMediaStreamSource(stream);
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 512;
            analyser.smoothingTimeConstant = 0.15;
            analyser.minDecibels = -90;
            analyser.maxDecibels = -10;
            frequencyData = new Uint8Array(analyser.frequencyBinCount);

            // Direct mic → analyser. Browser's echoCancellation is already
            // suppressing TTS speaker echo; we don't need a second AEC layer.
            micSource.connect(analyser);

            // Only transition state if caller wants it — interrupt detection
            // needs the mic during 'speaking' state without flipping to 'listening'.
            if (!_startListeningSilent) setState('listening');
        } catch (e) {
            console.error('[VoiceMode] Microphone error:', e);
            if (!_startListeningSilent) setState('idle');
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

    // Placeholder for legacy AEC reference feed — AEC is now handled
    // entirely by the browser. No-op kept for call-site compatibility.
    function _feedAECReference(_audioBuffer) { /* no-op */ }
    function _feedAECReference_UNUSED(audioBuffer) {
        if (!_aecNode) return;
        try {
            const srcData = audioBuffer.getChannelData(0);
            const srcRate = audioBuffer.sampleRate;
            const dstRate = 16000;
            let samples;
            if (srcRate === dstRate) {
                samples = Array.from(srcData);
            } else {
                const ratio = srcRate / dstRate;
                const dstLen = Math.floor(srcData.length / ratio);
                samples = new Array(dstLen);
                for (let i = 0; i < dstLen; i++) {
                    samples[i] = srcData[Math.floor(i * ratio)];
                }
            }
            _aecNode.port.postMessage({ type: 'reference', samples });
        } catch (e) {
            // ignore — AEC ref is best-effort
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
                    // Predictive: prime backend context the moment user starts speaking
                    PredictivePipeline.onSpeechDetected();
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

            // 6. Loop back to listening — unless interrupt already captured next turn
            if (isActive && !_interrupted) {
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
        const isShortVoiceCmd = t.length < 40;
        if (isShortVoiceCmd) {
            for (const [phrase, voice] of Object.entries(voiceMap)) {
                const patterns = [
                    `switch to ${phrase}`,
                    `use ${phrase}`,
                    `switch to ${phrase} voice`,
                    `use ${phrase} voice`,
                ];
                const matches = patterns.some(p => t === p || t === p + '.' || t === p + '!');
                if (matches || t === phrase) {
                    _preferredVoice = voice;
                    const label = voice === 'auto' ? 'default' : phrase;
                    if (window.Toast) window.Toast.success(`Switched to ${label} voice`);
                    return true;
                }
            }
        }
        // Language requests go through to the LLM — it responds in the requested
        // language naturally, and backend TTS detection picks up the language
        // from the actual response text. No client-side interception needed.
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
        const maxChunk = 200;
        if (buffer.length < minChunk) return [null, buffer];
        let lastSplit = -1;
        // Fast path: return at FIRST boundary past minChunk so bursts of text
        // are sliced into individual sentence chunks (not one mega-chunk).
        for (let i = minChunk; i < Math.min(buffer.length, maxChunk); i++) {
            const ch = buffer[i];
            if ('.?!。？！…'.includes(ch)) {
                const next = buffer[i + 1];
                if ('。？！…'.includes(ch) || i + 1 >= buffer.length || next === ' ' || next === '\n') {
                    return [buffer.substring(0, i + 1).trim(), buffer.substring(i + 1)];
                }
            }
            if (ch === '\n' && i > minChunk) {
                return [buffer.substring(0, i + 1).trim(), buffer.substring(i + 1)];
            }
        }
        // ASCII + CJK sentence terminators
        const terminators = '.?!。？！…';
        // CJK comma / pause punctuation — fallback break points
        const cjkPauses = '、，';
        for (let i = minChunk; i < buffer.length; i++) {
            const ch = buffer[i];
            // Sentence terminators — require space/newline AFTER for ASCII, immediate for CJK
            if (terminators.includes(ch)) {
                const next = buffer[i + 1];
                const isCJKTerm = '。？！…'.includes(ch);
                if (isCJKTerm || i + 1 >= buffer.length || next === ' ' || next === '\n') {
                    lastSplit = i + 1;
                }
            }
            if (ch === '\n' && i > minChunk) lastSplit = i + 1;
        }
        if (lastSplit > 0) return [buffer.substring(0, lastSplit).trim(), buffer.substring(lastSplit)];
        // Fallback 1: CJK commas/pauses when buffer is getting long
        if (buffer.length > 80) {
            for (let i = buffer.length - 1; i >= minChunk; i--) {
                if (cjkPauses.includes(buffer[i])) {
                    return [buffer.substring(0, i + 1).trim(), buffer.substring(i + 1)];
                }
            }
        }
        // Fallback 2: break at space for Latin-script long text
        if (buffer.length > 200) {
            const lastSpace = buffer.lastIndexOf(' ', 200);
            if (lastSpace > minChunk) return [buffer.substring(0, lastSpace).trim(), buffer.substring(lastSpace)];
        }
        // Fallback 3: hard break for CJK that has no punctuation at all
        if (buffer.length > 150) {
            return [buffer.substring(0, 150).trim(), buffer.substring(150)];
        }
        return [null, buffer];
    }

    async function _fetchTTSChunk(text, lang) {
        const clean = _cleanTextForTTS(text);
        console.log('[VoiceMode] TTS fetch:', lang, clean.length, 'chars:', clean.slice(0, 40));
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

    // Graceful yield: fade current TTS, finish current sentence, drop the rest
    async function gracefulYield(intent) {
        console.log(`[VoiceMode] gracefulYield — intent: ${intent}`);
        if (_interruptTimer) { clearInterval(_interruptTimer); _interruptTimer = null; }

        // Kill the ongoing SSE fetch so buffered chunks stop arriving.
        if (_streamAbort) {
            try { _streamAbort.abort(); } catch (_) {}
            _streamAbort = null;
        }

        // Transition speaking → yielding (allowed) before fade
        setState('yielding', { intent });

        // 1. Fade out current audio source over 180ms instead of hard stop
        if (_currentSource && playbackCtx) {
            try {
                const gain = playbackCtx.createGain();
                // Reroute: source → gain → analyser (or destination)
                try { _currentSource.disconnect(); } catch (_) {}
                _currentSource.connect(gain);
                if (_playbackAnalyser) {
                    gain.connect(_playbackAnalyser);
                } else {
                    gain.connect(playbackCtx.destination);
                }
                const t = playbackCtx.currentTime;
                gain.gain.setValueAtTime(1, t);
                gain.gain.linearRampToValueAtTime(0, t + 0.18);
                await new Promise(r => setTimeout(r, 200));
                try { _currentSource.stop(); } catch (_) {}
            } catch (e) {
                try { _currentSource.stop(); } catch (_) {}
            }
            _currentSource = null;
        }

        // 2. Drop remaining queued TTS chunks — don't keep speaking
        _audioQueue = [];
        _ttsInflight = 0;

        // 3. Save context for next LLM call
        _interruptContext = {
            wasAISaying: _currentTTSText || _lastCompletedTTSText || '',
            intent,
            timestamp: Date.now(),
        };

        // 4. Begin capturing the user's interrupt speech
        setState('capturing', { intent });
        await _captureInterruptSpeech();
    }

    // Persistent interrupt context for system_addition on next turn
    let _interruptContext = null;

    function _buildInterruptSystemAddition() {
        if (!_interruptContext) return '';
        const { wasAISaying, intent, timestamp } = _interruptContext;
        if (Date.now() - timestamp > 30000) return ''; // expire after 30s
        const phrasing = {
            correction: 'corrected you or redirected the conversation',
            question:   'asked a question mid-response',
            backchannel:'acknowledged what you were saying',
        }[intent] || 'interrupted you';
        const excerpt = (wasAISaying || '').slice(0, 120).trim();
        return [
            '[CONVERSATION CONTEXT]',
            `You were just interrupted mid-response. You were saying: "${excerpt}..."`,
            `The user ${phrasing}.`,
            'Respond naturally — acknowledge the interruption briefly if appropriate ("Oh, good point", "Sure, let me address that"), then focus entirely on what the user just said.',
            'Do not continue what you were saying unless the user explicitly asks you to.',
            '[END CONTEXT]'
        ].join('\n');
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
        const item = _audioQueue.shift();
        const buffer = item.buffer || item; // support both plain buffer and {buffer, text}
        const source = playbackCtx.createBufferSource();
        source.buffer = buffer;
        _ensurePlaybackAnalyser();
        if (_playbackAnalyser) {
            source.connect(_playbackAnalyser);
        } else {
            source.connect(playbackCtx.destination);
        }
        // Feed TTS samples into AEC reference — mic will subtract this echo
        _feedAECReference(buffer);
        _currentSource = source;
        _currentTTSText = item.text || null;
        window._voiceTtsAudio = { pause: () => { try { source.stop(); } catch(_){} } };
        source.onended = () => {
            // Store the just-finished text for potential interrupt context
            if (_currentTTSText) _lastCompletedTTSText = _currentTTSText;
            _currentTTSText = null;
            _currentSource = null;
            if (!_interrupted) _playNextInQueue();
        };
        source.start(0);
    }

    async function _enqueueAudio(arrayBuffer, text) {
        if (_interrupted || !isActive || !arrayBuffer || arrayBuffer.byteLength < 100) return;
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        try {
            const decoded = await playbackCtx.decodeAudioData(arrayBuffer.slice(0));
            _audioQueue.push({ buffer: decoded, text: text || '' });
            if (!_currentSource) _playNextInQueue();
        } catch (e) {
            console.error('[VoiceMode] audio decode error:', e);
        }
    }

    async function _startInterruptDetection() {
        console.log('[Interrupt] starting detection…');
        // Ensure mic is active during speaking so we can hear user interrupting.
        // Open mic silently — do NOT transition state (we want to stay 'speaking').
        if (!analyser) {
            _startListeningSilent = true;
            try { await startListening(); }
            catch (e) { console.warn('[Interrupt] cannot start mic:', e); _startListeningSilent = false; return; }
            _startListeningSilent = false;
        }
        if (!analyser) {
            console.warn('[Interrupt] still no analyser after startListening');
            return;
        }
        console.log('[Interrupt] mic ready, watching for barge-in');
        let interruptCount = 0;
        // Use MINIMUM level during calibration — ignores user-voice residue and
        // spikes, keeps baseline as "quiet moments" of TTS echo only.
        _playbackBaseline = 1.0;
        let baselineSamples = 0;
        let lastLogTime = 0;
        const detectStart = Date.now();
        const CALIBRATE_MS = 800;
        _interruptTimer = setInterval(async () => {
            if (!analyser || _interrupted) return;
            _captureShortBuffer();
            const level = getAudioLevel();
            const now = Date.now();
            const elapsed = now - detectStart;
            if (elapsed < CALIBRATE_MS) {
                // Track minimum observed level (quiet moments = pure echo or silence)
                if (level < _playbackBaseline) _playbackBaseline = level;
                baselineSamples++;
                return;
            }
            if (baselineSamples > 0) {
                _playbackBaseline = Math.max(_playbackBaseline, 0.001);
                baselineSamples = 0;
                console.log(`[Interrupt] baseline calibrated: ${_playbackBaseline.toFixed(4)} (post-AEC floor)`);
            }
            // With browser AEC ON, baseline is near-zero (0.001-0.003).
            // User voice during TTS is suppressed but still shows as 0.02-0.06.
            // Absolute floor 0.015 keeps us above AEC noise floor while catching
            // real speech. No multiplier needed — baseline is already minimal.
            const threshold = 0.015;
            // Only log every 3s to reduce spam
            if (now - lastLogTime > 3000) {
                console.log(`[Interrupt] level=${level.toFixed(4)} threshold=${threshold.toFixed(4)} count=${interruptCount}`);
                lastLogTime = now;
            }
            if (level > threshold) {
                interruptCount++;
                // 8 ticks × 50ms = 400ms sustained above threshold.
                // TTS echo spikes are brief; real user speech is much longer.
                if (interruptCount >= 8) {
                    const samples = _captureShortBuffer();
                    const intent = IntentClassifier.classify(samples, '');
                    console.log(`[Interrupt] candidate — intent: ${intent}, level=${level.toFixed(4)}`);
                    if (intent === 'backchannel' || intent === 'noise') {
                        interruptCount = 0;
                        return;
                    }
                    console.log('[Interrupt] FIRING — intent:', intent);
                    _interrupted = true;
                    _lastInterruptIntent = intent;
                    await gracefulYield(intent);
                }
            } else {
                interruptCount = Math.max(0, interruptCount - 1);
            }
        }, 50);
    }
    let _lastInterruptIntent = null;

    // After interrupt, record what the user says and send as next prompt
    async function _captureInterruptSpeech() {
        if (!isActive) return;
        console.log('[VoiceMode] capturing user interrupt speech');
        try {
            if (!stream) await startListening();
            startRecording();
            // Keep _interrupted=true until next streamAndSpeak starts.
            // This blocks late in-flight TTS fetches from enqueueing after yield.
            setState('listening');
            startSilenceDetection();
        } catch (e) {
            console.error('[VoiceMode] capture interrupt failed:', e);
            if (isActive) {
                await startListening();
                startRecording();
                startSilenceDetection();
            }
        }
    }

    async function streamAndSpeak(transcript) {
        _interrupted = false;
        _audioQueue = [];
        _currentSource = null;
        _ttsInflight = 0;
        _streamDone = false;
        // Wait for the context primer (if any) so the first LLM chunk is faster
        await PredictivePipeline.onTranscriptReady();
        _preferredVoice = 'auto';

        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const chatHeaders = { 'Content-Type': 'application/x-www-form-urlencoded' };
        if (csrfMeta) chatHeaders['X-CSRFToken'] = csrfMeta.content;

        const sessionId = window.chatManager?.activeSessionId || '';
        const formBodyParams = {
            prompt: transcript, model: 'auto', session_id: sessionId,
        };
        // If user interrupted the AI, attach system addition so next response acknowledges it
        const systemAddition = _buildInterruptSystemAddition();
        if (systemAddition) {
            formBodyParams.system_addition = systemAddition;
            console.log('[VoiceMode] system_addition attached for interrupt continuity');
        }
        _interruptContext = null; // consume once
        const formBody = new URLSearchParams(formBodyParams);

        let fullContent = '';
        let ttsBuffer = '';
        let chunkIndex = 0;
        let firstChunkTime = null;
        let _responseLang = null;
        const streamStart = Date.now();
        const FIRST_CHUNK_MIN = 30; // lower for CJK (denser text per char)
        const MAX_TTS_INFLIGHT = 2;
        const _pendingSentences = [];

        function _fireTTS(text) {
            const lang = _responseLang || _detectedLang;
            _ttsInflight++;
            _fetchTTSChunk(text, lang).then(audio => {
                _ttsInflight--;
                if (audio) _enqueueAudio(audio, text);
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

        // AbortController kills the SSE stream at the network level on interrupt.
        // Without this, buffered stream chunks keep firing TTS even after yield.
        _streamAbort = new AbortController();
        try {
            const resp = await fetch('/chat/stream', {
                method: 'POST', headers: chatHeaders, body: formBody,
                credentials: 'same-origin', signal: _streamAbort.signal,
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
                            console.log('[VoiceMode] tts_voice locked:', _preferredVoice, 'lang:', _responseLang);
                            continue;
                        }
                        if (parsed.type === 'content' && parsed.text) {
                            fullContent += parsed.text;
                            ttsBuffer += parsed.text;
                            if (chunkIndex === 0 && ttsBuffer.length < FIRST_CHUNK_MIN) continue;
                            const [sentence, remainder] = _splitAtSentenceBoundary(ttsBuffer);
                            console.log('[VoiceMode] buffer:', ttsBuffer.length, 'split:', sentence ? sentence.length : 'null');
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

            // If streaming didn't populate the buffer (e.g., non-streaming model that
            // only sent a final 'done' event), use the full content as fallback.
            let finalText = ttsBuffer.trim();
            if (!finalText && fullContent.trim() && chunkIndex === 0) {
                finalText = fullContent.trim();
                console.log('[VoiceMode] using fullContent fallback:', finalText.length, 'chars');
            }
            if (finalText && !_interrupted) {
                console.log('[VoiceMode] final TTS dispatch:', finalText.length, 'chars, text:', finalText.slice(0, 50));
                _dispatchTTS(finalText);
            } else {
                console.log('[VoiceMode] no final dispatch — buffer:', ttsBuffer.length, 'fullContent:', fullContent.length, 'interrupted:', _interrupted);
            }
            _streamDone = true;

            await new Promise((resolve) => {
                const check = setInterval(() => {
                    if (_interrupted || (!_currentSource && _audioQueue.length === 0 && _ttsInflight === 0)) {
                        clearInterval(check); resolve();
                    }
                }, 100);
            });

            if (_interruptTimer) { clearInterval(_interruptTimer); _interruptTimer = null; }
            _streamAbort = null;
            return fullContent || null;
        } catch (e) {
            // AbortError is expected when gracefulYield kills the stream — don't noise-log it
            if (e && e.name !== 'AbortError') {
                console.error('[VoiceMode] streamAndSpeak error:', e);
            }
            _flushAudioQueue();
            _streamAbort = null;
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
    // State Management — FSM with guards
    // ────────────────────────────────────────

    const STATE_LABELS = {
        idle: '',
        listening: 'Listening...',
        thinking: 'Thinking...',
        speaking: 'Speaking...',
        yielding: 'Listening...',
        capturing: 'Listening...',
    };

    // Finite state machine: allowed transitions + guards
    const FSM = (() => {
        const TRANSITIONS = {
            idle:      ['listening'],
            listening: ['thinking', 'idle'],
            thinking:  ['speaking', 'idle', 'listening'],
            speaking:  ['yielding', 'idle', 'listening'],
            yielding:  ['capturing', 'listening', 'idle'],
            capturing: ['thinking', 'listening', 'idle'],
        };
        const GUARDS = {
            'speaking→yielding': (ctx) => ctx.intent !== 'backchannel' && ctx.intent !== 'noise',
        };
        return {
            canTransition(from, to, ctx) {
                const allowed = TRANSITIONS[from] || [];
                if (!allowed.includes(to)) {
                    console.warn(`[FSM] Invalid transition: ${from} → ${to}`);
                    return false;
                }
                const guardKey = `${from}→${to}`;
                if (GUARDS[guardKey] && !GUARDS[guardKey](ctx || {})) {
                    console.log(`[FSM] Guard blocked: ${guardKey}`, ctx);
                    return false;
                }
                return true;
            },
            log(from, to, ctx) {
                console.log(`[FSM] ${from} → ${to}`, ctx || '');
            }
        };
    })();

    function setState(state, ctx) {
        const prev = currentState;
        // No-op if same state
        if (prev === state) return;
        // Validate transition
        if (prev && !FSM.canTransition(prev, state, ctx)) {
            return;
        }
        FSM.log(prev, state, ctx);
        currentState = state;

        // Update textarea container data attribute for CSS
        if (_textareaContainer) {
            _textareaContainer.dataset.voiceState = state;
        }

        // Update voice bar label
        if (_voiceBarLabel) {
            _voiceBarLabel.textContent = STATE_LABELS[state] || '';
        }

        // Keep mic alive during 'speaking' state so we can detect interrupts.
        // Only fully stop on idle/thinking.
        if (state === 'idle' || state === 'thinking') stopListening();
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

        // Tell backend this voice session ended (releases the tracked slot)
        try {
            const csrfMeta = document.querySelector('meta[name="csrf-token"]');
            const headers = { 'Content-Type': 'application/json' };
            if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.content;
            const sid = window.chatManager?.activeSessionId || '';
            if (sid) {
                fetch('/chat/voice-exit', {
                    method: 'POST', headers,
                    body: JSON.stringify({ session_id: sid }),
                    credentials: 'same-origin'
                }).catch(() => {});
            }
        } catch (_) {}
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
