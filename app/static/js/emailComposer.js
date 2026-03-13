/**
 * Email Composer Module
 * Renders an email composer card when AI generates email content
 * Provides send options via Gmail, Outlook, or default mail app
 */

(function() {
    'use strict';

    // Encode for URL parameters
    function enc(value) {
        return encodeURIComponent(value || '');
    }

    // Create the send modal (singleton)
    let sendModal = null;
    let currentEmailData = null;

    function createSendModal() {
        if (sendModal) return sendModal;

        const overlay = document.createElement('div');
        overlay.className = 'email-send-modal-overlay';
        overlay.id = 'email-send-modal-overlay';
        overlay.innerHTML = `
            <div class="email-send-modal">
                <div class="email-send-modal-header">
                    <div class="email-send-modal-title">Send Email</div>
                    <div class="email-send-modal-subtitle">Choose how to send your email</div>
                </div>

                <div class="email-send-options">
                    <div class="email-send-option" data-provider="gmail">
                        <div class="email-send-option-icon gmail">
                            <i class="fas fa-envelope"></i>
                        </div>
                        <div class="email-send-option-text">
                            <div class="email-send-option-name">Gmail</div>
                            <div class="email-send-option-desc">Open in Gmail web compose</div>
                        </div>
                        <div class="email-send-option-arrow">
                            <i class="fas fa-external-link-alt"></i>
                        </div>
                    </div>

                    <div class="email-send-option" data-provider="outlook">
                        <div class="email-send-option-icon outlook">
                            <i class="fas fa-envelope"></i>
                        </div>
                        <div class="email-send-option-text">
                            <div class="email-send-option-name">Outlook</div>
                            <div class="email-send-option-desc">Open in Outlook web compose</div>
                        </div>
                        <div class="email-send-option-arrow">
                            <i class="fas fa-external-link-alt"></i>
                        </div>
                    </div>

                    <div class="email-send-option" data-provider="default">
                        <div class="email-send-option-icon default">
                            <i class="fas fa-paper-plane"></i>
                        </div>
                        <div class="email-send-option-text">
                            <div class="email-send-option-name">Default Email App</div>
                            <div class="email-send-option-desc">Open with mailto: link</div>
                        </div>
                        <div class="email-send-option-arrow">
                            <i class="fas fa-external-link-alt"></i>
                        </div>
                    </div>
                </div>

                <div class="email-send-modal-footer">
                    <button class="email-send-modal-close">Cancel</button>
                </div>
            </div>
        `;

        // Close on overlay click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                closeSendModal();
            }
        });

        // Close button
        overlay.querySelector('.email-send-modal-close').addEventListener('click', closeSendModal);

        // Send options
        overlay.querySelectorAll('.email-send-option').forEach(option => {
            option.addEventListener('click', () => {
                const provider = option.dataset.provider;
                if (currentEmailData) {
                    sendEmail(provider, currentEmailData);
                }
                closeSendModal();
            });
        });

        document.body.appendChild(overlay);
        sendModal = overlay;
        return overlay;
    }

    function openSendModal(emailData) {
        currentEmailData = emailData;
        const modal = createSendModal();
        modal.classList.add('active');
    }

    function closeSendModal() {
        if (sendModal) {
            sendModal.classList.remove('active');
        }
        currentEmailData = null;
    }

    // Send email via different providers
    function sendEmail(provider, { to, subject, body }) {
        switch (provider) {
            case 'gmail':
                openGmailCompose(to, subject, body);
                break;
            case 'outlook':
                openOutlookCompose(to, subject, body);
                break;
            case 'default':
                openDefaultMailApp(to, subject, body);
                break;
        }
    }

    function openGmailCompose(to, subject, body) {
        const url = `https://mail.google.com/mail/?view=cm&fs=1&to=${enc(to)}&su=${enc(subject)}&body=${enc(body)}`;
        window.open(url, '_blank', 'noopener,noreferrer');
    }

    function openOutlookCompose(to, subject, body) {
        const url = `https://outlook.office.com/mail/deeplink/compose?to=${enc(to)}&subject=${enc(subject)}&body=${enc(body)}`;
        window.open(url, '_blank', 'noopener,noreferrer');
    }

    function openDefaultMailApp(to, subject, body) {
        const mailto = `mailto:${enc(to)}?subject=${enc(subject)}&body=${enc(body)}`;
        window.location.href = mailto;
    }

    // Copy email to clipboard
    async function copyEmailToClipboard(emailData) {
        const text = `To: ${emailData.to || '(recipient)'}\nSubject: ${emailData.subject}\n\n${emailData.body}`;
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.error('Failed to copy email:', err);
            return false;
        }
    }

    // Stream text into an input element character by character
    function streamText(element, text, speed = 15) {
        return new Promise((resolve) => {
            if (!text) {
                resolve();
                return;
            }
            let index = 0;

            function typeChar() {
                if (index < text.length) {
                    element.value += text[index];
                    index++;
                    setTimeout(typeChar, speed);
                } else {
                    resolve();
                }
            }
            typeChar();
        });
    }

    // Stream text into a contenteditable element
    function streamTextContentEditable(element, text, speed = 15) {
        return new Promise((resolve) => {
            if (!text) {
                resolve();
                return;
            }
            let index = 0;

            function typeChar() {
                if (index < text.length) {
                    // Handle newlines
                    if (text[index] === '\n') {
                        element.innerHTML += '<br>';
                    } else {
                        element.innerText += text[index];
                    }
                    index++;
                    setTimeout(typeChar, speed);
                } else {
                    resolve();
                }
            }
            typeChar();
        });
    }

    // Generate stable ID for email draft (based on content, not time)
    function generateEmailId(emailData) {
        // Use subject + first 100 chars of body for stable identification
        const content = (emailData.subject || '') + (emailData.body || '').substring(0, 100);
        let hash = 0;
        for (let i = 0; i < content.length; i++) {
            hash = ((hash << 5) - hash) + content.charCodeAt(i);
            hash |= 0;
        }
        return 'email_' + Math.abs(hash);
    }

    // Check if user is logged in (not guest mode)
    function isLoggedIn() {
        return document.body.dataset.guestMode !== 'true';
    }

    // Get CSRF token for server requests
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    // Save email draft to localStorage (and server if logged in)
    function saveEmailDraft(emailId, data) {
        // Always save to localStorage first (fast, works offline)
        try {
            const drafts = JSON.parse(localStorage.getItem('emailDrafts') || '{}');
            drafts[emailId] = {
                ...data,
                updatedAt: Date.now()
            };
            localStorage.setItem('emailDrafts', JSON.stringify(drafts));
        } catch (e) {
            console.error('Failed to save email draft to localStorage:', e);
        }

        // Also sync to server if logged in
        if (isLoggedIn()) {
            syncDraftToServer(emailId, data);
        }
    }

    // Sync draft to server (fire and forget, non-blocking)
    function syncDraftToServer(emailId, data) {
        fetch('/email-drafts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            body: JSON.stringify({
                id: emailId,
                to: data.to || '',
                subject: data.subject || '',
                bodyHtml: data.bodyHtml || '',
                bodyText: data.bodyText || ''
            })
        }).then(response => {
            if (response.ok) {
                console.log('Email draft synced to server:', emailId);
            }
        }).catch(err => {
            console.warn('Failed to sync draft to server (will retry later):', err);
        });
    }

    // Load email draft from localStorage (with server fallback for logged-in users)
    function loadEmailDraft(emailId) {
        // First check localStorage (fast)
        try {
            const drafts = JSON.parse(localStorage.getItem('emailDrafts') || '{}');
            const localDraft = drafts[emailId] || null;

            // If logged in and no local draft, the caller can use loadEmailDraftFromServer
            return localDraft;
        } catch (e) {
            console.error('Failed to load email draft from localStorage:', e);
            return null;
        }
    }

    // Load draft from server (async, for logged-in users)
    async function loadEmailDraftFromServer(emailId) {
        if (!isLoggedIn()) return null;

        try {
            const response = await fetch(`/email-drafts/${encodeURIComponent(emailId)}`);
            if (response.ok) {
                const data = await response.json();
                if (data.draft) {
                    console.log('Loaded email draft from server:', emailId);
                    // Also update localStorage
                    try {
                        const drafts = JSON.parse(localStorage.getItem('emailDrafts') || '{}');
                        drafts[emailId] = {
                            ...data.draft,
                            updatedAt: Date.now()
                        };
                        localStorage.setItem('emailDrafts', JSON.stringify(drafts));
                    } catch (e) {
                        // Ignore localStorage errors
                    }
                    return data.draft;
                }
            }
        } catch (err) {
            console.warn('Failed to load draft from server:', err);
        }
        return null;
    }

    // Delete email draft from localStorage (and server if logged in)
    function deleteEmailDraft(emailId) {
        // Delete from localStorage
        try {
            const drafts = JSON.parse(localStorage.getItem('emailDrafts') || '{}');
            if (drafts[emailId]) {
                delete drafts[emailId];
                localStorage.setItem('emailDrafts', JSON.stringify(drafts));
                console.log('Email draft deleted from localStorage:', emailId);
            }
        } catch (e) {
            console.error('Failed to delete email draft from localStorage:', e);
        }

        // Also delete from server if logged in
        if (isLoggedIn()) {
            fetch(`/email-drafts/${encodeURIComponent(emailId)}`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': getCsrfToken()
                }
            }).then(response => {
                if (response.ok) {
                    console.log('Email draft deleted from server:', emailId);
                }
            }).catch(err => {
                console.warn('Failed to delete draft from server:', err);
            });
        }
    }

    // Clean up old drafts (older than 7 days)
    function cleanupOldDrafts() {
        try {
            const drafts = JSON.parse(localStorage.getItem('emailDrafts') || '{}');
            const now = Date.now();
            const maxAge = 7 * 24 * 60 * 60 * 1000; // 7 days
            let cleaned = false;

            for (const id in drafts) {
                if (drafts[id].updatedAt && (now - drafts[id].updatedAt) > maxAge) {
                    delete drafts[id];
                    cleaned = true;
                }
            }

            if (cleaned) {
                localStorage.setItem('emailDrafts', JSON.stringify(drafts));
                console.log('Old email drafts cleaned up');
            }
        } catch (e) {
            console.error('Failed to cleanup drafts:', e);
        }
    }

    // Run cleanup on load
    cleanupOldDrafts();

    // Debounce helper for auto-save
    function debounce(fn, delay) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // Create email composer card
    function createEmailComposerCard(emailData, container, streaming = true, emailId = null) {
        // Generate or use provided email ID
        const draftId = emailId || generateEmailId(emailData);

        // Check for saved draft in localStorage first
        let savedDraft = loadEmailDraft(draftId);
        let hasSavedDraft = savedDraft && savedDraft.updatedAt;

        const card = document.createElement('div');
        card.className = 'email-composer-card';
        card.dataset.emailId = draftId;
        card.innerHTML = `
            <div class="email-composer-header">
                <div class="email-composer-title">
                    <i class="fas fa-envelope"></i>
                    <span>Email Draft</span>
                </div>
                <div class="email-composer-actions">
                    <button class="email-composer-btn copy-btn" title="Copy to clipboard">
                        <i class="far fa-copy"></i>
                    </button>
                    <button class="email-composer-btn primary send-btn" title="Send email">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
            </div>

            <div class="email-composer-fields">
                <div class="email-field-row">
                    <div class="email-field">
                        <label>To</label>
                        <input type="email" class="email-to" placeholder="recipient@example.com" value="">
                    </div>
                </div>
                <div class="email-field-row">
                    <div class="email-field">
                        <label>Subject</label>
                        <input type="text" class="email-subject" placeholder="Add a subject" value="">
                    </div>
                </div>
                <div class="email-field-row body-row">
                    <div class="email-field body-field">
                        <div class="email-body" contenteditable="true" data-placeholder="Compose your email..."></div>
                    </div>
                </div>
            </div>

            <div class="email-composer-hint">
                <i class="fas fa-lightbulb"></i>
                <span>Edit the email above, then click Send to choose your email provider</span>
            </div>
        `;

        const toInput = card.querySelector('.email-to');
        const subjectInput = card.querySelector('.email-subject');
        const bodyEditor = card.querySelector('.email-body');

        // Get current values from inputs
        const getEmailData = () => ({
            to: toInput.value.trim(),
            subject: subjectInput.value.trim(),
            body: bodyEditor.innerText // Plain text for email
        });

        // Get HTML content (for copy with formatting)
        const getEmailHtml = () => ({
            to: toInput.value.trim(),
            subject: subjectInput.value.trim(),
            body: bodyEditor.innerHTML
        });

        // Auto-save function (debounced)
        const autoSave = debounce(() => {
            const data = {
                to: toInput.value.trim(),
                subject: subjectInput.value.trim(),
                bodyHtml: bodyEditor.innerHTML,
                bodyText: bodyEditor.innerText
            };
            saveEmailDraft(draftId, data);
            console.log('Email draft auto-saved:', draftId);
        }, 500);

        // Add auto-save listeners
        toInput.addEventListener('input', autoSave);
        subjectInput.addEventListener('input', autoSave);
        bodyEditor.addEventListener('input', autoSave);

        // Copy button
        card.querySelector('.copy-btn').addEventListener('click', async () => {
            const btn = card.querySelector('.copy-btn');
            const success = await copyEmailToClipboard(getEmailData());
            if (success) {
                btn.innerHTML = '<i class="fas fa-check"></i>';
                setTimeout(() => {
                    btn.innerHTML = '<i class="far fa-copy"></i>';
                }, 2000);
            }
        });

        // Send button
        card.querySelector('.send-btn').addEventListener('click', () => {
            openSendModal(getEmailData());
        });

        // Append to container
        if (container) {
            container.appendChild(card);
        }

        // Helper to fill fields from draft data
        function fillFromDraft(draft) {
            toInput.value = draft.to || emailData.to || '';
            subjectInput.value = draft.subject || emailData.subject || '';
            if (draft.bodyHtml) {
                bodyEditor.innerHTML = draft.bodyHtml;
            } else {
                bodyEditor.innerText = draft.bodyText || emailData.body || '';
            }
        }

        // Use saved draft or original email data
        if (hasSavedDraft) {
            // Load from localStorage draft (instant, no streaming)
            fillFromDraft(savedDraft);
            console.log('Loaded saved email draft from localStorage:', draftId);
        } else if (isLoggedIn()) {
            // Try to load from server for logged-in users
            // Fill with original data first (instant), then update if server has draft
            toInput.value = emailData.to || '';
            subjectInput.value = emailData.subject || '';
            bodyEditor.innerText = emailData.body || '';

            // Async load from server
            loadEmailDraftFromServer(draftId).then(serverDraft => {
                if (serverDraft) {
                    fillFromDraft(serverDraft);
                    console.log('Loaded saved email draft from server:', draftId);
                } else {
                    // No server draft, save initial draft
                    autoSave();
                }
            });
        } else if (streaming) {
            // Stream text into fields sequentially (new email, guest mode)
            requestAnimationFrame(async () => {
                await streamText(toInput, emailData.to || '', 20);
                await streamText(subjectInput, emailData.subject || '', 15);
                await streamTextContentEditable(bodyEditor, emailData.body || '', 5);
                // Save initial draft after streaming completes
                autoSave();
            });
        } else {
            // Instant fill for history (no saved draft, guest mode)
            toInput.value = emailData.to || '';
            subjectInput.value = emailData.subject || '';
            bodyEditor.innerText = emailData.body || '';
            // Save initial draft
            autoSave();
        }

        return card;
    }

    // Escape HTML for safe rendering
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Check if response contains email data and render composer
    function handleEmailResponse(data, messageContainer) {
        if (!data.email || !data.email.body) {
            return false;
        }

        // Create email composer card
        const card = createEmailComposerCard(data.email, messageContainer);

        console.log('Email composer created for:', data.email.subject);
        return true;
    }

    // Expose globally
    // Note: Email detection and parsing is now handled by the backend
    window.EmailComposer = {
        createCard: createEmailComposerCard,
        handleResponse: handleEmailResponse,
        openSendModal: openSendModal,
        closeSendModal: closeSendModal,
        deleteDraft: deleteEmailDraft,
        loadDraft: loadEmailDraft,
        loadDraftFromServer: loadEmailDraftFromServer,
        generateId: generateEmailId,
        isLoggedIn: isLoggedIn
    };

})();
