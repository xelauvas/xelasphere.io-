/**
 * Dictation Module - Speech-to-Text using Groq Whisper API
 * Provides voice input functionality for the chat interface.
 */
const DictationModule = (function() {
    'use strict';

    // State
    let mediaRecorder = null;
    let audioChunks = [];
    let isRecording = false;
    let stream = null;
    let audioContext = null;
    let analyser = null;
    let animationId = null;

    // DOM elements
    let micButton = null;
    let promptInput = null;
    let textareaContainer = null;
    let recordingOverlay = null;

    // Supported MIME types in order of preference
    const MIME_TYPES = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus',
        'audio/ogg',
        'audio/wav'
    ];

    /**
     * Get the best supported MIME type for recording
     */
    function getSupportedMimeType() {
        for (const mimeType of MIME_TYPES) {
            if (MediaRecorder.isTypeSupported(mimeType)) {
                return mimeType;
            }
        }
        return ''; // Let browser choose default
    }

    /**
     * Initialize the dictation module
     */
    function init() {
        micButton = document.getElementById('microphone-button');
        promptInput = document.getElementById('prompt');
        textareaContainer = document.querySelector('.textarea-container');

        if (!micButton) {
            console.warn('DictationModule: Microphone button not found');
            return;
        }

        if (!promptInput || !textareaContainer) {
            console.warn('DictationModule: Required elements not found');
            return;
        }

        // Check for browser support
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            console.warn('DictationModule: MediaDevices API not supported');
            micButton.style.display = 'none';
            return;
        }

        if (typeof MediaRecorder === 'undefined') {
            console.warn('DictationModule: MediaRecorder API not supported');
            micButton.style.display = 'none';
            return;
        }

        // Create recording overlay
        createRecordingOverlay();

        // Bind click handler
        micButton.addEventListener('click', handleMicClick);

        console.log('DictationModule initialized');
    }

    /**
     * Create the recording overlay UI
     */
    function createRecordingOverlay() {
        recordingOverlay = document.createElement('div');
        recordingOverlay.className = 'dictation-recording-overlay';
        recordingOverlay.innerHTML = `
            <div class="dictation-waveform">
                <canvas class="dictation-waveform-canvas"></canvas>
            </div>
            <div class="dictation-controls">
                <button class="dictation-cancel-btn" data-tooltip="Cancel">
                    <i class="fa-solid fa-xmark"></i>
                </button>
                <button class="dictation-confirm-btn" data-tooltip="Send">
                    <i class="fa-solid fa-check"></i>
                </button>
            </div>
        `;

        // Insert overlay into textarea container (hidden by default)
        textareaContainer.appendChild(recordingOverlay);

        // Bind button events
        const cancelBtn = recordingOverlay.querySelector('.dictation-cancel-btn');
        const confirmBtn = recordingOverlay.querySelector('.dictation-confirm-btn');

        cancelBtn.addEventListener('click', handleCancel);
        confirmBtn.addEventListener('click', handleConfirm);

        // Keyboard shortcuts: ESC to cancel, Enter to send
        document.addEventListener('keydown', handleKeyboard);
    }

    /**
     * Handle keyboard shortcuts during recording
     */
    function handleKeyboard(event) {
        if (!isRecording) return;

        if (event.key === 'Escape') {
            event.preventDefault();
            cancelRecording();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            stopRecording(true);
        }
    }

    /**
     * Handle microphone button click
     */
    async function handleMicClick(event) {
        event.preventDefault();

        if (!isRecording) {
            await startRecording();
        }
    }

    /**
     * Handle cancel button click
     */
    function handleCancel(event) {
        event.preventDefault();
        cancelRecording();
    }

    /**
     * Handle confirm button click
     */
    function handleConfirm(event) {
        event.preventDefault();
        stopRecording(true); // true = process the recording
    }

    /**
     * Start recording audio
     */
    async function startRecording() {
        try {
            // Request microphone permission
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000
                }
            });

            const mimeType = getSupportedMimeType();
            const options = mimeType ? { mimeType } : {};

            mediaRecorder = new MediaRecorder(stream, options);
            audioChunks = [];

            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };

            mediaRecorder.onerror = (event) => {
                console.error('MediaRecorder error:', event.error);
                resetState();
            };

            // Start recording
            mediaRecorder.start(100); // Collect data every 100ms
            isRecording = true;

            // Show recording UI first
            showRecordingUI();

            // Setup audio analyzer for waveform (after isRecording is true)
            setupAudioAnalyzer();

            console.log('Recording started with MIME type:', mediaRecorder.mimeType);

        } catch (error) {
            console.error('Failed to start recording:', error);
            // Silent fail - errors logged to console only
            resetState();
        }
    }

    // Waveform history for scrolling effect
    let waveformHistory = [];
    const MAX_WAVEFORM_HISTORY = 200; // Maximum history to keep

    /**
     * Setup Web Audio API analyzer for waveform visualization
     */
    function setupAudioAnalyzer() {
        try {
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            analyser.smoothingTimeConstant = 0.3;
            analyser.minDecibels = -90;
            analyser.maxDecibels = -10;

            const source = audioContext.createMediaStreamSource(stream);
            source.connect(analyser);

            // Initialize waveform history with zeros
            waveformHistory = new Array(MAX_WAVEFORM_HISTORY).fill(0);

            // Start waveform animation
            drawWaveform();
        } catch (error) {
            console.error('Failed to setup audio analyzer:', error);
        }
    }

    /**
     * Draw scrolling waveform visualization
     */
    function drawWaveform() {
        console.log('drawWaveform called', { isRecording, analyser: !!analyser, recordingOverlay: !!recordingOverlay });

        if (!isRecording || !analyser || !recordingOverlay) {
            console.log('drawWaveform aborted - missing requirements');
            return;
        }

        const canvas = recordingOverlay.querySelector('.dictation-waveform-canvas');
        if (!canvas) {
            console.log('Canvas not found');
            return;
        }

        const ctx = canvas.getContext('2d');
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        // Set canvas size based on parent
        const rect = canvas.parentElement.getBoundingClientRect();
        console.log('Canvas parent rect:', rect);

        const dpr = window.devicePixelRatio || 1;
        const displayWidth = Math.max(200, rect.width);
        const displayHeight = Math.max(32, rect.height);

        canvas.width = displayWidth * dpr;
        canvas.height = displayHeight * dpr;
        canvas.style.width = displayWidth + 'px';
        canvas.style.height = displayHeight + 'px';

        let lastUpdateTime = 0;
        const updateInterval = 50; // Update history every 50ms
        let frameCount = 0;

        function draw(timestamp) {
            if (!isRecording) return;

            animationId = requestAnimationFrame(draw);
            frameCount++;

            // Get audio data
            analyser.getByteTimeDomainData(dataArray);

            // Calculate current amplitude (RMS)
            let sumSquares = 0;
            for (let i = 0; i < bufferLength; i++) {
                const normalized = (dataArray[i] - 128) / 128;
                sumSquares += normalized * normalized;
            }
            const rms = Math.sqrt(sumSquares / bufferLength);
            const currentLevel = Math.min(1, rms * 4); // Amplify more

            // Log audio level occasionally
            if (frameCount % 60 === 0) {
                console.log('Audio level:', currentLevel.toFixed(3), 'RMS:', rms.toFixed(3));
            }

            // Update history at fixed interval for scrolling effect
            if (timestamp - lastUpdateTime > updateInterval) {
                waveformHistory.shift(); // Remove oldest
                waveformHistory.push(currentLevel); // Add newest
                lastUpdateTime = timestamp;
            }

            // Clear and set up context
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.scale(dpr, dpr);
            ctx.clearRect(0, 0, displayWidth, displayHeight);

            // Draw bars - slimmer bars spanning full width
            const barWidth = 1.5;
            const barGap = 2;
            const totalBarWidth = barWidth + barGap;
            const padding = 10; // Small padding on sides
            const availableWidth = displayWidth - (padding * 2);
            const barsToShow = Math.floor(availableWidth / totalBarWidth);
            const startX = padding;
            const centerY = displayHeight / 2;
            const maxBarHeight = displayHeight * 0.75;
            const minBarHeight = 2;

            // Use the calculated bar count for display
            const displayBarCount = Math.min(barsToShow, waveformHistory.length);

            for (let i = 0; i < displayBarCount; i++) {
                // Map to history - show the most recent bars that fit
                const historyIndex = Math.max(0, waveformHistory.length - displayBarCount) + i;
                const level = waveformHistory[historyIndex] || 0;

                // Calculate bar height
                const barHeight = Math.max(minBarHeight, level * maxBarHeight);

                const x = startX + i * totalBarWidth;
                const y = centerY - barHeight / 2;

                // Gradient opacity - newer bars (right) are brighter
                const opacity = 0.5 + (i / displayBarCount) * 0.5;

                // Draw bar extending from center
                ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(x, y, barWidth, barHeight, 1);
                } else {
                    ctx.rect(x, y, barWidth, barHeight);
                }
                ctx.fill();
            }
        }

        draw(0);
    }

    /**
     * Show recording UI overlay
     */
    function showRecordingUI() {
        if (!recordingOverlay || !textareaContainer) return;

        // Hide textarea and buttons
        promptInput.style.display = 'none';
        micButton.style.display = 'none';

        const sendButton = textareaContainer.querySelector('.send-button');
        if (sendButton) sendButton.style.display = 'none';

        // Show voice mode button in recording state
        const voiceModeButton = document.getElementById('voice-mode-button');
        if (voiceModeButton) {
            voiceModeButton.classList.remove('hidden');
            voiceModeButton.classList.add('recording');
        }

        // Show overlay
        recordingOverlay.classList.add('active');
    }

    /**
     * Hide recording UI overlay
     */
    function hideRecordingUI() {
        if (!recordingOverlay || !textareaContainer) return;

        // Show textarea and buttons
        promptInput.style.display = '';
        micButton.style.display = '';

        const sendButton = textareaContainer.querySelector('.send-button');
        if (sendButton) sendButton.style.display = '';

        // Remove recording state from voice mode button
        const voiceModeButton = document.getElementById('voice-mode-button');
        if (voiceModeButton) {
            voiceModeButton.classList.remove('recording');
        }

        // Update button visibility based on input state
        if (typeof window.updateSendButtonState === 'function') {
            window.updateSendButtonState();
        }

        // Hide overlay
        recordingOverlay.classList.remove('active');
    }

    /**
     * Stop recording audio
     */
    function stopRecording(shouldProcess = true) {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            if (shouldProcess) {
                mediaRecorder.onstop = async () => {
                    await processRecording();
                };
            }
            mediaRecorder.stop();
        }

        // Stop animation
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }

        // Close audio context
        if (audioContext) {
            audioContext.close().catch(() => {});
            audioContext = null;
            analyser = null;
        }

        // Stop all tracks
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }

        isRecording = false;

        if (shouldProcess) {
            // Show processing state briefly
            showProcessingState();
        } else {
            hideRecordingUI();
        }
    }

    /**
     * Show processing state in the overlay
     */
    function showProcessingState() {
        if (!recordingOverlay) return;

        const waveform = recordingOverlay.querySelector('.dictation-waveform');
        const controls = recordingOverlay.querySelector('.dictation-controls');

        if (waveform) {
            waveform.innerHTML = '<div class="dictation-processing"><i class="fa-solid fa-spinner fa-spin"></i><span>Processing...</span></div>';
        }
        if (controls) {
            controls.style.display = 'none';
        }
    }

    /**
     * Process the recorded audio
     */
    async function processRecording() {
        if (audioChunks.length === 0) {
            resetState();
            return;
        }

        const mimeType = (mediaRecorder && mediaRecorder.mimeType) ? mediaRecorder.mimeType : 'audio/webm';
        const audioBlob = new Blob(audioChunks, { type: mimeType });

        // Check if we have any audio data
        if (audioBlob.size < 100) {
            resetState();
            return;
        }

        console.log('Processing audio:', audioBlob.size, 'bytes,', mimeType);

        try {
            const text = await transcribeAudio(audioBlob, mimeType);

            if (text && text.trim()) {
                insertText(text.trim());
            }
            // Silent fail for no speech detected

        } catch (error) {
            console.error('Transcription failed:', error);
            // Silent fail - no toast notification
        }

        resetState();
    }

    /**
     * Send audio to backend for transcription
     */
    async function transcribeAudio(audioBlob, mimeType) {
        const formData = new FormData();

        // Determine file extension from MIME type
        let extension = 'webm';
        if (mimeType.includes('mp4')) extension = 'mp4';
        else if (mimeType.includes('ogg')) extension = 'ogg';
        else if (mimeType.includes('wav')) extension = 'wav';

        formData.append('audio', audioBlob, `recording.${extension}`);

        // Get CSRF token
        let csrfToken = null;
        if (typeof getCSRFToken === 'function') {
            csrfToken = getCSRFToken();
        } else {
            const metaTag = document.querySelector('meta[name="csrf-token"]');
            if (metaTag) {
                csrfToken = metaTag.getAttribute('content');
            }
        }

        const headers = {};
        if (csrfToken) {
            headers['X-CSRFToken'] = csrfToken;
        }

        const response = await fetch('/speech-to-text/groq', {
            method: 'POST',
            body: formData,
            headers: headers,
            credentials: 'same-origin'
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            console.error('Transcription error details:', errorData);
            throw new Error(errorData.error || `HTTP ${response.status}`);
        }

        const result = await response.json();

        if (result.status === 'success' && result.text) {
            console.log('Transcription result:', result.text.substring(0, 50) + '...');
            return result.text;
        }

        throw new Error(result.error || 'No text returned');
    }

    /**
     * Insert transcribed text into the input field
     */
    function insertText(text) {
        if (!promptInput) return;

        const currentValue = promptInput.value;
        const cursorPos = promptInput.selectionStart || currentValue.length;

        // Add space before if there's existing text and it doesn't end with space
        let insertText = text;
        if (currentValue.length > 0 && cursorPos > 0) {
            const charBefore = currentValue.charAt(cursorPos - 1);
            if (charBefore !== ' ' && charBefore !== '\n') {
                insertText = ' ' + text;
            }
        }

        // Insert at cursor position
        const before = currentValue.substring(0, cursorPos);
        const after = currentValue.substring(cursorPos);
        promptInput.value = before + insertText + after;

        // Move cursor to end of inserted text
        const newPos = cursorPos + insertText.length;
        promptInput.setSelectionRange(newPos, newPos);

        // Focus the input
        promptInput.focus();

        // Trigger input event for auto-grow and other listeners
        promptInput.dispatchEvent(new Event('input', { bubbles: true }));

        // Update send button state if function exists
        if (typeof window.updateSendButtonState === 'function') {
            window.updateSendButtonState();
        }
    }

    /**
     * Reset module state
     */
    function resetState() {
        isRecording = false;
        audioChunks = [];
        mediaRecorder = null;

        // Stop animation
        if (animationId) {
            cancelAnimationFrame(animationId);
            animationId = null;
        }

        // Close audio context
        if (audioContext) {
            audioContext.close().catch(() => {});
            audioContext = null;
            analyser = null;
        }

        // Reset waveform history
        waveformHistory = [];

        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }

        // Restore overlay to initial state
        restoreOverlay();

        // Hide recording UI
        hideRecordingUI();
    }

    /**
     * Restore overlay to initial state
     */
    function restoreOverlay() {
        if (!recordingOverlay) return;

        const waveform = recordingOverlay.querySelector('.dictation-waveform');
        const controls = recordingOverlay.querySelector('.dictation-controls');

        if (waveform) {
            waveform.innerHTML = '<canvas class="dictation-waveform-canvas"></canvas>';
        }
        if (controls) {
            controls.style.display = '';
        }
    }

    /**
     * Check if currently recording
     */
    function isCurrentlyRecording() {
        return isRecording;
    }

    /**
     * Cancel current recording without processing
     */
    function cancelRecording() {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            // Remove the onstop handler to prevent processing
            mediaRecorder.onstop = null;
            mediaRecorder.stop();
        }
        resetState();
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API
    return {
        init,
        isRecording: isCurrentlyRecording,
        cancel: cancelRecording
    };

})();

// Export for potential module usage
if (typeof window !== 'undefined') {
    window.DictationModule = DictationModule;
}
