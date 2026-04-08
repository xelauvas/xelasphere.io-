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
        { id: 'auto', label: 'Auto', icon: '⚡', note: 'smart routing', host: '' },
        // OpenAI
        { id: 'gpt-4.1', label: 'GPT-4.1', icon: '✨', note: 'best coding', host: 'OpenAI' },
        { id: 'gpt-4.1-mini', label: 'GPT-4.1 Mini', icon: '✨', note: 'fast & smart', host: 'OpenAI' },
        // DeepSeek
        { id: 'deepseek-chat', label: 'DeepSeek V3', icon: '💻', note: 'top-tier coding', host: 'DeepSeek' },
        { id: 'deepseek-reasoner', label: 'DeepSeek R1', icon: '💻', note: 'deep reasoning', host: 'DeepSeek' },
        // Groq Cloud
        { id: 'llama-3.3-70b-versatile', label: 'Llama 70B', icon: '🦙', note: 'fast all-rounder', host: 'Groq' },
        { id: 'qwen/qwen3-32b', label: 'Qwen 32B', icon: '🧠', note: 'coding & math', host: 'Groq' },
        { id: 'qwen-3-235b-a22b-instruct-2507', label: 'Qwen 235B', icon: '🧠', note: 'complex reasoning', host: 'Groq' },
        { id: 'openai/gpt-oss-120b', label: 'GPT-OSS 120B', icon: '🤖', note: 'large model', host: 'Groq' },
        // Moonshot
        { id: 'kimi-k2.5', label: 'Kimi K2.5', icon: '📚', note: 'reasoning', host: 'Moonshot' },
        { id: 'kimi-latest', label: 'Kimi Latest', icon: '📚', note: 'long context', host: 'Moonshot' },
        // Local
        { id: 'dolphin3', label: 'Dolphin 3', icon: '🔓', note: 'uncensored', host: 'Local' },
        { id: 'gemma4:e2b', label: 'Gemma 4', icon: '🏠', note: 'on-device', host: 'Local' },
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
        coding: 'deepseek-chat',
        reasoning: 'qwen/qwen3-32b',
        uncensored: 'dolphin3',
        long_context: 'kimi-latest',
        vision: 'meta-llama/llama-4-maverick-17b-128e-instruct',
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

            if (!this.ghostEl || !this.inputEl) return;

            // State
            this.currentTask = 'chat';
            this.suggestedModel = 'auto';
            this.overrideModel = null;       // per-message override
            this.lockedModel = null;         // permanent override
            this.pillsOpen = false;
            this.debounceTimer = null;

            // Restore locked model from localStorage
            const saved = localStorage.getItem('ghostLockedModel');
            if (saved && MODELS.some(m => m.id === saved)) {
                this.lockedModel = saved;
            }

            this.init();
        }

        init() {
            // Render initial state
            this.updateGhostDisplay();

            // Input listener (debounced intent detection)
            this.inputEl.addEventListener('input', () => {
                if (this.lockedModel) return; // Skip detection when locked
                clearTimeout(this.debounceTimer);
                this.debounceTimer = setTimeout(() => this.detectAndUpdate(), 400);
            });

            // Ghost click → toggle pills
            this.ghostEl.addEventListener('click', (e) => {
                if (e.target === this.clearEl || e.target.closest('.ghost-model__clear')) {
                    this.clearLock();
                    return;
                }
                this.togglePills();
            });

            // Close pills on outside click
            document.addEventListener('click', (e) => {
                if (this.pillsOpen &&
                    !this.ghostEl.contains(e.target) &&
                    !this.pillsEl.contains(e.target)) {
                    this.closePills();
                }
                if (this.sheetEl?.classList.contains('active') &&
                    !this.sheetEl.contains(e.target) &&
                    !this.ghostEl.contains(e.target)) {
                    this.closeSheet();
                }
            });

            // Escape key closes pills/sheet
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.closePills();
                    this.closeSheet();
                }
            });

            // Build pill elements
            this.buildPills();
            this.buildSheet();

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
            let lastHost = '';

            MODELS.forEach(model => {
                if (model.id === activeModelId) return;

                // Provider group divider
                if (model.host && model.host !== lastHost) {
                    const divider = document.createElement('div');
                    divider.className = 'ghost-pill__group';
                    divider.textContent = model.host;
                    this.pillsEl.appendChild(divider);
                    lastHost = model.host;
                }

                const pill = document.createElement('button');
                pill.className = 'ghost-pill';
                pill.dataset.model = model.id;
                pill.title = model.note;
                pill.setAttribute('role', 'option');
                pill.setAttribute('tabindex', '-1');

                pill.innerHTML = `<span class="ghost-pill__icon">${model.icon}</span><span class="ghost-pill__label">${model.label}</span><span class="ghost-pill__note">${model.note}</span>`;

                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.selectModel(model.id);
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
            }
        }

        closeSheet() {
            this.sheetEl?.classList.remove('active');
            this.sheetBackdrop?.classList.remove('active');
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
                localStorage.removeItem('ghostLockedModel');
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
                localStorage.removeItem('ghostLockedModel');
            } else {
                this.lockedModel = modelId;
                this.overrideModel = null;
                localStorage.setItem('ghostLockedModel', modelId);
            }
            this.closePills();
            this.closeSheet();
            this.buildPills();
            this.crossfadeGhost();
        }

        clearLock() {
            this.lockedModel = null;
            this.overrideModel = null;
            localStorage.removeItem('ghostLockedModel');
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
            // Find the last block-level child (p, li, h1-h6, div, etc.)
            const blocks = textMsg.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, div, blockquote');
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
