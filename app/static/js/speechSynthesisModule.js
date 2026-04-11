let isSpeaking = false;
let isFetchingAudio = false;
let activeButton = null;
let ttsAbortController = null;
let ttsAudioContext = null;
let activeBufferSource = null;

function updateButtonIcon(button, state) {
    if (!button) return;

    if (!button.dataset.defaultIconHtml) {
        button.dataset.defaultIconHtml = button.innerHTML;
    }

    if (state === 'stop') {
        button.innerHTML = '<i class="fas fa-stop"></i>';
        if (button._readOptionLabel) {
            button.dataset.readOptionDefault = button.dataset.readOptionDefault || button._readOptionLabel.textContent || 'Read Aloud';
            button._readOptionLabel.textContent = 'Stop Reading';
        }
    } else {
        button.innerHTML = button.dataset.defaultIconHtml || button.innerHTML;
        if (button._readOptionLabel) {
            const label = button.dataset.readOptionDefault || 'Read Aloud';
            button._readOptionLabel.textContent = label;
        }
    }
}

function getCsrfToken() {
    if (typeof document === 'undefined') return '';
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

function stopActiveAudio() {
    if (ttsAbortController) {
        ttsAbortController.abort();
        ttsAbortController = null;
    }
    if (activeBufferSource) {
        try {
            activeBufferSource.stop();
        } catch (_) {
            // ignore
        }
        if (typeof activeBufferSource.disconnect === 'function') {
            activeBufferSource.disconnect();
        }
        activeBufferSource = null;
    }
    isSpeaking = false;
    isFetchingAudio = false;
    if (activeButton) {
        updateButtonIcon(activeButton, 'play');
        activeButton = null;
    }
}

function buildTtsError(payload, status) {
    if (!payload) return `Text-to-speech request failed${status ? ` (HTTP ${status})` : ''}.`;
    if (typeof payload === 'string') return payload;
    if (typeof payload.error === 'string') return payload.error;
    if (payload.error && typeof payload.error.message === 'string') return payload.error.message;
    try {
        return JSON.stringify(payload);
    } catch (_) {
        return `Text-to-speech request failed${status ? ` (HTTP ${status})` : ''}.`;
    }
}

const DEFAULT_TTS_VOICE = 'autumn';
const DEFAULT_TTS_FORMAT = 'wav';
const MAX_TTS_CHARACTERS = 10000;

function parseMessageSources(messageElement) {
    if (!messageElement) return [];
    const raw = messageElement.dataset?.sourceResults;
    if (!raw) return [];
    try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
        console.warn('Failed to parse stored sources for message', messageElement.id, error);
        return [];
    }
}

function extractDomain(entry) {
    if (!entry) return '';
    if (entry.source_domain) return entry.source_domain;
    if (entry.link) {
        try {
            const url = new URL(entry.link);
            return url.hostname;
        } catch (_) {
            return '';
        }
    }
    return '';
}

function buildSourcesButton(messageElement, messageId, messageSources = null) {
    const sources = Array.isArray(messageSources) ? messageSources : parseMessageSources(messageElement);
    const uniqueDomains = [...new Set(sources.map(extractDomain).filter(Boolean))];
    if (!uniqueDomains.length) {
        return null;
    }

    const sourcesBtn = document.getElementById("sources-btn");
    const sourcesInline = document.createElement('button');
    sourcesInline.type = 'button';
    sourcesInline.className = 'action-sources-button';

    const faviconsWrapper = document.createElement('div');
    faviconsWrapper.className = 'action-sources-favicons';
    const label = document.createElement('span');
    label.textContent = 'Sources';

    uniqueDomains.forEach(domain => {
        const img = document.createElement('img');
        img.src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;
        img.alt = domain;
        img.className = 'source-icon';
        img.onerror = () => img.remove();
        faviconsWrapper.appendChild(img);
    });

    sourcesInline.append(faviconsWrapper, label);
    sourcesInline.addEventListener('click', () => {
        const sidebarOpen = document.body.classList.contains('right-sidebar-open');
        const activeSourceId = sourcesBtn?.dataset?.activeSourceMessage;
        const isSameMessageActive = activeSourceId && activeSourceId === messageId;

        if (sidebarOpen && isSameMessageActive) {
            if (typeof toggleRightSidebar === 'function') {
                toggleRightSidebar(false);
            }
            return;
        }

        if (typeof window.updateSourcesButton === 'function') {
            window.updateSourcesButton(sources);
        }
        if (typeof populateRightSidebar === 'function') {
            populateRightSidebar(sources);
        }

        if (sourcesBtn) {
            sourcesBtn.dataset.activeSourceMessage = messageId;
        }

        if (typeof toggleRightSidebar === 'function') {
            toggleRightSidebar(true);
        }
    });

    return sourcesInline;
}

async function requestGroqTts(text, format = DEFAULT_TTS_FORMAT, voice = DEFAULT_TTS_VOICE, lang = '') {
    const csrfToken = getCsrfToken();
    const controller = new AbortController();
    ttsAbortController = controller;
    try {
        const body = { text, format, voice };
        if (lang) body.lang = lang;
        const response = await fetch('/tts/groq', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {})
            },
            body: JSON.stringify(body),
            signal: controller.signal
        });
        const contentType = response.headers.get('content-type') || '';
        if (!response.ok) {
            let payload = null;
            if (contentType.includes('application/json')) {
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = null;
                }
            }
            const message = buildTtsError(payload, response.status);
            const error = new Error(message);
            error.status = response.status;
            error.payload = payload;
            throw error;
        }

        const selectedVoice = response.headers.get('x-tts-voice') || '';
        const selectedLang = response.headers.get('x-tts-lang') || '';

        if (contentType.includes('application/json')) {
            const payload = await response.json();
            if (!payload || !payload.audio_base64) {
                throw new Error('Audio payload is empty.');
            }
            const arrayBuffer = base64ToArrayBuffer(payload.audio_base64);
            return { audio: arrayBuffer, voice: selectedVoice, lang: selectedLang };
        }

        const arrayBuffer = await response.arrayBuffer();
        return { audio: arrayBuffer, voice: selectedVoice, lang: selectedLang };
    } finally {
        ttsAbortController = null;
    }
}

function ensureAudioContext() {
    if (!ttsAudioContext) {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
            throw new Error('Web Audio API not supported in this browser.');
        }
        ttsAudioContext = new AudioContext();
    }
    if (ttsAudioContext.state === 'suspended') {
        ttsAudioContext.resume();
    }
    return ttsAudioContext;
}

function base64ToArrayBuffer(base64Data) {
    const binaryString = atob(base64Data);
    const length = binaryString.length;
    const bytes = new Uint8Array(length);
    for (let i = 0; i < length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
}

async function playAudioBuffer(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'audio/wav' });
    const blobUrl = URL.createObjectURL(blob);

    return new Promise((resolve, reject) => {
        const audio = document.createElement('audio');
        audio.src = blobUrl;
        audio.volume = 1.0;
        audio.setAttribute('playsinline', '');
        document.body.appendChild(audio);

        activeBufferSource = {
            stop: () => { audio.pause(); audio.remove(); }
        };
        isSpeaking = true;

        audio.addEventListener('ended', () => {
            URL.revokeObjectURL(blobUrl);
            audio.remove();
            activeBufferSource = null;
            isSpeaking = false;
            resolve();
        }, { once: true });

        audio.addEventListener('error', (e) => {
            console.error('TTS playback error:', audio.error);
            URL.revokeObjectURL(blobUrl);
            audio.remove();
            stopActiveAudio();
            reject(e);
        }, { once: true });

        const p = audio.play();
        if (p && p.catch) p.catch((e) => {
            console.error('TTS play() blocked:', e.message);
            URL.revokeObjectURL(blobUrl);
            audio.remove();
            reject(e);
        });
    });
}

function splitIntoChunks(text, maxLen = 400) {
    // Split by sentences, then group into chunks under maxLen
    const sentences = text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [text];
    const chunks = [];
    let current = '';
    for (const s of sentences) {
        if (current.length + s.length > maxLen && current) {
            chunks.push(current.trim());
            current = s;
        } else {
            current += s;
        }
    }
    if (current.trim()) chunks.push(current.trim());
    return chunks.length ? chunks : [text];
}

let _ttsAborted = false;

async function speakText(text, button) {
    if (!text) return;
    const sanitized = text.replace(/^XelasGPT\s*/i, '').replace(/\*/g, '').replace(/```[\s\S]*?```/g, '').replace(/[#>`|]/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').trim();
    if (!sanitized) return;

    if (isSpeaking || isFetchingAudio) {
        stopActiveAudio();
        _ttsAborted = true;
        return;
    }

    _ttsAborted = false;
    isFetchingAudio = true;
    activeButton = button;
    updateButtonIcon(button, 'stop');


    try {
        const chunks = splitIntoChunks(sanitized, 400);
        let lockedVoice = null;
        let lockedLang = null;

        for (let i = 0; i < chunks.length; i++) {
            if (_ttsAborted) break;

            const voice = lockedVoice || DEFAULT_TTS_VOICE;
            const result = await requestGroqTts(chunks[i], DEFAULT_TTS_FORMAT, voice, lockedLang || '');
            if (_ttsAborted) break;

            // Lock voice + language from first chunk for consistency
            if (!lockedVoice && result.voice) lockedVoice = result.voice;
            if (!lockedLang && result.lang) lockedLang = result.lang;

            isFetchingAudio = false;
            await playAudioBuffer(result.audio);
            if (_ttsAborted) break;
        }
        isFetchingAudio = false;
        stopActiveAudio();
    } catch (error) {
        isFetchingAudio = false;
        console.error('Failed to play TTS audio:', error);
        const message = error && error.message ? error.message : 'Unable to play audio.';
        if (window.chatManager?.showNotification) {
            window.chatManager.showNotification(message, 'error');
        }
        stopActiveAudio();
    }
}

function resolveModelLabel() {
    const manager = window.chatManager;
    if (manager) {
        const sessionId = manager.activeSessionId;
        const session = sessionId ? manager.chatHistory?.[sessionId] : null;
        if (session && typeof session.model_label === 'string' && session.model_label.trim()) {
            return session.model_label.trim();
        }
        if (typeof manager.getActiveModelLabel === 'function') {
            const label = manager.getActiveModelLabel();
            if (label) return label;
        }
    }
    const select = document.getElementById('model');
    if (select && select.options && select.selectedIndex >= 0) {
        return select.options[select.selectedIndex].textContent?.trim() || 'Unknown';
    }
    return 'Unknown';
}

function appendActionButtons(container, messageId, { skipSourcesRefresh = false } = {}) {
    if (!container || !messageId) {
        console.warn('Container or messageId is missing:', { container, messageId });
        return;
    }

    // Skip if email composer has taken over this container
    if (container.dataset && container.dataset.emailComposer === 'true') {
        return;
    }

    let messageElement = document.getElementById(messageId);
    if (!messageElement) {
        // Fallback: use the nearest message wrapper
        messageElement = container.closest('.message');
        if (messageElement && !messageElement.id) {
            messageElement.id = messageId;
        }
    }
    if (!messageElement) {
        console.warn('Message element not found:', messageId);
        return;
    }

    const classTokens = (messageElement.className || '')
        .split(/\s+/)
        .map(token => token.trim().toLowerCase())
        .filter(Boolean);
    const senderAttr = (messageElement.dataset?.sender || '').trim().toLowerCase();
    const isAiMessage = classTokens.includes('ai-message') ||
        classTokens.includes('assistant-message') ||
        senderAttr === 'ai' ||
        senderAttr === 'assistant';
    if (!isAiMessage) {
        return;
    }

    const targetWrapper = messageElement.querySelector('.text-message-container:last-of-type') ||
        messageElement.querySelector('.message-content') ||
        container;
    const existingButtons = messageElement.querySelectorAll('.action-buttons');
    existingButtons.forEach(btn => btn.remove());

    const buttonsContainer = document.createElement("div");
    buttonsContainer.className = "action-buttons";
    buttonsContainer.dataset.forMessage = messageId;

    const getMessageText = () => {
        const textNode = messageElement.querySelector('.text-message');
        if (textNode && (textNode.innerText || textNode.textContent)) {
            return textNode.innerText || textNode.textContent || '';
        }
        const contentNode = messageElement.querySelector('.message-content');
        if (contentNode && (contentNode.innerText || contentNode.textContent)) {
            return contentNode.innerText || contentNode.textContent || '';
        }
        return messageElement.textContent || '';
    };

    const primaryRow = document.createElement('div');
    primaryRow.className = 'action-buttons-row';
    buttonsContainer.appendChild(primaryRow);

    const createIconButton = (iconClass, tooltipText, handler) => {
        const btn = document.createElement('button');
        btn.className = 'action-btn';
        const icon = document.createElement('i');
        icon.className = iconClass;
        icon.setAttribute('aria-hidden', 'true');
        btn.appendChild(icon);

        let tooltip = null;
        if (typeof tooltipText === 'string' && tooltipText.trim().length) {
            tooltip = createTooltip(tooltipText);
            btn.appendChild(tooltip);
            const showTip = () => { tooltip.style.visibility = "visible"; tooltip.style.opacity = "1"; };
            const hideTip = () => { tooltip.style.visibility = "hidden"; tooltip.style.opacity = "0"; };
            btn.onmouseover = showTip;
            btn.onmouseout = hideTip;
            // Mobile: show on touch then auto-hide after 1.5s
            btn.addEventListener('touchstart', () => {
                showTip();
                setTimeout(hideTip, 1500);
            }, { passive: true });
        }

        btn.addEventListener('click', handler);
        return btn;
    };

    const buttonCopy = createIconButton('fa-regular fa-copy', 'Copy', () => {
        const textContent = getMessageText();
        navigator.clipboard.writeText(textContent)
            .then(() => console.log('Content copied'))
            .catch(err => console.error('Failed to copy:', err));
    });

    // Helper: persist feedback to DOM + chatManager message data + backend
    const _persistFeedback = (type) => {
        messageElement.dataset.feedback = type;
        const mgr = window.chatManager;
        if (!mgr?.activeSessionId) return;
        const msgKey = messageElement.dataset.messageKey || messageId;
        try {
            const sess = mgr.chatHistory?.[mgr.activeSessionId];
            if (!sess) return;
            // Find in session.messages or active page messages
            const pages = sess.pages || [];
            const msgs = (pages.length > 1)
                ? (mgr.getActivePage?.(sess)?.messages || [])
                : (sess.messages || []);
            const msg = msgs.find(m => m && (m.response_id === msgKey || m.id === msgKey));
            if (msg) msg.user_feedback = type;
            mgr.markSessionDirty?.(mgr.activeSessionId);
            // Flush immediately so it persists before user might refresh
            mgr.saveSession?.(mgr.activeSessionId, { silent: true })?.catch?.(() => {});
        } catch(e) {}
    };

    const _resetBoth = () => {
        buttonLike.querySelector('i').className = 'fa-regular fa-face-smile';
        buttonLike.style.color = '';
        buttonDislike.querySelector('i').className = 'fa-regular fa-face-frown';
        buttonDislike.style.color = '';
    };

    const _sendFeedback = (feedback) => {
        const modelId = messageElement.dataset.modelId || '';
        const taskType = messageElement.dataset.taskType || 'general_chat';
        fetch('/chat/feedback', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken()},
            body: JSON.stringify({model_id: modelId || 'unknown', task_type: taskType, feedback})
        }).catch(err => console.warn('Feedback failed:', err));
    };

    const buttonLike = createIconButton('fa-regular fa-face-smile', 'Good response', () => {
        const current = messageElement.dataset.feedback;
        _resetBoth();
        if (current === 'positive') {
            // Toggle off — clear feedback
            _persistFeedback('');
        } else {
            buttonLike.querySelector('i').className = 'fa-solid fa-face-smile';
            buttonLike.style.color = 'var(--accent-color, #4CAF50)';
            _sendFeedback('positive');
            _persistFeedback('positive');
        }
    });

    const buttonDislike = createIconButton('fa-regular fa-face-frown', 'Bad response', () => {
        const current = messageElement.dataset.feedback;
        _resetBoth();
        if (current === 'negative') {
            // Toggle off — clear feedback
            _persistFeedback('');
        } else {
            buttonDislike.querySelector('i').className = 'fa-solid fa-face-frown';
            buttonDislike.style.color = 'var(--error-color, #f44336)';
            _sendFeedback('negative');
            _persistFeedback('negative');
        }
    });

    // Restore feedback state from previous session data
    const _existingFeedback = messageElement.dataset.feedback;
    if (_existingFeedback === 'positive') {
        buttonLike.querySelector('i').className = 'fa-solid fa-face-smile';
        buttonLike.style.color = 'var(--accent-color, #4CAF50)';
    } else if (_existingFeedback === 'negative') {
        buttonDislike.querySelector('i').className = 'fa-solid fa-face-frown';
        buttonDislike.style.color = 'var(--error-color, #f44336)';
    }

    const buttonShare = createIconButton('fa-regular fa-share-from-square', 'Share', async () => {
        const textContent = getMessageText();
        if (navigator.share) {
            try {
                await navigator.share({ text: textContent });
            } catch (error) {
                console.error('Share canceled or failed:', error);
            }
        } else if (window.chatManager?.activeSessionId) {
            window.chatManager.shareSession(window.chatManager.activeSessionId).catch(console.error);
        } else {
            navigator.clipboard.writeText(textContent).catch(console.error);
        }
    });

    const modelLabel = resolveModelLabel();

    const buttonRefresh = createIconButton('fa-solid fa-rotate-right', `Try Again\nUsed model: ${modelLabel}`, () => {
        if (typeof retryAnswer === 'function') {
            const originalPrompt = messageElement.dataset.originalPrompt || "";
            retryAnswer(originalPrompt, messageId);
        }
    });

    primaryRow.append(buttonCopy, buttonLike, buttonDislike, buttonShare, buttonRefresh);

    // Auto model details: show in-theme as an info button in the action bar
    if ((messageElement.dataset?.autoSelected || '').toString() === 'true') {
        const infoBtn = createIconButton('fa-solid fa-circle-info', '', () => {
            // Toggle a clean popover above the icon
            const existingPopover = document.querySelector('.ai-meta-popover');
            if (existingPopover) {
                existingPopover.remove();
            }

            // If the previous popover belonged to this button, we're done (toggle off)
            if (existingPopover && existingPopover.dataset?.anchorId === messageId) {
                return;
            }

            const providerLabel = messageElement.dataset.modelProviderLabel || messageElement.dataset.modelProvider || '';
            const label = messageElement.dataset.modelLabel || messageElement.dataset.modelId || 'Unknown model';
            const task = messageElement.dataset.taskType ? ` • task: ${messageElement.dataset.taskType}` : '';
            const text = `Auto → ${label}${providerLabel ? ` (${providerLabel})` : ''}${task}`;

            const popover = document.createElement('div');
            popover.className = 'ai-meta-popover';
            popover.dataset.anchorId = messageId;
            popover.textContent = text;

            document.body.appendChild(popover);

            const btnRect = infoBtn.getBoundingClientRect();
            const popRect = popover.getBoundingClientRect();

            // Place centered above the icon
            const top = window.scrollY + btnRect.top - popRect.height - 10;
            const left = window.scrollX + (btnRect.left + btnRect.width / 2) - (popRect.width / 2);

            popover.style.top = `${Math.max(8, top)}px`;
            popover.style.left = `${Math.max(8, Math.min(left, window.scrollX + window.innerWidth - popRect.width - 8))}px`;

            const close = (ev) => {
                // Don't close when clicking the icon itself (it will toggle)
                if (ev && (ev.target === infoBtn || infoBtn.contains(ev.target))) return;
                popover.remove();
                document.removeEventListener('click', close);
                document.removeEventListener('scroll', close, true);
            };
            setTimeout(() => {
                document.addEventListener('click', close);
                document.addEventListener('scroll', close, true);
            }, 0);
        });

        primaryRow.appendChild(infoBtn);
    }

    const moreWrapper = document.createElement('div');
    moreWrapper.className = 'action-more-wrapper';

    const moreButton = document.createElement('button');
    moreButton.className = 'action-more-button';
    moreButton.type = 'button';
    moreButton.innerHTML = '&hellip;';
    moreButton.setAttribute('aria-label', 'More actions');

    const moreMenu = document.createElement('div');
    moreMenu.className = 'action-more-menu';

    const iconDefinitions = {
        read: {
            paths: [
                "M11 5 6 9H2v6h4l5 4V5Z",
                "M19 5c1.5 1.6 2 3.5 2 5s-.5 3.4-2 5",
                "M15.54 8.46A5 5 0 0 1 17 12a5 5 0 0 1-1.46 3.54"
            ]
        },
        report: {
            paths: [
                "M4 4h7l1 2h7l-2 4h-6l-1-2H4Z",
                "M4 4v16"
            ]
        },
        branch: {
            paths: [
                "M6 3v12a3 3 0 1 0 3 3",
                "M6 9h6a3 3 0 1 0 3-3"
            ],
            circles: [
                { cx: 6, cy: 6, r: 2 },
                { cx: 18, cy: 6, r: 2 },
                { cx: 6, cy: 18, r: 2 }
            ]
        }
    };

    const createSvgIcon = (definition) => {
        if (!definition) return null;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '1.6');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');

        (definition.paths || []).forEach((d) => {
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', d);
            svg.appendChild(path);
        });

        (definition.circles || []).forEach(({ cx, cy, r }) => {
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', cx);
            circle.setAttribute('cy', cy);
            circle.setAttribute('r', r);
            svg.appendChild(circle);
        });

        return svg;
    };

    const createMenuOption = (label, handler, iconKey) => {
        const option = document.createElement('button');
        option.type = 'button';
        const icon = createSvgIcon(iconDefinitions[iconKey]);
        const textSpan = document.createElement('span');
        textSpan.textContent = label;
        if (icon) {
            option.append(icon, textSpan);
        } else {
            option.append(textSpan);
        }
        option.addEventListener('click', () => {
            handler();
            moreMenu.classList.remove('open');
        });
        return option;
    };

    const readMenuOption = createMenuOption('Read Aloud', () => {
        const textContent = getMessageText();
        if (typeof speakText === 'function') {
            speakText(textContent, moreButton);
        }
    }, 'read');

    const readOptionLabel = readMenuOption.querySelector('span');
    if (readOptionLabel) {
        moreButton._readOptionLabel = readOptionLabel;
        moreButton.dataset.readOptionDefault = readOptionLabel.textContent;
    }

    const reportMenuOption = createMenuOption('Report Message', () => {
        console.log('Report requested for message:', messageId);
        if (window.chatManager?.showNotification) {
            window.chatManager.showNotification('Message flagged for review', 'success');
        }
    }, 'report');

    const branchMenuOption = createMenuOption('Branch in New Chat', async () => {
        const textContent = getMessageText();
        if (!textContent) return;
        try {
            if (window.chatManager?.createNewChat) {
                await window.chatManager.createNewChat(null, { notifySuccess: false });
                if (typeof window.sendMessage === 'function') {
                    window.sendMessage(textContent);
                }
            }
        } catch (error) {
            console.error('Failed to branch chat:', error);
        }
    }, 'branch');

    // --- Smart timestamp header ---
    const timeHeader = document.createElement('div');
    timeHeader.className = 'action-more-time-header';

    function formatSmartTime(isoString) {
        if (!isoString) return '';
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return '';
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / 86400000);
        const timeStr = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });

        if (diffDays === 0 && now.getDate() === date.getDate()) {
            return 'Today ' + timeStr;
        }
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (date.getDate() === yesterday.getDate() &&
            date.getMonth() === yesterday.getMonth() &&
            date.getFullYear() === yesterday.getFullYear()) {
            return 'Yesterday ' + timeStr;
        }
        if (diffDays < 7) {
            const dayName = date.toLocaleDateString([], { weekday: 'long' });
            return dayName + ' ' + timeStr;
        }
        const dateStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        if (date.getFullYear() !== now.getFullYear()) {
            return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' + timeStr;
        }
        return dateStr + ' ' + timeStr;
    }

    function updateTimeHeader() {
        const ts = messageElement?.dataset?.timestamp;
        timeHeader.textContent = formatSmartTime(ts);
    }
    updateTimeHeader();

    // Update the timestamp each time the menu opens
    moreButton._updateTimeHeader = updateTimeHeader;

    moreMenu.append(timeHeader, readMenuOption, reportMenuOption, branchMenuOption);

    moreButton.addEventListener('click', (event) => {
        if (activeButton === moreButton && (isSpeaking || isFetchingAudio)) {
            event.preventDefault();
            event.stopPropagation();
            stopActiveAudio();
            return;
        }

        event.stopPropagation();
        const isOpen = moreMenu.classList.toggle('open');
        if (isOpen) {
            // Refresh the smart timestamp each time the menu opens
            if (moreButton._updateTimeHeader) moreButton._updateTimeHeader();
            const closeMenu = (e) => {
                if (!moreMenu.contains(e.target) && e.target !== moreButton) {
                    moreMenu.classList.remove('open');
                    document.removeEventListener('click', closeMenu);
                    document.removeEventListener('touchstart', closeMenuTouch);
                }
            };
            const closeMenuTouch = (e) => {
                if (!moreMenu.contains(e.target) && e.target !== moreButton) {
                    moreMenu.classList.remove('open');
                    document.removeEventListener('click', closeMenu);
                    document.removeEventListener('touchstart', closeMenuTouch);
                }
            };
            document.addEventListener('click', closeMenu);
            document.addEventListener('touchstart', closeMenuTouch, { passive: true });
        }
    });

    moreWrapper.append(moreButton, moreMenu);
    primaryRow.appendChild(moreWrapper);

    targetWrapper.appendChild(buttonsContainer);
}

// Function to create tooltip
function createTooltip(text) {
    const tooltip = document.createElement('div');
    tooltip.style.position = 'absolute';
    tooltip.style.top = '100%'; // Position below the button
    tooltip.style.left = '50%';
    tooltip.style.transform = 'translateX(-50%)';
    tooltip.style.backgroundColor = 'rgba(0, 0, 0, 0.8)';
    tooltip.style.color = 'white';
    tooltip.style.padding = '6px 10px'; // Increased padding for better visibility
    tooltip.style.borderRadius = '4px'; // Slightly larger border radius for modern look
    tooltip.style.whiteSpace = 'nowrap';
    tooltip.style.visibility = 'hidden';
    tooltip.style.opacity = '0';
    tooltip.style.transition = 'visibility 0s, opacity 0.3s ease-in-out';
    tooltip.style.fontSize = '14px'; // Adjusted font size
    tooltip.style.marginTop = '8px'; // Space between button and tooltip

    // Create the arrow
    const arrow = document.createElement("div");
    arrow.style.position = "absolute";
    arrow.style.top = "-5px"; // Position the arrow just above the tooltip
    arrow.style.left = "50%";
    arrow.style.transform = "translateX(-50%)";
    arrow.style.width = "0";
    arrow.style.height = "0";
    arrow.style.borderLeft = "5px solid transparent";
    arrow.style.borderRight = "5px solid transparent";
    arrow.style.borderBottom = "5px solid black";

    tooltip.appendChild(arrow);

    if (typeof text === 'string' && text.includes('\n')) {
        const [primary, secondary] = text.split('\n');
        const primarySpan = document.createElement('span');
        primarySpan.textContent = primary.trim();
        primarySpan.style.display = 'block';
        const secondarySpan = document.createElement('span');
        secondarySpan.textContent = secondary.trim();
        secondarySpan.style.display = 'block';
        secondarySpan.style.fontSize = '12px';
        secondarySpan.style.color = '#cccccc';
        tooltip.append(primarySpan, secondarySpan);
    } else {
        const textSpan = document.createElement('span');
        textSpan.textContent = text;
        tooltip.appendChild(textSpan);
    }

    return tooltip;
}

// Retry answer - creates branch for pagination and generates new response
function retryAnswer(originalPrompt, messageId) {
    if (!originalPrompt) {
        console.error("No original prompt for retry");
        return;
    }

    const messageElement = document.getElementById(messageId);
    if (!messageElement) {
        console.error("No message element found for messageId:", messageId);
        return;
    }

    // Get the message key - strip any ai-/user- prefix
    let messageKey = messageElement.dataset.messageKey || messageId;
    messageKey = messageKey.replace(/^(ai-|user-)/, '');

    // Try to branch the session using branchSessionForRetry (handles AI message lookup)
    let branchResult = null;
    if (window.chatManager) {
        if (typeof window.chatManager.branchSessionForRetry === 'function') {
            branchResult = window.chatManager.branchSessionForRetry(messageId, originalPrompt);
        } else if (typeof window.chatManager.branchSessionAtMessage === 'function') {
            branchResult = window.chatManager.branchSessionAtMessage(messageKey, originalPrompt);
        }
    }

    // Send message to generate new response
    if (typeof window.sendMessage === 'function') {
        if (branchResult) {
            window.sendMessage(branchResult.text || originalPrompt, branchResult.messageId, {
                skipUserMessageRender: true,
                reuseMessageIdForResponse: false,
                forcePageId: branchResult.pageId
            });

            // Update pagination controls after response starts
            setTimeout(() => {
                if (window.chatManager && typeof window.chatManager.renderInlinePageAnchors === 'function') {
                    window.chatManager.renderInlinePageAnchors();
                }
            }, 500);
        } else {
            // Fallback: remove old message and regenerate (no pagination)
            console.warn("Branch failed, falling back to simple regenerate");
            messageElement.remove();
            window.sendMessage(originalPrompt, null, {
                skipUserMessageRender: true
            });
        }
    }
}

// Mock implementation of hideOriginalInput function (you may already have this)
function hideOriginalInput(messageId) {
    const messageElement = document.getElementById(messageId);
    if (!messageElement) return;

    messageElement.style.display = 'none'; // Hide the message element

    const originalPrompt = messageElement.dataset.originalPrompt;
    if (originalPrompt) {
        const promptElements = document.querySelectorAll(`[data-original-prompt="${originalPrompt}"]`);
        promptElements.forEach(element => {
            element.style.display = 'none'; // Hide all elements with the original prompt
        });
    }
}

// Function to create copy button
function createCopyButton(getMessageFunc) {
    const buttonCopy = document.createElement("button");
    buttonCopy.innerHTML = '<i class="fas fa-copy"></i>'; 
    buttonCopy.style.position = "relative"; // Ensure button's position is relative for tooltip

    // Create tooltip element
    const tooltip = createTooltip("Copy");
    buttonCopy.appendChild(tooltip);

    // Show tooltip on hover
    buttonCopy.onmouseover = () => {
        tooltip.style.visibility = "visible";
        tooltip.style.opacity = "1";
    };

    // Hide tooltip when not hovering
    buttonCopy.onmouseout = () => {
        tooltip.style.visibility = "hidden";
        tooltip.style.opacity = "0";
    };

    buttonCopy.onclick = async () => {
        // Retrieve and clean the message text
        let messageText = getMessageFunc();
        // Remove 'XelasGPT' and any spaces following it
        messageText = messageText.replace(/^XelasGPT\s*/i, '').replace(/\*/g, ''); // Also removing asterisks if needed

        // Copy the cleaned text to the clipboard
        await copyText(messageText);
        buttonCopy.innerHTML = '<i class="fas fa-check"></i>';
        setTimeout(() => {
            buttonCopy.innerHTML = '<i class="fas fa-copy"></i>'; 
        }, 2000);
    };

    return buttonCopy;
}

// Function to copy text using the Clipboard API
async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        console.log('Text copied to clipboard');
    } catch (err) {
        console.error('Failed to copy text: ', err);
    }
}
