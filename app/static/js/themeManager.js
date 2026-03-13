/**
 * Theme Manager
 * Handles dark/light theme switching with localStorage persistence
 */

(function() {
  'use strict';

  const STORAGE_KEY = 'xelasphere-theme';
  const THEME_ATTR = 'data-theme';
  const THEMES = {
    DARK: 'dark',
    LIGHT: 'light'
  };

  class ThemeManager {
    constructor() {
      this.currentTheme = this.getInitialTheme();
      this.toggleButton = null;
      this.transitionOverlay = null;

      // Initialize immediately to prevent flash
      this.applyTheme(this.currentTheme, false);

      // Wait for DOM to be ready for UI elements
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => this.init());
      } else {
        this.init();
      }
    }

    /**
     * Get initial theme from localStorage or system preference
     */
    getInitialTheme() {
      // Check localStorage first
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && (stored === THEMES.DARK || stored === THEMES.LIGHT)) {
        return stored;
      }

      // Check system preference
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        return THEMES.LIGHT;
      }

      // Default to dark
      return THEMES.DARK;
    }

    /**
     * Initialize theme toggle UI
     */
    init() {
      this.createToggleButton();
      this.createTransitionOverlay();
      this.setupSystemThemeListener();

      // Announce initial theme to screen readers
      this.announceTheme();
    }

    /**
     * Create theme toggle button
     */
    createToggleButton() {
      // Check if button already exists
      if (document.querySelector('.theme-toggle')) {
        this.toggleButton = document.querySelector('.theme-toggle__button');
        this.attachToggleListener();
        return;
      }

      const container = document.createElement('div');
      container.className = 'theme-toggle';
      container.setAttribute('role', 'complementary');
      container.setAttribute('aria-label', 'Theme settings');

      const button = document.createElement('button');
      button.className = 'theme-toggle__button';
      button.setAttribute('type', 'button');
      button.setAttribute('aria-label', `Switch to ${this.currentTheme === THEMES.DARK ? 'light' : 'dark'} theme`);
      button.setAttribute('title', `Switch to ${this.currentTheme === THEMES.DARK ? 'light' : 'dark'} theme`);

      // Icon container
      const iconContainer = document.createElement('div');
      iconContainer.className = 'theme-toggle__icon-container';

      // Sun icon (light theme)
      const sunIcon = document.createElement('i');
      sunIcon.className = 'fas fa-sun theme-toggle__icon theme-toggle__icon--sun';
      sunIcon.setAttribute('aria-hidden', 'true');

      // Moon icon (dark theme)
      const moonIcon = document.createElement('i');
      moonIcon.className = 'fas fa-moon theme-toggle__icon theme-toggle__icon--moon';
      moonIcon.setAttribute('aria-hidden', 'true');

      iconContainer.appendChild(sunIcon);
      iconContainer.appendChild(moonIcon);

      // Tooltip
      const tooltip = document.createElement('span');
      tooltip.className = 'theme-toggle__tooltip';
      tooltip.textContent = this.currentTheme === THEMES.DARK ? 'Light mode' : 'Dark mode';

      button.appendChild(iconContainer);
      button.appendChild(tooltip);
      container.appendChild(button);

      document.body.appendChild(container);
      this.toggleButton = button;

      this.attachToggleListener();
    }

    /**
     * Create transition overlay for smooth theme switching
     */
    createTransitionOverlay() {
      const overlay = document.createElement('div');
      overlay.className = 'theme-transition-overlay';
      overlay.setAttribute('aria-hidden', 'true');
      document.body.appendChild(overlay);
      this.transitionOverlay = overlay;
    }

    /**
     * Attach click listener to toggle button
     */
    attachToggleListener() {
      if (!this.toggleButton) return;

      this.toggleButton.addEventListener('click', () => {
        this.toggle();
      });

      // Keyboard shortcut: Ctrl/Cmd + Shift + T
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
          e.preventDefault();
          this.toggle();

          // Show toast notification
          if (window.Toast) {
            Toast.info(`Switched to ${this.currentTheme === THEMES.DARK ? 'dark' : 'light'} theme`, {
              duration: 2000
            });
          }
        }
      });
    }

    /**
     * Toggle between themes
     */
    toggle() {
      const newTheme = this.currentTheme === THEMES.DARK ? THEMES.LIGHT : THEMES.DARK;
      this.setTheme(newTheme);
    }

    /**
     * Set specific theme
     * @param {string} theme - Theme to set (THEMES.DARK or THEMES.LIGHT)
     * @param {boolean} animate - Whether to show transition animation
     */
    setTheme(theme, animate = true) {
      if (theme !== THEMES.DARK && theme !== THEMES.LIGHT) {
        console.warn(`Invalid theme: ${theme}. Using dark theme.`);
        theme = THEMES.DARK;
      }

      if (theme === this.currentTheme) return;

      this.currentTheme = theme;

      // Apply theme with optional animation
      this.applyTheme(theme, animate);

      // Save to localStorage
      localStorage.setItem(STORAGE_KEY, theme);

      // Update button attributes
      this.updateToggleButton();

      // Announce to screen readers
      this.announceTheme();

      // Dispatch custom event
      window.dispatchEvent(new CustomEvent('themechange', {
        detail: { theme }
      }));
    }

    /**
     * Apply theme to document
     * @param {string} theme - Theme to apply
     * @param {boolean} animate - Whether to show transition animation
     */
    applyTheme(theme, animate = true) {
      if (animate && this.transitionOverlay && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        // Show overlay
        this.transitionOverlay.classList.add('is-active');

        // Small delay for visual effect
        setTimeout(() => {
          document.documentElement.setAttribute(THEME_ATTR, theme);

          // Hide overlay after theme applied
          setTimeout(() => {
            this.transitionOverlay.classList.remove('is-active');
          }, 150);
        }, 150);
      } else {
        document.documentElement.setAttribute(THEME_ATTR, theme);
      }
    }

    /**
     * Update toggle button state
     */
    updateToggleButton() {
      if (!this.toggleButton) return;

      const isDark = this.currentTheme === THEMES.DARK;
      const newTheme = isDark ? 'light' : 'dark';

      this.toggleButton.setAttribute('aria-label', `Switch to ${newTheme} theme`);
      this.toggleButton.setAttribute('title', `Switch to ${newTheme} theme`);

      // Update tooltip
      const tooltip = this.toggleButton.querySelector('.theme-toggle__tooltip');
      if (tooltip) {
        tooltip.textContent = isDark ? 'Light mode' : 'Dark mode';
      }
    }

    /**
     * Announce theme change to screen readers
     */
    announceTheme() {
      const announcement = document.createElement('div');
      announcement.setAttribute('role', 'status');
      announcement.setAttribute('aria-live', 'polite');
      announcement.className = 'sr-only';
      announcement.textContent = `Theme changed to ${this.currentTheme} mode`;

      document.body.appendChild(announcement);

      // Remove after announcement
      setTimeout(() => {
        document.body.removeChild(announcement);
      }, 1000);
    }

    /**
     * Listen for system theme changes
     */
    setupSystemThemeListener() {
      if (!window.matchMedia) return;

      const darkModeQuery = window.matchMedia('(prefers-color-scheme: dark)');

      const handleSystemThemeChange = (e) => {
        // Only auto-switch if user hasn't manually set a preference
        if (!localStorage.getItem(STORAGE_KEY)) {
          const newTheme = e.matches ? THEMES.DARK : THEMES.LIGHT;
          this.setTheme(newTheme, true);
        }
      };

      // Modern browsers
      if (darkModeQuery.addEventListener) {
        darkModeQuery.addEventListener('change', handleSystemThemeChange);
      } else {
        // Fallback for older browsers
        darkModeQuery.addListener(handleSystemThemeChange);
      }
    }

    /**
     * Get current theme
     * @returns {string} Current theme
     */
    getTheme() {
      return this.currentTheme;
    }

    /**
     * Check if dark theme is active
     * @returns {boolean} True if dark theme is active
     */
    isDark() {
      return this.currentTheme === THEMES.DARK;
    }

    /**
     * Check if light theme is active
     * @returns {boolean} True if light theme is active
     */
    isLight() {
      return this.currentTheme === THEMES.LIGHT;
    }

    /**
     * Reset to default theme (dark)
     */
    reset() {
      localStorage.removeItem(STORAGE_KEY);
      this.setTheme(THEMES.DARK, true);
    }
  }

  // Create global instance
  window.ThemeManager = new ThemeManager();

  // Export for modules
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = window.ThemeManager;
  }
})();
