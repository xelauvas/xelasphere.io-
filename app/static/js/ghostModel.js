/**
 * Ghost Model Selector
 *
 * Replaces the dropdown model selector with an intelligent ghost label
 * that auto-detects intent from user typing and allows override via pills.
 */
(function () {
    'use strict';

    // ============================================
    // MODEL REGISTRY
    // ============================================

    const MODELS = [
        { id: 'auto',              label: 'Chat',  icon: '', note: '', host: '', mono: false },
        { id: 'deepseek-v4-pro',   label: 'Code',  icon: '', note: '', host: '', mono: true },
        { id: 'deepseek-v4-flash', label: 'Think', icon: '', note: '', host: '', mono: false },
    ];

    const TASK_ICON_MAP = {
        chat: '⚡',
        coding: '💻',
        reasoning: '🧠',
        uncensored: '🔓',
        long_context: '📚',
        vision: '👁',
        image_gen: '🎨',
    };

    const TASK_MODEL_MAP = {
        coding: 'deepseek-v4-pro',
        reasoning: 'deepseek-v4-pro',
    };

    // ============================================
    // INTENT DETECTION (ported from smart_router.py)
    // ============================================

    const CODING_PATTERNS = [
        /\b(code|coding|program|function|class|method|api|debug|fix\s+(?:the\s+)?bug)\b/i,
        /\b(python|javascript|typescript|java|rust|go|c\+\+|html|css|sql|react|node)\b/i,
        /\b(implement|refactor|optimize|write\s+(?:a\s+)?(?:script|program|function))\b/i,
        /```\w*/,
        /\b(error|exception|traceback|stack\s*trace)\b/i,
    ];

    const REASONING_PATTERNS = [
        /\b(explain|analyze|compare|evaluate|think\s+through|step\s+by\s+step)\b/i,
        /\b(why|how\s+does|what\s+(?:is|are)\s+the\s+(?:reason|cause|difference))\b/i,
        /\b(math|calculate|equation|formula|proof|logic)\b/i,
        /\b(pros?\s+and\s+cons?|trade-?offs?|advantages?\s+(?:and|vs)\s+disadvantages?)\b/i,
    ];

    const UNCENSORED_PATTERNS = [
        /\b(uncensored|unfiltered|no\s+restrictions?|without\s+limits?)\b/i,
        /\b(roleplay|fiction|creative\s+writing|story)\b/i,
    ];

    function detectTaskType(text) {
        if (!text || !text.trim()) return 'chat';

        // Uncensored: 1 match triggers
        for (const p of UNCENSORED_PATTERNS) {
            if (p.test(text)) return 'uncensored';
        }

        // Coding: 2+ matches
        let codingScore = 0;
        for (const p of CODING_PATTERNS) {
            if (p.test(text)) codingScore++;
        }
        if (codingScore >= 2) return 'coding';

        // Reasoning: 2+ matches
        let reasoningScore = 0;
        for (const p of REASONING_PATTERNS) {
            if (p.test(text)) reasoningScore++;
        }
        if (reasoningScore >= 2) return 'reasoning';

        return 'chat';
    }

    // ============================================
    // GHOST MODEL CLASS
    // ============================================

    class GhostModel {
        constructor() {
            // DOM refs
            this.ghostEl = document.getElementById('ghost-model');
            this.labelEl = document.getElementById('ghost-model-label');
            this.clearEl = document.getElementById('ghost-model-clear');
            this.pillsEl = document.getElementById('ghost-pills');
            this.inputEl = document.getElementById('prompt');

            // Mobile
            this.sheetEl = document.getElementById('ghost-bottom-sheet');
            this.sheetBackdrop = document.getElementById('ghost-bottom-sheet-backdrop');
            this.sheetGrid = document.getElementById('ghost-bottom-sheet-grid');
            this.mobileSelectorEl = document.getElementById('mobile-model-selector');
            this.mobileLabelEl = document.getElementById('mobile-model-label');

            if (!this.ghostEl || !this.inputEl) return;

            // State
            this.currentTask = 'chat';
            this.suggestedModel = 'auto';
            this.overrideModel = null;       // per-message override
            this.lockedModel = null;         // permanent override
            this.pillsOpen = false;
            this.debounceTimer = null;

            // Restore locked model from localStorage
            const saved = localStorage.getItem('ghostLockedModel_v2');
            if (saved && MODELS.some(m => m.id === saved)) {
                this.lockedModel = saved;
            }

            this.init();
        }

        init() {
            // Build always-visible pills
            this.buildPills();
            this.updateGhostDisplay();

            // Pills are always visible — show immediately
            if (this.pillsEl) this.pillsEl.classList.add('active');

            // Mobile: move pills into header center
            const mobileCenter = document.getElementById('mobile-header-center');
            if (mobileCenter && window.innerWidth <= 768 && this.pillsEl) {
                mobileCenter.appendChild(this.pillsEl);
            }

            // Input listener (debounced intent detection)
            this.inputEl.addEventListener('input', () => {
                if (this.lockedModel) return;
                clearTimeout(this.debounceTimer);
                this.debounceTimer = setTimeout(() => this.detectAndUpdate(), 400);
            });

            // Clear override after message is sent
            document.addEventListener('messageSent', () => {
                if (this.overrideModel && !this.lockedModel) {
                    this.overrideModel = null;
                    this.detectAndUpdate();
                }
            });
        }

        // ---- Intent Detection ----

        detectAndUpdate() {
            const text = this.inputEl.value;
            const task = detectTaskType(text);

            if (task !== this.currentTask) {
                this.currentTask = task;
                this.suggestedModel = TASK_MODEL_MAP[task] || 'auto';
                this.crossfadeGhost();
            }
        }

        // ---- Display ----

        getDisplayModel() {
            if (this.lockedModel) return this.lockedModel;
            if (this.overrideModel) return this.overrideModel;
            return this.suggestedModel;
        }

        getModelInfo(modelId) {
            return MODELS.find(m => m.id === modelId) || MODELS[0];
        }

        updateGhostDisplay() {
            const modelId = this.getDisplayModel();
            const info = this.getModelInfo(modelId);

            this.labelEl.textContent = info.label;

            // Sync mobile header label
            if (this.mobileLabelEl) {
                this.mobileLabelEl.textContent = info.label;
            }

            // Show/hide clear button
            if (this.clearEl) {
                this.clearEl.style.display = this.lockedModel ? 'inline' : 'none';
            }

            // Update active pill
            this.pillsEl?.querySelectorAll('.ghost-pill').forEach(pill => {
                pill.classList.remove('active');
                if (pill.dataset.model === modelId) {
                    pill.classList.add('active');
                }
                pill.classList.toggle('locked', pill.dataset.model === this.lockedModel);
            });
        }

        crossfadeGhost() {
            this.labelEl.classList.add('fade-out');
            this.labelEl.classList.remove('fade-in');

            setTimeout(() => {
                this.updateGhostDisplay();
                this.labelEl.classList.remove('fade-out');
                this.labelEl.classList.add('fade-in');
            }, 150);
        }

        // ---- Pills (Desktop) ----

        buildPills() {
            if (!this.pillsEl) return;
            this.pillsEl.innerHTML = '';

            const activeModelId = this.getDisplayModel();

            MODELS.forEach(model => {
                const pill = document.createElement('button');
                pill.className = 'ghost-pill';
                if (model.id === activeModelId) pill.classList.add('active');
                pill.dataset.model = model.id;
                pill.setAttribute('role', 'option');

                const labelSpan = document.createElement('span');
                labelSpan.className = 'ghost-pill__label';
                labelSpan.textContent = model.label;
                if (model.mono) labelSpan.style.fontFamily = "'SF Mono', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace";
                pill.appendChild(labelSpan);

                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (model.id === 'auto') {
                        this.clearLock();
                    } else {
                        this.lockModel(model.id);
                    }
                    // Update active pill
                    this.pillsEl.querySelectorAll('.ghost-pill').forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');
                });

                pill.addEventListener('dblclick', (e) => {
                    e.stopPropagation();
                    this.lockModel(model.id);
                });

                this.pillsEl.appendChild(pill);
            });
        }

        togglePills() {
            // Mobile → bottom sheet
            if (window.innerWidth <= 768) {
                this.toggleSheet();
                return;
            }

            this.pillsOpen = !this.pillsOpen;
            this.pillsEl.classList.toggle('active', this.pillsOpen);
            this.ghostEl.setAttribute('aria-expanded', this.pillsOpen);

            if (this.pillsOpen) {
                this.updateGhostDisplay();
                // Focus first pill for keyboard nav
                const firstPill = this.pillsEl.querySelector('.ghost-pill');
                if (firstPill) firstPill.focus();

                // Close on outside click
                const closeOnOutside = (e) => {
                    if (!this.ghostEl.contains(e.target) && !this.pillsEl.contains(e.target)) {
                        this.closePills();
                        document.removeEventListener('click', closeOnOutside);
                    }
                };
                setTimeout(() => document.addEventListener('click', closeOnOutside), 0);
            }
        }

        closePills() {
            this.pillsOpen = false;
            this.pillsEl?.classList.remove('active');
            this.ghostEl?.setAttribute('aria-expanded', 'false');
        }

        // ---- Bottom Sheet (Mobile) ----

        buildSheet() {
            if (!this.sheetGrid) return;
            this.sheetGrid.innerHTML = '';

            const activeModelId = this.getDisplayModel();

            MODELS.forEach(model => {
                // Hide the currently selected model from the list
                if (model.id === activeModelId) return;

                const option = document.createElement('div');
                option.className = 'ghost-bottom-sheet__option';
                option.dataset.model = model.id;
                option.innerHTML = `<span>${model.icon}</span><span>${model.label}</span><span class="ghost-bottom-sheet__host">${model.host}</span>`;

                option.addEventListener('click', () => {
                    this.lockModel(model.id);
                    this.closeSheet();
                });

                this.sheetGrid.appendChild(option);
            });
        }

        toggleSheet() {
            const isOpen = this.sheetEl?.classList.contains('active');
            if (isOpen) {
                this.closeSheet();
            } else {
                this.updateSheetActive();
                this.sheetEl?.classList.add('active');
                this.sheetBackdrop?.classList.add('active');
                this.mobileSelectorEl?.setAttribute('aria-expanded', 'true');
            }
        }

        closeSheet() {
            this.sheetEl?.classList.remove('active');
            this.sheetBackdrop?.classList.remove('active');
            this.mobileSelectorEl?.setAttribute('aria-expanded', 'false');
        }

        updateSheetActive() {
            const modelId = this.getDisplayModel();
            this.sheetGrid?.querySelectorAll('.ghost-bottom-sheet__option').forEach(opt => {
                opt.classList.toggle('active', opt.dataset.model === modelId);
            });
        }

        // ---- Selection ----

        selectModel(modelId) {
            if (modelId === 'auto') {
                this.overrideModel = null;
                this.lockedModel = null;
                localStorage.removeItem('ghostLockedModel_v2');
            } else {
                this.overrideModel = modelId;
            }
            this.closePills();
            this.buildPills();
            this.crossfadeGhost();
        }

        lockModel(modelId) {
            if (modelId === 'auto') {
                this.lockedModel = null;
                this.overrideModel = null;
                localStorage.removeItem('ghostLockedModel_v2');
            } else {
                this.lockedModel = modelId;
                this.overrideModel = null;
                localStorage.setItem('ghostLockedModel_v2', modelId);
            }
            this.closePills();
            this.closeSheet();
            this.buildPills();
            this.crossfadeGhost();
        }

        clearLock() {
            this.lockedModel = null;
            this.overrideModel = null;
            localStorage.removeItem('ghostLockedModel_v2');
            this.detectAndUpdate();
            this.buildPills();
            this.crossfadeGhost();
        }

        // ---- Public API (for codeWindow.js) ----

        getSelectedModel() {
            if (this.lockedModel) return this.lockedModel;
            if (this.overrideModel) return this.overrideModel;
            return 'auto';
        }

        resetAfterSend() {
            // Keep the selected model — only reset intent detection
            this.currentTask = 'chat';
            if (!this.overrideModel && !this.lockedModel) {
                this.suggestedModel = 'auto';
                this.updateGhostDisplay();
            }
        }
    }

    // ============================================
    // RESPONSE ATTRIBUTION HELPER
    // ============================================

    function addModelAttribution(messageDiv, modelMeta) {
        if (!messageDiv || !modelMeta) return;

        const label = modelMeta.model_label || modelMeta.model_id;
        if (!label) return;

        // Idempotency guard — multiple code paths may call this (live stream
        // finalize + history-replay finalize). Skip if an attribution span
        // already exists inside this message.
        if (messageDiv.querySelector('.message-model-attribution')) return;

        let text = `via ${label}`;
        if (modelMeta.is_fallback) {
            text += ' (fallback)';
        }

        const attr = document.createElement('span');
        attr.className = 'message-model-attribution';
        attr.textContent = text;

        // Try to append inline at the end of the last text element
        const textMsg = messageDiv.querySelector('.text-message');
        if (textMsg) {
            // Find the last block-level child (p, li, h1-h6, div, etc.).
            // Skip anything inside a code chrome — its <div class="code-header">
            // would otherwise be picked as the last <div> and the attribution
            // would land in the code-window's title bar.
            const blocks = Array.from(
                textMsg.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, div, blockquote')
            ).filter(el => !el.closest('.code-window, .stream-block.block-code'));
            const lastBlock = blocks.length ? blocks[blocks.length - 1] : null;
            if (lastBlock) {
                lastBlock.appendChild(document.createTextNode(' '));
                lastBlock.appendChild(attr);
                return;
            }
        }

        // Fallback: append to message div
        messageDiv.appendChild(attr);
    }

    // ============================================
    // INIT
    // ============================================

    let ghostInstance = null;

    document.addEventListener('DOMContentLoaded', function () {
        try {
            ghostInstance = new GhostModel();
            window.ghostModel = ghostInstance;
        } catch (error) {
            console.error('Failed to initialize Ghost Model:', error);
        }
    });

    // Expose attribution helper globally
    window.addModelAttribution = addModelAttribution;
})();
