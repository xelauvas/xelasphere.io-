# Ghost Suggestion Model Selector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dropdown model selector with an intelligent ghost label that auto-detects model intent from user typing and allows one-click override via inline pills.

**Architecture:** A new `ghostModel.js` module handles client-side intent detection (regex), ghost label updates (debounced), and pill-based override UI. A new `ghost-model.css` handles all styling. The old dropdown (`modelSelect.js`, `model-select.css`) is removed. The backend is untouched — the ghost sends the same `model` form field the dropdown did.

**Tech Stack:** Vanilla JS, CSS3 transitions/animations, existing Flask templates (Jinja2)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/static/js/ghostModel.js` | Create | Intent detection, ghost label, pill picker, model override state |
| `app/static/css/ghost-model.css` | Create | Ghost label, pills, bottom sheet, transitions |
| `app/templates/index.html` | Modify | Remove old dropdown HTML, add ghost label HTML, update script/css refs |
| `app/static/js/modelSelect.js` | Delete | Replaced by ghostModel.js |
| `app/static/css/model-select.css` | Delete | Replaced by ghost-model.css |
| `app/static/js/codeWindow.js` | Modify | Read model from ghost instead of old dropdown, add response attribution |
| `app/static/css/mobile.css` | Modify | Remove old mobile model styles, add bottom sheet styles |

---

### Task 1: Create Ghost Label CSS

**Files:**
- Create: `app/static/css/ghost-model.css`

- [ ] **Step 1: Create the ghost-model.css file**

```css
/* ============================================
   GHOST MODEL SELECTOR
   ============================================ */

/* Ghost label below chat input */
.ghost-model {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.3rem;
    padding: 0.25rem 0.75rem 0;
    min-height: 1.25rem;
    cursor: pointer;
    user-select: none;
}

.ghost-model__label {
    font-size: 0.7rem;
    color: rgba(255, 255, 255, 0.35);
    transition: opacity 0.3s ease, color 0.3s ease;
    white-space: nowrap;
}

.ghost-model:hover .ghost-model__label {
    color: rgba(255, 255, 255, 0.6);
}

.ghost-model__icon {
    font-size: 0.7rem;
    transition: opacity 0.3s ease;
}

.ghost-model__hint {
    font-size: 0.6rem;
    color: rgba(255, 255, 255, 0.2);
    animation: ghost-pulse 2s ease-in-out 1;
}

@keyframes ghost-pulse {
    0%, 100% { opacity: 0.2; }
    50% { opacity: 0.6; }
}

/* Crossfade transition */
.ghost-model__label.fade-out {
    opacity: 0;
    transition: opacity 0.15s ease;
}

.ghost-model__label.fade-in {
    opacity: 1;
    transition: opacity 0.15s ease 0.15s;
}

/* ============================================
   PILL PICKER (Desktop override)
   ============================================ */

.ghost-pills {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0.75rem;
    overflow-x: auto;
    scrollbar-width: none;
    -ms-overflow-style: none;
    max-height: 0;
    opacity: 0;
    transition: max-height 0.2s ease, opacity 0.2s ease, padding 0.2s ease;
    padding-top: 0;
    padding-bottom: 0;
}

.ghost-pills::-webkit-scrollbar {
    display: none;
}

.ghost-pills.active {
    max-height: 3rem;
    opacity: 1;
    padding-top: 0.35rem;
    padding-bottom: 0.35rem;
}

.ghost-pill {
    flex-shrink: 0;
    padding: 0.25rem 0.6rem;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.6);
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.08);
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
}

.ghost-pill:hover {
    color: rgba(255, 255, 255, 0.9);
    background: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.15);
}

.ghost-pill.active {
    color: #fff;
    background: rgba(255, 255, 255, 0.15);
    border-color: rgba(255, 255, 255, 0.25);
}

.ghost-pill.locked {
    color: #fff;
    background: rgba(99, 102, 241, 0.25);
    border-color: rgba(99, 102, 241, 0.4);
}

/* Clear override button */
.ghost-model__clear {
    font-size: 0.65rem;
    color: rgba(255, 255, 255, 0.3);
    cursor: pointer;
    margin-left: 0.2rem;
    transition: color 0.15s ease;
}

.ghost-model__clear:hover {
    color: rgba(255, 255, 255, 0.7);
}

/* ============================================
   RESPONSE ATTRIBUTION
   ============================================ */

.message-model-attribution {
    text-align: right;
    font-size: 0.65rem;
    color: rgba(255, 255, 255, 0.3);
    padding: 0.25rem 0 0 0;
    user-select: none;
}

/* ============================================
   BOTTOM SHEET (Mobile override)
   ============================================ */

.ghost-bottom-sheet {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #2a2a2a;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px 16px 0 0;
    padding: 1rem;
    z-index: 2000;
    transform: translateY(100%);
    transition: transform 0.25s ease;
}

.ghost-bottom-sheet.active {
    transform: translateY(0);
}

.ghost-bottom-sheet__backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 1999;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
}

.ghost-bottom-sheet__backdrop.active {
    opacity: 1;
    pointer-events: auto;
}

.ghost-bottom-sheet__grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem;
}

.ghost-bottom-sheet__option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.75rem;
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.06);
    color: rgba(255, 255, 255, 0.8);
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.15s ease;
}

.ghost-bottom-sheet__option:hover,
.ghost-bottom-sheet__option.active {
    background: rgba(255, 255, 255, 0.12);
}

.ghost-bottom-sheet__handle {
    width: 2rem;
    height: 0.2rem;
    background: rgba(255, 255, 255, 0.2);
    border-radius: 999px;
    margin: 0 auto 0.75rem;
}

/* Guest disabled state */
.ghost-model.guest-disabled {
    pointer-events: none;
    opacity: 0.3;
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/ghost-model.css
git commit -m "feat: add Ghost Model selector CSS (label, pills, bottom sheet, attribution)"
```

---

### Task 2: Create Ghost Model JavaScript

**Files:**
- Create: `app/static/js/ghostModel.js`

- [ ] **Step 1: Create the ghostModel.js file**

```javascript
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
        { id: 'auto', label: 'Auto', icon: '⚡', note: 'smart routing' },
        { id: 'llama-3.3-70b-versatile', label: 'Llama 70B', icon: '🦙', note: 'fast all-rounder' },
        { id: 'qwen/qwen3-32b', label: 'Qwen 32B', icon: '🧠', note: 'coding & math' },
        { id: 'qwen-3-235b-a22b-instruct-2507', label: 'Qwen 235B', icon: '🧠', note: 'complex reasoning' },
        { id: 'openai/gpt-oss-120b', label: 'GPT-OSS 120B', icon: '🤖', note: 'large model' },
        { id: 'deepseek-chat', label: 'DeepSeek V3', icon: '💻', note: 'top-tier coding' },
        { id: 'deepseek-reasoner', label: 'DeepSeek R1', icon: '💻', note: 'reasoning' },
        { id: 'cognitivecomputations/dolphin-mistral-24b-venice-edition:free', label: 'Venice', icon: '🔓', note: 'uncensored' },
        { id: 'kimi-k2.5', label: 'Kimi K2.5', icon: '📚', note: 'reasoning' },
        { id: 'kimi-latest', label: 'Kimi Latest', icon: '📚', note: 'long context' },
        { id: 'gemma4:e2b', label: 'Gemma 4', icon: '🏠', note: 'local model' },
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
        uncensored: 'cognitivecomputations/dolphin-mistral-24b-venice-edition:free',
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
            this.iconEl = document.getElementById('ghost-model-icon');
            this.hintEl = document.getElementById('ghost-model-hint');
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
            const icon = this.lockedModel
                ? TASK_ICON_MAP[this.currentTask] || '⚡'
                : (TASK_ICON_MAP[this.currentTask] || '⚡');

            this.labelEl.textContent = `via ${info.label}`;
            this.iconEl.textContent = icon;

            // Show/hide clear button
            if (this.clearEl) {
                this.clearEl.style.display = this.lockedModel ? 'inline' : 'none';
            }

            // Show/hide hint
            if (this.hintEl) {
                this.hintEl.style.display = this.lockedModel ? 'none' : '';
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

            MODELS.forEach(model => {
                const pill = document.createElement('button');
                pill.className = 'ghost-pill';
                pill.dataset.model = model.id;
                pill.textContent = model.label;
                pill.title = model.note;
                pill.setAttribute('role', 'option');
                pill.setAttribute('tabindex', '-1');

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

            if (this.pillsOpen) {
                this.updateGhostDisplay();
                // Focus first pill for keyboard nav
                const firstPill = this.pillsEl.querySelector('.ghost-pill');
                if (firstPill) firstPill.focus();
            }
        }

        closePills() {
            this.pillsOpen = false;
            this.pillsEl?.classList.remove('active');
        }

        // ---- Bottom Sheet (Mobile) ----

        buildSheet() {
            if (!this.sheetGrid) return;
            this.sheetGrid.innerHTML = '';

            MODELS.forEach(model => {
                const option = document.createElement('div');
                option.className = 'ghost-bottom-sheet__option';
                option.dataset.model = model.id;
                option.innerHTML = `<span>${model.icon}</span><span>${model.label}</span>`;

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
            this.crossfadeGhost();
        }

        clearLock() {
            this.lockedModel = null;
            this.overrideModel = null;
            localStorage.removeItem('ghostLockedModel');
            this.detectAndUpdate();
            this.crossfadeGhost();
        }

        // ---- Public API (for codeWindow.js) ----

        getSelectedModel() {
            if (this.lockedModel) return this.lockedModel;
            if (this.overrideModel) return this.overrideModel;
            return 'auto';
        }

        resetAfterSend() {
            if (!this.lockedModel) {
                this.overrideModel = null;
                this.currentTask = 'chat';
                this.suggestedModel = 'auto';
                this.updateGhostDisplay();
            }
        }
    }

    // ============================================
    // RESPONSE ATTRIBUTION HELPER
    // ============================================

    /**
     * Append a "via ModelName" attribution to an AI message element.
     * Called from displayMessage in codeWindow.js.
     */
    function addModelAttribution(messageDiv, modelMeta) {
        if (!messageDiv || !modelMeta) return;

        const label = modelMeta.model_label || modelMeta.model_id;
        if (!label) return;

        const attr = document.createElement('div');
        attr.className = 'message-model-attribution';

        let text = `via ${label}`;
        if (modelMeta.is_fallback) {
            text += ' (fallback)';
        }
        attr.textContent = text;

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
```

- [ ] **Step 2: Commit**

```bash
git add app/static/js/ghostModel.js
git commit -m "feat: add Ghost Model selector JS (intent detection, pills, bottom sheet)"
```

---

### Task 3: Update index.html — Remove Old Dropdown, Add Ghost HTML

**Files:**
- Modify: `app/templates/index.html`

- [ ] **Step 1: Remove old desktop dropdown**

In `app/templates/index.html`, find the `model-select-wrapper` block (around line 478-554) and replace it with an empty placeholder that keeps the header layout intact:

```html
<!-- Old dropdown removed — model selection now handled by ghost label below input -->
```

Remove this entire block:
```html
<div class="model-select-wrapper{% if is_guest %} guest-disabled{% endif %}">
    <div class="model-select{% if is_guest %} disabled{% endif %}" id="model" ...>
        Auto (Recommended)
    </div>
    <div class="model-select-content" id="model-list" ...>
        ... all the model-option divs ...
    </div>
</div>
```

- [ ] **Step 2: Remove old mobile model selector**

Find the `mobile-model-select` block (around line 232-242) and remove it:

```html
<div class="mobile-model-select..." id="mobile-model-select">
    ...
</div>
```

- [ ] **Step 3: Add ghost label HTML after the textarea-container**

After the closing `</div>` of `textarea-container` (after the send button, around line 741), add:

```html
                                </div>
                                <!-- Ghost Model Selector -->
                                <div class="ghost-model{% if is_guest %} guest-disabled{% endif %}" id="ghost-model" role="status" aria-live="polite" aria-label="Current AI model">
                                    <span class="ghost-model__label" id="ghost-model-label">via Auto</span>
                                    <span class="ghost-model__icon" id="ghost-model-icon">⚡</span>
                                    <span class="ghost-model__hint" id="ghost-model-hint">✦</span>
                                    <span class="ghost-model__clear" id="ghost-model-clear" style="display:none" title="Clear model override">×</span>
                                </div>
                                <div class="ghost-pills" id="ghost-pills" role="listbox" aria-label="Override model selection"></div>
```

- [ ] **Step 4: Add mobile bottom sheet HTML before closing body**

Before the `</body>` tag, add:

```html
    <!-- Ghost Model Bottom Sheet (Mobile) -->
    <div class="ghost-bottom-sheet__backdrop" id="ghost-bottom-sheet-backdrop"></div>
    <div class="ghost-bottom-sheet" id="ghost-bottom-sheet" role="dialog" aria-label="Select AI model">
        <div class="ghost-bottom-sheet__handle"></div>
        <div class="ghost-bottom-sheet__grid" id="ghost-bottom-sheet-grid"></div>
    </div>
```

- [ ] **Step 5: Swap CSS and JS references**

Replace:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/model-select.css') }}">
```
With:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/ghost-model.css') }}">
```

Replace:
```html
<script src="{{ url_for('static', filename='js/modelSelect.js') }}"></script>
```
With:
```html
<script src="{{ url_for('static', filename='js/ghostModel.js') }}"></script>
```

- [ ] **Step 6: Commit**

```bash
git add app/templates/index.html
git commit -m "feat: replace dropdown with ghost model HTML in index.html"
```

---

### Task 4: Update codeWindow.js — Read Model from Ghost, Add Attribution

**Files:**
- Modify: `app/static/js/codeWindow.js`

- [ ] **Step 1: Update model reading in sendMessage**

Find (around line 2696-2698):
```javascript
// Get model selection
const modelSelect = document.getElementById('model');
const model = modelSelect?.dataset.value || 'llama-3.3-70b-versatile';
```

Replace with:
```javascript
// Get model selection from Ghost Model selector
const model = (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
```

- [ ] **Step 2: Update early model check for DeepSeek indicator**

Find (around line 2647-2648):
```javascript
const modelSelectEarly = document.getElementById('model');
const currentModelEarly = modelSelectEarly?.dataset.value || 'llama-3.3-70b-versatile';
```

Replace with:
```javascript
const currentModelEarly = (window.ghostModel && window.ghostModel.getSelectedModel()) || 'auto';
```

- [ ] **Step 3: Add response attribution after model meta is stored**

Find (around line 2312-2324):
```javascript
// Auto model details (store on DOM node; action-buttons renderer will display it in-theme)
if (normalizedSender === 'ai' && modelMeta && modelMeta.auto_selected) {
    try {
        messageDiv.dataset.autoSelected = 'true';
        if (modelMeta.model_id) messageDiv.dataset.modelId = modelMeta.model_id;
        if (modelMeta.model_label) messageDiv.dataset.modelLabel = modelMeta.model_label;
        if (modelMeta.model_provider) messageDiv.dataset.modelProvider = modelMeta.model_provider;
        if (modelMeta.model_provider_label) messageDiv.dataset.modelProviderLabel = modelMeta.model_provider_label;
        if (modelMeta.task_type) messageDiv.dataset.taskType = modelMeta.task_type;
    } catch (e) {
        console.warn('Failed to attach model meta to message element:', e);
    }
}
```

Replace with:
```javascript
// Auto model details (store on DOM node; action-buttons renderer will display it in-theme)
if (normalizedSender === 'ai' && modelMeta && modelMeta.auto_selected) {
    try {
        messageDiv.dataset.autoSelected = 'true';
        if (modelMeta.model_id) messageDiv.dataset.modelId = modelMeta.model_id;
        if (modelMeta.model_label) messageDiv.dataset.modelLabel = modelMeta.model_label;
        if (modelMeta.model_provider) messageDiv.dataset.modelProvider = modelMeta.model_provider;
        if (modelMeta.model_provider_label) messageDiv.dataset.modelProviderLabel = modelMeta.model_provider_label;
        if (modelMeta.task_type) messageDiv.dataset.taskType = modelMeta.task_type;
    } catch (e) {
        console.warn('Failed to attach model meta to message element:', e);
    }
}

// Ghost Model: add "via ModelName" attribution to AI messages
if (normalizedSender === 'ai' && modelMeta && typeof window.addModelAttribution === 'function') {
    window.addModelAttribution(messageDiv, modelMeta);
}
```

- [ ] **Step 4: Reset ghost after send**

In the `sendMessage` function, after `formData.append('model', model);` (around line 2699), add:

```javascript
// Reset ghost model for next message
if (window.ghostModel) {
    window.ghostModel.resetAfterSend();
}
```

- [ ] **Step 5: Dispatch messageSent event**

Still in `sendMessage`, after the fetch call succeeds and response is processed, find where the AI response is displayed and add before it:

```javascript
document.dispatchEvent(new CustomEvent('messageSent'));
```

- [ ] **Step 6: Commit**

```bash
git add app/static/js/codeWindow.js
git commit -m "feat: wire codeWindow.js to Ghost Model selector + response attribution"
```

---

### Task 5: Clean Up Old Files and Mobile CSS

**Files:**
- Delete: `app/static/js/modelSelect.js`
- Delete: `app/static/css/model-select.css`
- Modify: `app/static/css/mobile.css`

- [ ] **Step 1: Delete old model selector files**

```bash
rm app/static/js/modelSelect.js
rm app/static/css/model-select.css
```

- [ ] **Step 2: Remove old mobile model styles from mobile.css**

In `app/static/css/mobile.css`, find and remove all rules referencing `.mobile-model-select`, `.mobile-model-header`, `.mobile-model-name`, `.mobile-model-arrow`, `.mobile-model-options`, `.mobile-model-option`. These are the styles around lines 370-430+ (search for `mobile-model`).

Also remove any rules referencing `.model-select-wrapper` positioning overrides in mobile.css.

- [ ] **Step 3: Commit**

```bash
git rm app/static/js/modelSelect.js
git rm app/static/css/model-select.css
git add app/static/css/mobile.css
git commit -m "cleanup: remove old dropdown model selector files and mobile styles"
```

---

### Task 6: Smoke Test and Restart

- [ ] **Step 1: Restart the service**

```bash
sudo systemctl restart xelasphere
```

- [ ] **Step 2: Verify service is running**

```bash
systemctl status xelasphere --no-pager | head -8
```

Expected: `Active: active (running)`

- [ ] **Step 3: Test in browser — verify ghost label appears**

Open the app. Below the chat input, you should see `via Auto ⚡ ✦` in faint text, right-aligned.

- [ ] **Step 4: Test intent detection**

Type `help me fix this python error` in the input. After 400ms, ghost should crossfade to `via DeepSeek V3 💻`.

Clear input and type `explain quantum physics step by step`. Ghost should show `via Qwen 32B 🧠`.

Clear input. Ghost returns to `via Auto ⚡`.

- [ ] **Step 5: Test pill override**

Click the ghost label. Pills should appear below. Click "Venice". Ghost should show `via Venice 🔓`. Send a message. After response, ghost resets to `via Auto ⚡`.

- [ ] **Step 6: Test permanent lock**

Double-click a pill (e.g., "DeepSeek V3"). Ghost shows `via DeepSeek V3 💻 ×`. Type anything — ghost stays locked. Click `×` to clear. Ghost returns to auto-detect.

- [ ] **Step 7: Test response attribution**

Send any message. The AI response should have a faint `via ModelName` label at the bottom-right.

- [ ] **Step 8: Test mobile bottom sheet**

Open browser dev tools, toggle mobile viewport (375px width). Click ghost label. A bottom sheet should slide up with a 2-column grid of models. Tap one to select. Swipe down or tap backdrop to dismiss.

- [ ] **Step 9: Check logs**

```bash
journalctl -u xelasphere --since "2 min ago" --no-pager | grep -E "Router|Task detected"
```

Verify routing still works correctly with the model values sent by the ghost.

- [ ] **Step 10: Commit final state (if any fixes needed)**

```bash
git add -A
git commit -m "fix: smoke test fixes for Ghost Model selector"
```
