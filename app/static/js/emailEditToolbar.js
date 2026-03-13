/**
 * Email Edit Toolbar
 * Contextual formatting toolbar for email composer with AI-assisted edits
 * Clean, minimal design with inline diff preview
 */

(function() {
    'use strict';

    let toolbar = null;
    let floatingChangeInput = null;
    let currentTarget = null;
    let currentComposerCard = null;
    let currentSelection = { start: 0, end: 0, text: '' };
    let savedRange = null; // Save selection range for AI edit
    let pendingEdit = null;
    let originalTextareaValue = '';
    let isProcessing = false; // Prevent double-processing

    // Create the toolbar
    function createToolbar() {
        if (toolbar) return toolbar;

        const el = document.createElement('div');
        el.className = 'email-edit-toolbar';
        el.innerHTML = `
            <button class="eet-btn" data-action="bold" title="Bold (Ctrl+B)">
                <i class="fas fa-bold"></i>
            </button>
            <button class="eet-btn" data-action="italic" title="Italic (Ctrl+I)">
                <i class="fas fa-italic"></i>
            </button>
            <div class="eet-divider"></div>
            <button class="eet-btn" data-action="bullet" title="Bullet list">
                <i class="fas fa-list-ul"></i>
            </button>
            <button class="eet-btn" data-action="number" title="Numbered list">
                <i class="fas fa-list-ol"></i>
            </button>
            <div class="eet-divider"></div>
            <div class="eet-dropdown">
                <button class="eet-btn eet-dropdown-toggle" title="Text formatting">
                    <span>Text</span>
                    <i class="fas fa-chevron-down"></i>
                </button>
                <div class="eet-dropdown-menu">
                    <button class="eet-dropdown-item" data-action="h1">
                        <span class="eet-dropdown-label">Heading 1</span>
                        <span class="eet-dropdown-preview" style="font-size:18px;font-weight:700;">H1</span>
                    </button>
                    <button class="eet-dropdown-item" data-action="h2">
                        <span class="eet-dropdown-label">Heading 2</span>
                        <span class="eet-dropdown-preview" style="font-size:15px;font-weight:600;">H2</span>
                    </button>
                    <button class="eet-dropdown-item" data-action="h3">
                        <span class="eet-dropdown-label">Heading 3</span>
                        <span class="eet-dropdown-preview" style="font-size:13px;font-weight:600;">H3</span>
                    </button>
                    <div class="eet-dropdown-divider"></div>
                    <button class="eet-dropdown-item" data-action="paragraph">
                        <span class="eet-dropdown-label">Normal text</span>
                    </button>
                </div>
            </div>
            <div class="eet-divider"></div>
            <button class="eet-btn eet-ai-btn" data-action="ask-changes" title="AI Edit">
                <i class="fas fa-wand-magic-sparkles"></i>
                <span>AI Edit</span>
            </button>
        `;

        // Regular button handlers
        el.querySelectorAll('.eet-btn:not(.eet-dropdown-toggle)').forEach(btn => {
            btn.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
            });
            btn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();

                if (isProcessing) return;

                const action = e.currentTarget.dataset.action;

                if (action !== 'ask-changes') {
                    isProcessing = true;
                    handleAction(e);
                    hideToolbar();
                    setTimeout(() => { isProcessing = false; }, 100);
                } else {
                    handleAction(e);
                }
            });
        });

        // Dropdown toggle handler
        const dropdownToggle = el.querySelector('.eet-dropdown-toggle');
        const dropdownMenu = el.querySelector('.eet-dropdown-menu');

        dropdownToggle.addEventListener('mousedown', e => {
            e.preventDefault();
            e.stopPropagation();
        });

        dropdownToggle.addEventListener('click', e => {
            e.preventDefault();
            e.stopPropagation();
            dropdownMenu.classList.toggle('visible');
        });

        // Dropdown item handlers
        el.querySelectorAll('.eet-dropdown-item').forEach(item => {
            item.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
            });
            item.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();

                if (isProcessing) return;
                isProcessing = true;

                handleAction(e);
                dropdownMenu.classList.remove('visible');
                hideToolbar();

                setTimeout(() => { isProcessing = false; }, 100);
            });
        });

        document.body.appendChild(el);
        toolbar = el;
        return el;
    }

    // Create floating AI input
    function createFloatingInput() {
        if (floatingChangeInput) return floatingChangeInput;

        const el = document.createElement('div');
        el.className = 'eet-ai-input';
        el.innerHTML = `
            <div class="eet-ai-input-inner">
                <i class="fas fa-wand-magic-sparkles"></i>
                <input type="text" placeholder="How should I change this?" autocomplete="off">
                <button class="eet-ai-submit">
                    <i class="fas fa-arrow-right"></i>
                </button>
            </div>
            <div class="eet-ai-suggestions">
                <button data-suggestion="more formal">More formal</button>
                <button data-suggestion="more casual">Casual</button>
                <button data-suggestion="shorter">Shorter</button>
                <button data-suggestion="fix grammar">Fix grammar</button>
                <button data-suggestion="rephrase this differently">Rephrase</button>
                <button data-suggestion="make it more professional and business-like">Professional</button>
            </div>
        `;

        const input = el.querySelector('input');
        const submitBtn = el.querySelector('.eet-ai-submit');

        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitAIRequest(input.value.trim());
            }
            if (e.key === 'Escape') {
                hideFloatingInput();
            }
        });

        submitBtn.addEventListener('click', () => {
            submitAIRequest(input.value.trim());
        });

        el.querySelectorAll('[data-suggestion]').forEach(btn => {
            btn.addEventListener('click', () => {
                submitAIRequest(btn.dataset.suggestion);
            });
        });

        document.body.appendChild(el);
        floatingChangeInput = el;
        return el;
    }

    // Handle toolbar actions
    function handleAction(e) {
        const action = e.currentTarget.dataset.action;

        switch (action) {
            case 'bold':
                applyBold();
                break;
            case 'italic':
                applyItalic();
                break;
            case 'h1':
                applyHeading('h1');
                break;
            case 'h2':
                applyHeading('h2');
                break;
            case 'h3':
                applyHeading('h3');
                break;
            case 'bullet':
                applyBulletList();
                break;
            case 'number':
                applyNumberedList();
                break;
            case 'paragraph':
                applyParagraph();
                break;
            case 'ask-changes':
                showFloatingInput();
                break;
        }
    }

    // Apply bold formatting using execCommand
    function applyBold() {
        document.execCommand('bold', false, null);
    }

    // Apply italic formatting using execCommand
    function applyItalic() {
        document.execCommand('italic', false, null);
    }

    // Apply heading (h1, h2, h3)
    function applyHeading(level) {
        document.execCommand('formatBlock', false, level);
    }

    // Apply normal paragraph (remove heading)
    function applyParagraph() {
        document.execCommand('formatBlock', false, 'div');
    }

    // Apply bullet list
    function applyBulletList() {
        document.execCommand('insertUnorderedList', false, null);
    }

    // Apply numbered list
    function applyNumberedList() {
        document.execCommand('insertOrderedList', false, null);
    }

    // Hide toolbar
    function hideToolbar() {
        if (toolbar) {
            toolbar.classList.remove('visible');
            // Also hide dropdown menu
            const dropdownMenu = toolbar.querySelector('.eet-dropdown-menu');
            if (dropdownMenu) dropdownMenu.classList.remove('visible');
        }
    }

    // Show floating AI input
    function showFloatingInput() {
        if (!floatingChangeInput) createFloatingInput();

        // Save the current selection range BEFORE hiding toolbar or changing focus
        const selection = window.getSelection();
        if (selection && selection.rangeCount > 0) {
            savedRange = selection.getRangeAt(0).cloneRange();
        }

        hideToolbar();

        // Position above current selection (fixed positioning)
        if (savedRange) {
            const rect = savedRange.getBoundingClientRect();

            const inputWidth = 320;
            const inputHeight = floatingChangeInput.offsetHeight || 100;

            let left = rect.left + (rect.width / 2) - (inputWidth / 2);
            let top = rect.top - inputHeight - 8;

            if (left < 10) left = 10;
            if (left + inputWidth > window.innerWidth - 10) {
                left = window.innerWidth - inputWidth - 10;
            }
            if (top < 10) {
                top = rect.bottom + 8;
            }

            floatingChangeInput.style.left = `${left}px`;
            floatingChangeInput.style.top = `${top}px`;
        }

        floatingChangeInput.classList.add('visible');

        const input = floatingChangeInput.querySelector('input');
        input.value = '';
        setTimeout(() => input.focus(), 50);
    }

    // Hide floating input
    function hideFloatingInput() {
        if (floatingChangeInput) {
            floatingChangeInput.classList.remove('visible');
        }
        // Don't clear savedRange here - it's needed for the AI edit to work
    }

    // Submit AI request
    async function submitAIRequest(instruction) {
        if (!instruction || !currentTarget || !currentSelection.text) {
            hideFloatingInput();
            return;
        }

        const selectedText = currentSelection.text;
        const composerCard = currentTarget.closest('.email-composer-card');
        if (!composerCard) return;

        // Store original content for restore
        originalTextareaValue = currentTarget.innerHTML;

        // Show loading
        const input = floatingChangeInput.querySelector('input');
        const submitBtn = floatingChangeInput.querySelector('.eet-ai-submit');
        input.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>';

        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

            const response = await fetch('/api/edit-text', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ text: selectedText, instruction })
            });

            if (response.ok) {
                const data = await response.json();
                if (data.edited_text && data.edited_text !== selectedText) {
                    hideFloatingInput();
                    // Replace selected text with new text directly
                    replaceSelectionWithDiff(selectedText, data.edited_text);
                }
            } else {
                console.error('AI edit failed');
            }
        } catch (err) {
            console.error('AI edit error:', err);
        } finally {
            input.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-arrow-right"></i>';
            hideFloatingInput();
        }
    }

    // Replace selection with diff showing old and new
    function replaceSelectionWithDiff(oldText, newText) {
        // Use saved range (selection is lost when input is focused)
        if (!savedRange) {
            console.error('No saved range for AI edit');
            return;
        }

        currentComposerCard = currentTarget.closest('.email-composer-card');
        pendingEdit = { oldText, newText, originalHtml: originalTextareaValue };

        // Create diff element
        const diffSpan = document.createElement('span');
        diffSpan.className = 'eet-inline-diff';
        diffSpan.innerHTML = `<span class="eet-diff-new">${escapeHtml(newText)}</span><span class="eet-diff-old">${escapeHtml(oldText)}</span><span class="eet-diff-actions"><button class="eet-diff-accept" title="Accept"><i class="fas fa-check"></i></button><button class="eet-diff-reject" title="Reject"><i class="fas fa-times"></i></button></span>`;

        // Replace saved range with diff
        savedRange.deleteContents();
        savedRange.insertNode(diffSpan);

        // Add event listeners
        diffSpan.querySelector('.eet-diff-accept').addEventListener('click', acceptDiff);
        diffSpan.querySelector('.eet-diff-reject').addEventListener('click', rejectDiff);

        // Clear saved range
        savedRange = null;

        // Clear any selection
        const selection = window.getSelection();
        if (selection) selection.removeAllRanges();
    }

    // Remove inline diff
    function removeInlineDiff() {
        const existing = document.querySelector('.eet-inline-diff');
        if (existing) existing.remove();
        pendingEdit = null;
    }

    // Accept diff
    function acceptDiff() {
        if (pendingEdit && currentTarget) {
            const diffSpan = currentTarget.querySelector('.eet-inline-diff');
            if (diffSpan) {
                // Replace diff span with just the new text
                const newTextNode = document.createTextNode(pendingEdit.newText);
                diffSpan.parentNode.replaceChild(newTextNode, diffSpan);
            }
            pendingEdit = null;
        }
    }

    // Reject diff - restore original content
    function rejectDiff() {
        if (pendingEdit && currentTarget) {
            const diffSpan = currentTarget.querySelector('.eet-inline-diff');
            if (diffSpan) {
                // Replace diff span with the original old text
                const oldTextNode = document.createTextNode(pendingEdit.oldText);
                diffSpan.parentNode.replaceChild(oldTextNode, diffSpan);
            }
            pendingEdit = null;
        } else {
            removeInlineDiff();
        }
    }

    // Escape HTML
    function escapeHtml(text) {
        return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/\n/g, '<br>');
    }

    // Check if in email composer
    function isInEmailComposer(el) {
        return el?.closest('.email-composer-card') !== null;
    }

    // Handle selection changes
    function handleSelectionChange() {
        // Skip if diff is showing or AI input is open
        if (document.querySelector('.eet-inline-diff')) return;
        if (floatingChangeInput?.classList.contains('visible')) return;

        // Skip if user is interacting with toolbar
        if (toolbar?.contains(document.activeElement)) return;

        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) {
            hideToolbar();
            return;
        }

        const selectedText = selection.toString().trim();

        // Check if selection is in email composer body
        const anchorNode = selection.anchorNode;
        if (!anchorNode) {
            hideToolbar();
            return;
        }

        // Get the element (anchorNode could be a text node)
        const element = anchorNode.nodeType === Node.TEXT_NODE
            ? anchorNode.parentElement
            : anchorNode;

        // Find the email body contenteditable (check if element itself is email-body, or find parent)
        let emailBody = null;
        if (element?.classList?.contains('email-body')) {
            emailBody = element;
        } else {
            emailBody = element?.closest('.email-body') ||
                        element?.closest('[contenteditable="true"]');
        }

        if (!emailBody || !emailBody.isContentEditable) {
            // Don't hide if toolbar is visible and we have a valid selection stored
            if (toolbar?.classList.contains('visible') && currentSelection.text) {
                return;
            }
            hideToolbar();
            return;
        }

        if (selectedText.length >= 2) {
            currentTarget = emailBody;
            currentSelection = { text: selectedText };
            showToolbarAtSelection(selection);
            return;
        }

        hideToolbar();
    }

    // Show toolbar at current selection
    function showToolbarAtSelection(selection) {
        if (!toolbar) createToolbar();
        if (!selection.rangeCount) return;

        const range = selection.getRangeAt(0);
        const rect = range.getBoundingClientRect();

        if (rect.width === 0 && rect.height === 0) {
            hideToolbar();
            return;
        }

        const toolbarWidth = 280;
        const toolbarHeight = toolbar.offsetHeight || 44;

        // Position above selection
        let left = rect.left + (rect.width / 2) - (toolbarWidth / 2);
        let top = rect.top - toolbarHeight - 8;

        // Keep in viewport
        if (left < 10) left = 10;
        if (left + toolbarWidth > window.innerWidth - 10) {
            left = window.innerWidth - toolbarWidth - 10;
        }
        // If no room above, show below
        if (top < 10) {
            top = rect.bottom + 8;
        }

        toolbar.style.left = `${left}px`;
        toolbar.style.top = `${top}px`;
        toolbar.classList.add('visible');
    }

    // Keyboard shortcuts
    function handleKeyboard(e) {
        // Diff shortcuts
        if (document.querySelector('.eet-inline-diff')) {
            if (e.key === 'Escape') {
                e.preventDefault();
                rejectDiff();
            } else if (e.key === 'Enter' && e.ctrlKey) {
                e.preventDefault();
                acceptDiff();
            }
            return;
        }

        // Formatting shortcuts in email composer (contenteditable)
        const activeEl = document.activeElement;
        const emailBody = activeEl?.closest?.('.email-body[contenteditable]') ||
                          (activeEl?.classList?.contains('email-body') && activeEl.contentEditable === 'true' ? activeEl : null);

        if (emailBody && !isProcessing) {
            const selection = window.getSelection();
            const selectedText = selection?.toString();

            if (selectedText && e.ctrlKey && (e.key === 'b' || e.key === 'i')) {
                e.preventDefault();
                isProcessing = true;

                if (e.key === 'b') {
                    applyBold();
                } else if (e.key === 'i') {
                    applyItalic();
                }

                hideToolbar();
                setTimeout(() => { isProcessing = false; }, 100);
            }
        }
    }

    // Initialize
    function init() {
        createToolbar();
        createFloatingInput();

        let selectionTimeout;
        document.addEventListener('selectionchange', () => {
            clearTimeout(selectionTimeout);
            selectionTimeout = setTimeout(handleSelectionChange, 100);
        });

        document.addEventListener('mousedown', e => {
            if (toolbar && !toolbar.contains(e.target) && !e.target.closest('.eet-ai-input')) {
                hideToolbar();
            }
            if (floatingChangeInput && !floatingChangeInput.contains(e.target) && !e.target.closest('.email-edit-toolbar')) {
                hideFloatingInput();
            }
        });

        document.addEventListener('keydown', handleKeyboard);
        document.addEventListener('scroll', hideToolbar, true);

        console.log('Email Edit Toolbar v2 initialized');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.EmailEditToolbar = { acceptDiff, rejectDiff };
})();
