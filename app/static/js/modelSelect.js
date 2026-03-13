document.addEventListener('DOMContentLoaded', function () {
    const MODEL_ALIAS_MAP = {
        'venice-uncensored': 'cognitivecomputations/dolphin-mistral-24b-venice-edition:free',
        'openrouter/venice-uncensored': 'cognitivecomputations/dolphin-mistral-24b-venice-edition:free',
        'deepseek-v3': 'deepseek-chat',
        'deepseek-r1': 'deepseek-reasoner'
    };

    /**
     * @typedef {Object} DropdownElements
     * @property {HTMLElement} select - The dropdown trigger element
     * @property {HTMLElement} list - The dropdown options container
     * @property {HTMLElement[]} options - Array of option elements
     */

    /**
     * @typedef {Object} DropdownState
     * @property {string} selectedValue - Currently selected value
     * @property {string} selectedText - Display text of selected option
     */

    class CustomDropdown {
        /**
         * @param {string} selectId - ID of the dropdown trigger element
         * @param {string} listId - ID of the dropdown list container
         */
        constructor(selectId, listId) {
            // Initialize elements
            this.elements = this.initializeElements(selectId, listId);
            
            // Initialize state
            this.state = {
                selectedValue: '',
                selectedText: ''
            };

            // Bind methods to preserve context
            this.handleClickOutside = this.handleClickOutside.bind(this);
            this.handleKeyboardNavigation = this.handleKeyboardNavigation.bind(this);
            this.toggleDropdown = this.toggleDropdown.bind(this);

            // Set up event listeners
            this.setupEventListeners();
            
            // Initialize selection
            this.initializeSelection();
        }

        /**
         * Initialize and validate required DOM elements
         * @throws {Error} If required elements are not found
         */
        initializeElements(selectId, listId) {
            const select = document.getElementById(selectId);
            const list = document.getElementById(listId);

            if (!select || !list) {
                throw new Error('Required dropdown elements not found');
            }

            const options = Array.from(list.querySelectorAll('[role="option"]'));
            if (!options.length) {
                throw new Error('No options found in dropdown');
            }

            return { select, list, options };
        }

        /**
         * Set up all event listeners
         */
        setupEventListeners() {
            // Dropdown toggle
            this.elements.select.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleDropdown();
            });

            // Option selection
            this.elements.options.forEach(option => {
                option.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.selectOption(option);
                });
            });

            // Keyboard navigation
            this.elements.select.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.toggleDropdown();
                }
            });

            this.elements.list.addEventListener('keydown', this.handleKeyboardNavigation);

            // Close on outside click
            document.addEventListener('click', this.handleClickOutside);
        }

        /**
         * Toggle dropdown visibility
         * @param {boolean} [forceClose=false] - Force the dropdown to close
         */
        toggleDropdown(forceClose = false) {
            const isExpanded = this.elements.select.getAttribute('aria-expanded') === 'true';
            const shouldExpand = !isExpanded && !forceClose;

            this.elements.select.setAttribute('aria-expanded', shouldExpand);
            this.elements.list.classList.toggle('active', shouldExpand);
            this.elements.select.classList.toggle('active', shouldExpand);

            // Update ARIA attributes
            if (shouldExpand) {
                this.elements.list.setAttribute('aria-hidden', 'false');
            } else {
                this.elements.list.setAttribute('aria-hidden', 'true');
            }
        }

        /**
         * Select an option from the dropdown
         * @param {HTMLElement} option - The option element to select
         */
        selectOption(option) {
            if (!option) return;

            // Update state
            this.state.selectedValue = option.getAttribute('data-value');
            this.state.selectedText = option.getAttribute('data-label') || option.textContent.trim();

            // Update DOM
            this.elements.options.forEach(opt => {
                opt.setAttribute('aria-selected', 'false');
                opt.classList.remove('selected');
            });

            option.setAttribute('aria-selected', 'true');
            option.classList.add('selected');
            
            this.elements.select.textContent = this.state.selectedText;
            this.elements.select.dataset.value = this.state.selectedValue;

            // Persist selection
            localStorage.setItem('selectedModel', this.state.selectedValue);

            // Close dropdown and focus
            this.toggleDropdown(true);
            this.elements.select.focus();

            // Dispatch event
            document.dispatchEvent(new CustomEvent('modelSelected', {
                detail: {
                    value: this.state.selectedValue,
                    text: this.state.selectedText
                }
            }));
        }

        /**
         * Handle keyboard navigation within the dropdown
         * @param {KeyboardEvent} e - Keyboard event
         */
        handleKeyboardNavigation(e) {
            if (!this.elements.list.classList.contains('active')) return;

            const activeOption = this.elements.options.find(
                opt => opt.getAttribute('aria-selected') === 'true'
            );
            const currentIndex = activeOption ? 
                this.elements.options.indexOf(activeOption) : -1;

            switch (e.key) {
                case 'ArrowUp':
                    e.preventDefault();
                    if (currentIndex > 0) {
                        this.elements.options[currentIndex - 1].focus();
                    }
                    break;

                case 'ArrowDown':
                    e.preventDefault();
                    if (currentIndex < this.elements.options.length - 1) {
                        this.elements.options[currentIndex + 1].focus();
                    }
                    break;

                case 'Enter':
                case ' ':
                    e.preventDefault();
                    if (document.activeElement.getAttribute('role') === 'option') {
                        this.selectOption(document.activeElement);
                    }
                    break;

                case 'Escape':
                    e.preventDefault();
                    this.toggleDropdown(true);
                    this.elements.select.focus();
                    break;
            }
        }

        /**
         * Handle clicks outside the dropdown
         * @param {MouseEvent} event - Click event
         */
        handleClickOutside(event) {
            if (!this.elements.select.contains(event.target) &&
                !this.elements.list.contains(event.target)) {
                this.toggleDropdown(true);
            }
        }

        /**
         * Initialize dropdown selection from localStorage or default
         */
        initializeSelection() {
            const savedModel = localStorage.getItem('selectedModel');
            const normalizedModel = MODEL_ALIAS_MAP[savedModel] || savedModel;

            if (savedModel && normalizedModel !== savedModel) {
                localStorage.setItem('selectedModel', normalizedModel);
            }

            const savedOption = this.elements.options.find(
                option => option.getAttribute('data-value') === normalizedModel
            );

            if (savedOption) {
                this.selectOption(savedOption);
            } else if (this.elements.options.length > 0) {
                this.selectOption(this.elements.options[0]);
            }

            // Ensure dropdown starts closed
            this.toggleDropdown(true);
        }

        /**
         * Clean up event listeners
         */
        destroy() {
            document.removeEventListener('click', this.handleClickOutside);
            this.elements.list.removeEventListener('keydown', this.handleKeyboardNavigation);
        }
    }

    // Initialize the dropdown
    try {
        const dropdown = new CustomDropdown('model', 'model-list');

        // Optional: Make the instance available globally for debugging
        window.modelDropdown = dropdown;

        // === Mobile Inline Expanding Model Selector ===
        const mobileModelSelect = document.getElementById('mobile-model-select');
        const mobileModelName = mobileModelSelect?.querySelector('.mobile-model-name');
        const mobileModelOptions = document.getElementById('mobile-model-options');
        const mobileModelHeader = mobileModelSelect?.querySelector('.mobile-model-header');

        if (mobileModelSelect && mobileModelName && mobileModelOptions) {
            // Populate mobile options from desktop model list
            const populateMobileOptions = () => {
                mobileModelOptions.innerHTML = '';
                dropdown.elements.options.forEach(option => {
                    const value = option.getAttribute('data-value');
                    const label = option.getAttribute('data-label');
                    const note = option.querySelector('.model-note')?.textContent || '';
                    const isSelected = option.getAttribute('aria-selected') === 'true';

                    const optionEl = document.createElement('div');
                    optionEl.className = `mobile-model-option${isSelected ? ' selected' : ''}`;
                    optionEl.dataset.value = value;
                    optionEl.innerHTML = `
                        <span class="mobile-model-option-name">${label}</span>
                        <span class="mobile-model-option-note">${note}</span>
                    `;
                    mobileModelOptions.appendChild(optionEl);
                });
            };

            // Initialize options
            populateMobileOptions();

            // Toggle expand/collapse
            const toggleExpand = (forceClose = false) => {
                const isExpanded = mobileModelSelect.classList.contains('expanded');
                if (forceClose || isExpanded) {
                    mobileModelSelect.classList.remove('expanded');
                } else {
                    mobileModelSelect.classList.add('expanded');
                }
            };

            // Click on header toggles expansion
            mobileModelHeader.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleExpand();
            });

            // Click on option selects it
            mobileModelOptions.addEventListener('click', (e) => {
                const optionEl = e.target.closest('.mobile-model-option');
                if (!optionEl) return;

                e.stopPropagation();
                const value = optionEl.dataset.value;

                // Find and select the corresponding desktop option
                const desktopOption = dropdown.elements.options.find(
                    opt => opt.getAttribute('data-value') === value
                );
                if (desktopOption) {
                    dropdown.selectOption(desktopOption);
                }

                // Update mobile UI
                populateMobileOptions();
                toggleExpand(true);
            });

            // Close when clicking outside
            document.addEventListener('click', (e) => {
                if (!mobileModelSelect.contains(e.target)) {
                    toggleExpand(true);
                }
            });

            // Listen for model changes from desktop and update mobile
            document.addEventListener('modelSelected', (e) => {
                mobileModelName.textContent = e.detail.text;
                populateMobileOptions();
            });

            // Initialize with current selection
            const currentText = dropdown.elements.select.textContent;
            if (currentText) {
                mobileModelName.textContent = currentText;
            }
        }
    } catch (error) {
        console.error('Failed to initialize dropdown:', error);
    }
});
