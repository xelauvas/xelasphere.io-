/**
 * Voice Mode Module — Inline voice input
 * Pill bar in input area: waveform + End button. No fullscreen overlay.
 * Transcript appears as chat bubbles. TTS streams alongside chat.
 */
const VoiceModeModule = (function() {
    'use strict';

    // State
    let isActive = false;
    let currentState = 'idle'; // idle | listening | thinking | speaking

    // Audio capture
    let audioContext = null;
    let analyser = null;
    let stream = null;
    let frequencyData = null;

    // Shared playback context — created once on user gesture, reused for all TTS
    let playbackCtx = null;

    // Recording
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

    // ── Pill Bar ──
    let _pillEl = null;
    let _pillBars = [];
    let _pillAnimId = null;
    let _pillTime = 0;

    // ── Voice shortcuts ──
    let _preferredVoice = 'auto';

    function _handleVoiceShortcut(transcript) {
        const t = transcript.toLowerCase().trim();

        // Stop / exit
        if (t === 'stop' || t === 'be quiet' || t === 'exit' || t === 'end') {
            console.log('[VoiceMode] shortcut: stop');
            exit();
            return true;
        }

        // Speed control
        if (t.includes('go faster') || t.includes('speak faster') || t.includes('speed up')) {
            _preferredVoice = 'auto';
            if (window.Toast) window.Toast.success('Speaking faster');
            console.log('[VoiceMode] shortcut: faster');
            return true;
        }
        if (t.includes('slow down') || t.includes('speak slower') || t.includes('go slower')) {
            _preferredVoice = 'auto';
            if (window.Toast) window.Toast.success('Speaking slower');
            console.log('[VoiceMode] shortcut: slower');
            return true;
        }

        // Voice switching
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
                console.log(`[VoiceMode] shortcut: voice → ${voice}`);
                return true;
            }
        }

        // Language switching
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
                console.log(`[VoiceMode] shortcut: lang → ${lang}`);
                return true;
            }
        }

        return false;
    }

    // ── Streaming TTS Pipeline ──
    let _interrupted = false;
    let _audioQueue = [];
    let _currentSource = null;
    let _ttsInflight = 0;
    let _streamDone = false;
    let _interruptTimer = null;
    let _playbackBaseline = 0;

    // ────────────────────────────────────────
    // Audio helpers
    // ────────────────────────────────────────

    function getAudioLevel() {
        if (!analyser || !frequencyData) return 0;
        // RMS from time-domain data — same method as dictation module
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

            // Reset waveform history
            _waveHistory = new Array(WAVE_HISTORY_LEN).fill(0);
            _lastWaveUpdate = 0;

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
    // VAD
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

            // 1.5 Voice shortcuts — intercept commands before LLM
            if (_handleVoiceShortcut(transcript)) {
                await startListening(); startRecording(); startSilenceDetection();
                return;
            }

            // 2. Show user bubble in chat
            if (window.chatManager?.renderMessage) {
                window.chatManager.renderMessage({
                    id: 'voice-' + Date.now(),
                    sender: 'user',
                    content: transcript,
                    timestamp: new Date().toISOString()
                });
            }

            // 3. Stream LLM response + pipeline TTS
            const response = await streamAndSpeak(transcript);

            // 4. Show AI bubble
            if (response && window.chatManager?.renderMessage) {
                window.chatManager.renderMessage({
                    id: 'voice-ai-' + Date.now(),
                    sender: 'ai',
                    content: response,
                    timestamp: new Date().toISOString()
                });
            }

            // 5. Loop back to listening
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

    let _detectedLang = 'en'; // auto-detected from Whisper

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
        // Capture detected language for TTS voice matching
        if (data.language) _detectedLang = data.language;
        return data.text || null;
    }

    // ────────────────────────────────────────
    // Streaming TTS Pipeline
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

        if (lastSplit > 0) {
            return [buffer.substring(0, lastSplit).trim(), buffer.substring(lastSplit)];
        }

        if (buffer.length > 200) {
            const lastSpace = buffer.lastIndexOf(' ', 200);
            if (lastSpace > minChunk) {
                return [buffer.substring(0, lastSpace).trim(), buffer.substring(lastSpace)];
            }
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
        source.connect(playbackCtx.destination);
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
        let _responseLang = null; // locked after first chunk
        const streamStart = Date.now();
        const FIRST_CHUNK_MIN = 150; // wait for enough text to detect language + voice

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
                        // Voice decided server-side — lock before any TTS fires
                        if (parsed.type === 'tts_voice') {
                            _preferredVoice = parsed.voice;
                            _responseLang = parsed.lang || _detectedLang;
                            console.log(`[VoiceMode] voice locked from stream: ${_preferredVoice}, lang=${_responseLang}`);
                            continue;
                        }
                        if (parsed.type === 'content' && parsed.text) {
                            fullContent += parsed.text;
                            ttsBuffer += parsed.text;

                            // Hold first chunk until we have enough text
                            if (chunkIndex === 0 && ttsBuffer.length < FIRST_CHUNK_MIN) continue;

                            const [sentence, remainder] = _splitAtSentenceBoundary(ttsBuffer);
                            if (sentence) {
                                ttsBuffer = remainder;
                                if (chunkIndex === 0) {
                                    firstChunkTime = Date.now() - streamStart;
                                    console.log(`[VoiceMode] first TTS chunk at ${firstChunkTime}ms`);
                                }
                                chunkIndex++;
                                _ttsInflight++;
                                const lang = _responseLang || _detectedLang;
                                _fetchTTSChunk(sentence, lang).then(audio => {
                                    _ttsInflight--;
                                    if (audio) _enqueueAudio(audio);
                                });
                            }
                        }
                        if (parsed.type === 'done' && parsed.content) fullContent = parsed.content;
                    } catch (_) {}
                }
            }

            if (ttsBuffer.trim() && !_interrupted) {
                _ttsInflight++;
                const lang = _responseLang || _detectedLang;
                _fetchTTSChunk(ttsBuffer, lang).then(audio => {
                    _ttsInflight--;
                    if (audio) _enqueueAudio(audio);
                });
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

            const totalTime = Date.now() - streamStart;
            console.log(`[VoiceMode] done: ${totalTime}ms, first-audio=${firstChunkTime}ms, chunks=${chunkIndex}`);

            return fullContent || null;
        } catch (e) {
            console.error('[VoiceMode] streamAndSpeak error:', e);
            _flushAudioQueue();
            return fullContent || null;
        }
    }

    // ────────────────────────────────────────
    // Pill Bar UI
    // ────────────────────────────────────────

    let _pillLabel = null;
    let _inputRow = null;
    const NUM_BARS = 40;

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

        // Center: waveform bars — fill the width
        const wave = document.createElement('div');
        wave.className = 'voice-pill-wave';
        _pillBars = [];
        for (let i = 0; i < NUM_BARS; i++) {
            const bar = document.createElement('span');
            bar.className = 'voice-pill-bar-segment';
            wave.appendChild(bar);
            _pillBars.push(bar);
        }

        // Right: End button
        const endBtn = document.createElement('button');
        endBtn.className = 'voice-pill-end';
        endBtn.textContent = 'End';
        endBtn.addEventListener('click', (e) => { e.stopPropagation(); exit(); });

        pill.appendChild(label);
        pill.appendChild(wave);
        pill.appendChild(endBtn);

        // Insert as overlay child of input-row
        _inputRow.appendChild(pill);
        _pillEl = pill;
    }

    function _showPill() {
        _createPill();
        if (!_pillEl) return;

        // Add class to input-row to hide its children
        if (_inputRow) _inputRow.classList.add('voice-active');

        _pillEl.style.display = 'flex';
        _pillEl.offsetHeight; // reflow
        requestAnimationFrame(() => { _pillEl.classList.add('active'); });

        _pillTime = 0;
        _animatePill();
    }

    function _hidePill() {
        if (_pillAnimId) { cancelAnimationFrame(_pillAnimId); _pillAnimId = null; }

        if (_pillEl) {
            _pillEl.classList.remove('active');
            _pillEl.classList.add('exiting');
            setTimeout(() => {
                if (_pillEl) { _pillEl.style.display = 'none'; _pillEl.classList.remove('exiting'); }
            }, 200);
        }

        // Restore input row
        setTimeout(() => {
            if (_inputRow) _inputRow.classList.remove('voice-active');
            if (window.updateSendButtonState) window.updateSendButtonState();
        }, 200);
    }

    // Scrolling waveform history — same style as dictation mic
    const WAVE_HISTORY_LEN = NUM_BARS; // one per bar
    let _waveHistory = new Array(WAVE_HISTORY_LEN).fill(0);
    let _lastWaveUpdate = 0;
    const WAVE_UPDATE_MS = 80; // push a new sample every 80ms

    const MAX_BAR_H = 28; // px, matches wave container height minus padding
    const MIN_BAR_H = 2;

    function _animatePill(timestamp) {
        if (!isActive || !_pillEl) return;

        _pillTime += 1 / 60;
        const now = timestamp || performance.now();
        const n = _pillBars.length;

        // ── Listening: scrolling amplitude bars from real mic ──
        if (currentState === 'listening') {
            if (now - _lastWaveUpdate > WAVE_UPDATE_MS) {
                const raw = getAudioLevel();
                const level = Math.min(1, raw * 4);
                _waveHistory.shift();
                _waveHistory.push(level);
                _lastWaveUpdate = now;
            }
            for (let i = 0; i < n; i++) {
                const level = _waveHistory[i] || 0;
                // Fade: older bars (left) dimmer
                const age = i / n;
                const faded = level * (0.4 + age * 0.6);
                const h = MIN_BAR_H + faded * (MAX_BAR_H - MIN_BAR_H);
                _pillBars[i].style.height = Math.min(MAX_BAR_H, Math.max(MIN_BAR_H, h)) + 'px';
                _pillBars[i].style.opacity = (0.35 + age * 0.65).toFixed(2);
            }

        // ── Thinking: traveling pulse wave ──
        } else if (currentState === 'thinking') {
            for (let i = 0; i < n; i++) {
                const phase = i / n;
                // Two slow waves traveling in opposite directions
                const w1 = Math.sin(2 * Math.PI * 0.8 * _pillTime + phase * Math.PI * 3);
                const w2 = Math.sin(2 * Math.PI * 1.3 * _pillTime - phase * Math.PI * 2 + 1.5);
                const combined = (w1 * 0.6 + w2 * 0.4) * 0.5 + 0.5;
                const h = MIN_BAR_H + combined * 12;
                _pillBars[i].style.height = Math.max(MIN_BAR_H, h) + 'px';
                _pillBars[i].style.opacity = (0.5 + combined * 0.5).toFixed(2);
            }

        // ── Speaking: lively dual-frequency wave ──
        } else if (currentState === 'speaking') {
            for (let i = 0; i < n; i++) {
                const phase = i / n;
                const w1 = Math.sin(2 * Math.PI * 2.5 * _pillTime + phase * Math.PI * 2.5);
                const w2 = Math.sin(2 * Math.PI * 4.2 * _pillTime + phase * Math.PI * 3.5);
                const w3 = Math.sin(2 * Math.PI * 7.0 * _pillTime + phase * Math.PI * 1.8);
                const combined = (w1 * 0.45 + w2 * 0.35 + w3 * 0.2) * 0.5 + 0.5;
                const h = MIN_BAR_H + combined * (MAX_BAR_H - 6);
                _pillBars[i].style.height = Math.min(MAX_BAR_H, Math.max(MIN_BAR_H, h)) + 'px';
                _pillBars[i].style.opacity = '';
            }

        // ── Idle: gentle breathing ──
        } else {
            const breath = Math.sin(_pillTime * 1.5) * 0.5 + 0.5;
            for (let i = 0; i < n; i++) {
                const h = MIN_BAR_H + breath * 3;
                _pillBars[i].style.height = h + 'px';
                _pillBars[i].style.opacity = (0.2 + breath * 0.15).toFixed(2);
            }
        }

        _pillAnimId = requestAnimationFrame((ts) => _animatePill(ts));
    }

    // ────────────────────────────────────────
    // State management
    // ────────────────────────────────────────

    const STATE_LABELS = {
        idle: 'Tap to speak',
        listening: 'Listening...',
        thinking: 'Thinking...',
        speaking: 'Speaking...'
    };

    function setState(state) {
        currentState = state;

        // Update pill visuals
        if (_pillEl) _pillEl.dataset.state = state;
        if (_pillLabel) _pillLabel.textContent = STATE_LABELS[state] || '';

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

        // Create playback AudioContext on user gesture (required for mobile)
        if (!playbackCtx || playbackCtx.state === 'closed') {
            playbackCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (playbackCtx.state === 'suspended') playbackCtx.resume();

        // Show pill bar (hides textarea + buttons)
        _showPill();

        // Auto-start mic after brief delay
        setTimeout(async () => {
            if (isActive && currentState === 'idle') {
                try {
                    await startListening();
                    startRecording();
                    startSilenceDetection();
                    console.log('[VoiceMode] listening');
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
        _hidePill();
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
