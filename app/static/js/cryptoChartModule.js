/**
 * Crypto Chart Module
 * Renders interactive cryptocurrency price charts using Lightweight Charts (TradingView)
 * Integrates with CoinGecko API via backend endpoints
 */

const CryptoChartModule = (function() {
    'use strict';

    // Chart instances storage
    const chartInstances = new Map();

    // Rate limiting - track last request time per chart
    const lastRequestTime = new Map();
    const MIN_REQUEST_INTERVAL = 1000; // 1 second between requests

    // Config cache - fetched from API
    let configCache = null;
    let configLoading = null; // Promise for config loading

    // Default chart options (lazy-loaded to ensure LightweightCharts is available)
    function getDefaultChartOptions() {
        return {
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: 'rgba(255, 255, 255, 0.7)',
                fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
            },
            // Prefix all prices on the right axis with "$" so the user knows
            // the unit at a glance. Decimal precision adapts to magnitude
            // so sub-cent tokens (SHIB, PEPE, etc.) don't show as $0.00.
            localization: {
                priceFormatter: (price) => {
                    const abs = Math.abs(Number(price));
                    let maxFrac, minFrac;
                    if (abs >= 1) { minFrac = 2; maxFrac = 2; }
                    else if (abs >= 0.01) { minFrac = 4; maxFrac = 4; }
                    else if (abs >= 0.0001) { minFrac = 6; maxFrac = 6; }
                    else { minFrac = 8; maxFrac = 8; }
                    return '$' + Number(price).toLocaleString('en-US', {
                        minimumFractionDigits: minFrac,
                        maximumFractionDigits: maxFrac,
                    });
                },
            },
            grid: {
                vertLines: { visible: false },
                // Stronger horizontal gridlines so price levels are
                // visually anchored. Was 0.06 (almost invisible);
                // 0.14 reads as clear-but-not-distracting.
                horzLines: { color: 'rgba(255, 255, 255, 0.14)', style: 1 },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {
                    color: 'rgba(255, 255, 255, 0.3)',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                },
                horzLine: {
                    color: 'rgba(255, 255, 255, 0.3)',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                },
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true,
                secondsVisible: false,
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
            },
            handleScroll: {
                mouseWheel: true,
                pressedMouseMove: true,
                horzTouchDrag: true,
                vertTouchDrag: false,
            },
            handleScale: {
                axisPressedMouseMove: true,
                mouseWheel: true,
                pinch: true,
            },
            // Mobile-friendly: kinetic scroll on touch (momentum after
            // the finger lifts) makes panning feel native, not janky.
            kineticScroll: {
                mouse: false,
                touch: true,
            },
        };
    }

    // Time range options (fallback, will be updated from API config)
    let TIME_RANGES = {
        '1D': { days: '1', label: '1D' },
        '1W': { days: '7', label: '1W' },
        '1M': { days: '30', label: '1M' },
        '1Y': { days: '365', label: '1Y' },
        'MAX': { days: 'max', label: 'ALL' },
    };

    /**
     * Fetch configuration from backend API
     */
    async function fetchConfig() {
        // Return cached config if available
        if (configCache) {
            return configCache;
        }

        // Return existing promise if already loading
        if (configLoading) {
            return configLoading;
        }

        // Start fetching config. Resets configLoading on completion (success
        // or failure) so a transient network error doesn't permanently
        // prevent retries.
        configLoading = (async () => {
            try {
                const response = await fetch('/api/crypto/config', {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                });

                if (!response.ok) {
                    console.warn('[CryptoChart] Failed to fetch config, using defaults');
                    return null;
                }

                const config = await response.json();
                if (config.success) {
                    configCache = config;
                    // Update TIME_RANGES if available
                    if (config.time_ranges) {
                        TIME_RANGES = config.time_ranges;
                    }
                    console.log('[CryptoChart] Config loaded from API');
                    return config;
                }
            } catch (error) {
                console.warn('[CryptoChart] Config fetch error:', error);
            } finally {
                // Clear the in-flight promise so the next fetchConfig() call
                // can retry. Without this, configCache stays null and
                // configLoading stays as the dead resolved-to-null promise
                // forever, locking us into the fallback TIME_RANGES.
                configLoading = null;
            }
            return null;
        })();

        return configLoading;
    }

    /**
     * Format price with appropriate decimal places
     */
    function formatPrice(price, currency = 'USD') {
        if (price >= 1000) {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: currency,
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            }).format(price);
        } else if (price >= 1) {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: currency,
                minimumFractionDigits: 2,
                maximumFractionDigits: 4
            }).format(price);
        } else {
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: currency,
                minimumFractionDigits: 4,
                maximumFractionDigits: 8
            }).format(price);
        }
    }

    /**
     * Escape HTML for safe interpolation into innerHTML.
     * Pre-escapes &, <, >, ", ' so attacker-controlled API content
     * (news titles, whale wallet IDs, symbol names) cannot inject
     * <script> or break out of attribute values.
     */
    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /**
     * Validate a URL for safe href use. Rejects javascript:, data:,
     * vbscript: and similar exfiltration vectors. Returns the URL if
     * safe, '#' otherwise (so links degrade to no-op rather than break).
     */
    function safeUrl(url) {
        if (!url) return '#';
        const trimmed = String(url).trim().toLowerCase();
        if (trimmed.startsWith('javascript:') ||
            trimmed.startsWith('data:') ||
            trimmed.startsWith('vbscript:') ||
            trimmed.startsWith('file:')) {
            return '#';
        }
        return url;
    }

    /**
     * Format large numbers (market cap, volume)
     */
    function formatLargeNumber(num) {
        // Defensive guard: callers pass null/undefined occasionally
        // (missing market_cap on new tokens, NaN from arithmetic). Rather
        // than throwing on .toFixed of null, return '--' which the UI
        // already treats as a valid placeholder.
        if (num == null || !Number.isFinite(num)) return '--';
        if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
        if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
        if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
        if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
        return num.toFixed(2);
    }

    /**
     * Format percentage change
     */
    // Map range button labels to time-period strings shown next to the
    // percentage. "1D" reads as "24h" because the user thinks in hours
    // for short windows, days for medium, etc.
    const RANGE_LABEL_MAP = {
        '1D': '24h',
        '1W': '7d',
        '1M': '30d',
        '1Y': '1y',
        'MAX': 'all-time',
        'ALL': 'all-time'
    };

    function formatChange(change, rangeLabel) {
        const sign = change >= 0 ? '+' : '';
        const pct = `${sign}${change.toFixed(2)}%`;
        if (!rangeLabel) return pct;
        const display = RANGE_LABEL_MAP[rangeLabel] || rangeLabel;
        return `${pct} (${display})`;
    }

    /**
     * Animate price counter from old value to new value
     */
    function animatePrice(element, newPrice, currency = 'USD', duration = 400) {
        const oldText = element.textContent;
        const oldPrice = parseFloat(oldText.replace(/[^0-9.-]/g, '')) || 0;

        // Remove shimmer if present
        element.classList.remove('shimmer-loading');

        // If no old price or same price, just set it
        if (oldPrice === 0 || Math.abs(oldPrice - newPrice) < 0.0001) {
            element.textContent = formatPrice(newPrice, currency);
            return;
        }

        // Determine direction for flash effect
        const isUp = newPrice > oldPrice;
        element.classList.add(isUp ? 'price-up' : 'price-down');

        // Animate the number
        const startTime = performance.now();
        const diff = newPrice - oldPrice;

        function update(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const currentPrice = oldPrice + (diff * eased);

            element.textContent = formatPrice(currentPrice, currency);

            if (progress < 1) {
                requestAnimationFrame(update);
            } else {
                // Clean up animation classes
                setTimeout(() => {
                    element.classList.remove('price-up', 'price-down');
                }, 100);
            }
        }

        requestAnimationFrame(update);
    }

    /**
     * Remove shimmer loading from element and set content
     */
    function setLoadedContent(element, content, className = '') {
        element.classList.remove('shimmer-loading');
        element.textContent = content;
        if (className) {
            element.className = element.className.split(' ').filter(c => !c.startsWith('shimmer')).join(' ') + ' ' + className;
        }
    }

    /**
     * Update the data source badge to show live/cache status
     */
    function updateDataSourceBadge(cardElement, dataSource, cacheAgeSeconds = 0) {
        const badge = cardElement.querySelector('.data-source-badge');
        if (!badge) return;

        if (dataSource === 'live') {
            badge.className = 'data-source-badge live';
            badge.innerHTML = 'LIVE';
        } else {
            badge.className = 'data-source-badge cache';
            // Format cache age
            let ageText;
            if (cacheAgeSeconds < 60) {
                ageText = `${cacheAgeSeconds}s`;
            } else if (cacheAgeSeconds < 3600) {
                ageText = `${Math.floor(cacheAgeSeconds / 60)}m`;
            } else {
                ageText = `${Math.floor(cacheAgeSeconds / 3600)}h`;
            }
            badge.innerHTML = `CACHED <span class="cache-age">${ageText} ago</span>`;
        }
    }

    /**
     * Get CSRF token
     */
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * Fetch chart data from backend
     */
    async function fetchChartData(coin, days = '1', currency = 'usd', signal) {
        try {
            const response = await fetch(
                `/api/crypto/chart?coin=${encodeURIComponent(coin)}&days=${days}&currency=${currency}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    },
                    signal
                }
            );

            if (!response.ok) {
                const errorText = await response.text();
                console.error('[CryptoChart] API error:', response.status, errorText);
                throw new Error(`Failed to fetch chart data: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('[CryptoChart] Fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch current price data
     */
    async function fetchPriceData(coin, currency = 'usd') {
        try {
            const response = await fetch(
                `/api/crypto/price?coin=${encodeURIComponent(coin)}&currency=${currency}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch price data');
            }

            return await response.json();
        } catch (error) {
            console.error('[CryptoChart] Price fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch stock chart data from backend
     */
    async function fetchStockChartData(symbol, range = '1D', signal) {
        try {
            const response = await fetch(
                `/api/stock/chart?symbol=${encodeURIComponent(symbol)}&range=${range}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    },
                    signal
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch stock chart data');
            }

            return await response.json();
        } catch (error) {
            console.error('[StockChart] Fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch stock price data
     */
    async function fetchStockPriceData(symbol) {
        try {
            const response = await fetch(
                `/api/stock/price?symbol=${encodeURIComponent(symbol)}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch stock price data');
            }

            return await response.json();
        } catch (error) {
            console.error('[StockChart] Price fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch detailed crypto data (market cap, ATH, supply, etc.)
     */
    async function fetchCryptoDetails(coin, currency = 'usd') {
        try {
            const response = await fetch(
                `/api/crypto/details?coin=${encodeURIComponent(coin)}&currency=${currency}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch crypto details');
            }

            const data = await response.json();
            if (data.success === false) {
                return null;
            }
            return data;
        } catch (error) {
            console.error('[CryptoChart] Details fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch crypto news from CryptoCompare
     */
    async function fetchCryptoNews(coin, limit = 4) {
        try {
            const response = await fetch(
                `/api/crypto/news?coin=${encodeURIComponent(coin)}&limit=${limit}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch crypto news');
            }

            return await response.json();
        } catch (error) {
            console.error('[CryptoChart] News fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch whale alerts (large transactions)
     */
    async function fetchWhaleAlerts(coin, limit = 5) {
        try {
            const response = await fetch(
                `/api/crypto/whales?coin=${encodeURIComponent(coin)}&limit=${limit}`,
                {
                    headers: {
                        'X-CSRFToken': getCsrfToken()
                    }
                }
            );

            if (!response.ok) {
                throw new Error('Failed to fetch whale alerts');
            }

            return await response.json();
        } catch (error) {
            console.error('[CryptoChart] Whale fetch error:', error);
            throw error;
        }
    }

    /**
     * Fetch Fear & Greed Index
     */
    async function fetchFearGreed() {
        try {
            const response = await fetch('/api/crypto/fear-greed', {
                headers: {
                    'X-CSRFToken': getCsrfToken()
                }
            });

            if (!response.ok) {
                throw new Error('Failed to fetch Fear & Greed');
            }

            return await response.json();
        } catch (error) {
            console.error('[CryptoChart] Fear & Greed fetch error:', error);
            return null;
        }
    }

    /**
     * Format relative time (e.g., "2 hours ago")
     */
    function formatRelativeTime(timestamp) {
        // Handle both Unix timestamps (numbers) and date strings (from RSS feeds)
        let epochSeconds;
        if (typeof timestamp === 'number') {
            epochSeconds = timestamp;
        } else if (typeof timestamp === 'string') {
            const parsed = Date.parse(timestamp);
            if (isNaN(parsed)) return '';
            epochSeconds = Math.floor(parsed / 1000);
        } else {
            return '';
        }

        const now = Math.floor(Date.now() / 1000);
        const diff = now - epochSeconds;

        if (diff < 0) return 'Just now';
        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return new Date(epochSeconds * 1000).toLocaleDateString();
    }

    // ==================== Logo Cache (localStorage) ==================== //
    const LOGO_CACHE_KEY = 'crypto_logo_cache';

    /**
     * Get cached logos from localStorage
     */
    function getLogoCache() {
        try {
            const cached = localStorage.getItem(LOGO_CACHE_KEY);
            return cached ? JSON.parse(cached) : {};
        } catch (e) {
            console.warn('[LogoCache] Error reading cache:', e);
            return {};
        }
    }

    /**
     * Save logo URL to localStorage cache
     */
    function cacheLogoUrl(coinId, logoUrl) {
        try {
            const cache = getLogoCache();
            cache[coinId.toLowerCase()] = logoUrl;
            localStorage.setItem(LOGO_CACHE_KEY, JSON.stringify(cache));
        } catch (e) {
            console.warn('[LogoCache] Error saving to cache:', e);
        }
    }

    /**
     * Get cached logo URL for a coin
     */
    function getCachedLogoUrl(coinId) {
        const cache = getLogoCache();
        return cache[coinId.toLowerCase()] || null;
    }

    /**
     * Get logo URL for asset - uses localStorage cache with CDN fallback
     */
    function getAssetLogoUrl(symbol, assetType = 'crypto') {
        const symbolUpper = symbol.toUpperCase();
        const symbolLower = symbol.toLowerCase();

        if (assetType === 'stock') {
            // Get the company domain. Prefer the curated mapping from
            // STOCK_LOGOS (via API config). Falls back to symbol.com.
            let domain;
            if (configCache && configCache.stock_logos) {
                domain = configCache.stock_logos[symbolUpper];
            }
            if (!domain) {
                domain = `${symbolLower}.com`;
            }
            // Clearbit's free Logo API was sunset late 2024. Google's
            // favicon service is free, fast, reliable, and returns up
            // to 128x128 PNG — plenty for our ~32px chart icon at 4x
            // retina. The configCache.clearbit_base override (if set)
            // is honored for fully-self-hosted deployments.
            const baseUrl = configCache?.clearbit_base
                || 'https://www.google.com/s2/favicons?sz=128&domain=';
            return baseUrl + domain;
        }

        // For crypto, check localStorage cache first
        const cachedUrl = getCachedLogoUrl(symbolLower);
        if (cachedUrl) {
            return cachedUrl;
        }

        // No cached logo - try CoinGecko CDN as fallback (static, no API call)
        const cdnUrl = getCdnLogoUrl(symbolLower);
        if (cdnUrl) {
            return cdnUrl;
        }

        // No known CDN URL - return null, let fallback initials show
        return null;
    }

    /**
     * Get logo URL from CoinGecko static CDN (fallback when no cache)
     * Uses known image ID and filename mappings for popular coins
     */
    function getCdnLogoUrl(coinId) {
        // CoinGecko CDN: imageId -> filename (not all filenames match coinId)
        const coinImages = {
            'bitcoin': { id: '1', file: 'bitcoin.png' },
            'ethereum': { id: '279', file: 'ethereum.png' },
            'solana': { id: '4128', file: 'solana.png' },
            'ripple': { id: '44', file: 'xrp-symbol-white-128.png' },
            'cardano': { id: '975', file: 'cardano.png' },
            'dogecoin': { id: '5', file: 'dogecoin.png' },
            'polkadot': { id: '12171', file: 'polkadot-new.png' },
            'chainlink': { id: '877', file: 'chainlink-new-logo.png' },
            'litecoin': { id: '2', file: 'litecoin.png' },
            'binancecoin': { id: '825', file: 'bnb.png' },
            'vechain': { id: '1167', file: 'VeChain-Logo-768x725.png' },
            'stellar': { id: '100', file: 'stellar.png' },
            'avalanche-2': { id: '12559', file: 'Avalanche_Circle_RedWhite_Trans.png' },
            'matic-network': { id: '4713', file: 'polygon.png' },
            'cosmos': { id: '1481', file: 'cosmos_hub.png' },
            'tron': { id: '1094', file: 'tron-logo.png' },
            'toncoin': { id: '17980', file: 'toncoin.png' },
            'shiba-inu': { id: '11939', file: 'shiba.png' },
            'uniswap': { id: '12504', file: 'uniswap-logo.png' },
            'near': { id: '10365', file: 'near.jpg' },
            'aptos': { id: '26455', file: 'aptos_round.png' }
        };

        const coin = coinImages[coinId.toLowerCase()];
        if (coin) {
            return `https://assets.coingecko.com/coins/images/${coin.id}/small/${coin.file}`;
        }
        return null;
    }

    /**
     * Get fallback initials for when logo is not available
     */
    function getAssetInitials(symbol) {
        const symbolUpper = symbol.toUpperCase();
        return symbolUpper.substring(0, 2);
    }

    /**
     * Handle logo load error - try fallbacks then show initials
     */
    function handleLogoError(img) {
        const fallback = img.nextElementSibling;
        img.style.display = 'none';
        if (fallback) fallback.style.display = 'flex';
    }

    /**
     * Handle logo load success
     */
    function handleLogoLoad(img) {
        img.classList.add('loaded');
        const fallback = img.nextElementSibling;
        if (fallback) fallback.style.display = 'none';
    }

    // Expose handlers globally for inline HTML
    window.handleLogoError = handleLogoError;
    window.handleLogoLoad = handleLogoLoad;

    /**
     * Create chart card HTML structure
     */
    function createChartCardHTML(chartId, symbol, assetType = 'crypto') {
        const logoUrl = getAssetLogoUrl(symbol, assetType);
        const initials = getAssetInitials(symbol);
        const safeSymbol = escapeHtml(symbol);
        const safeInitials = escapeHtml(initials);
        const safeLogoUrl = escapeHtml(safeUrl(logoUrl));
        const safeChartId = escapeHtml(chartId);

        // Always show fallback first, hide it when image loads successfully
        const iconHtml = logoUrl
            ? `<img class="symbol-logo" src="${safeLogoUrl}" alt="${safeSymbol}"
                   onload="handleLogoLoad(this)" onerror="handleLogoError(this)">
               <span class="symbol-icon-fallback">${safeInitials}</span>`
            : `<span class="symbol-icon-fallback">${safeInitials}</span>`;

        return `
            <div class="crypto-chart-card" id="${safeChartId}" role="region" aria-label="${escapeHtml(symbol.toUpperCase())} price chart">
                <div class="crypto-chart-header">
                    <div class="crypto-chart-info">
                        <div class="crypto-chart-symbol">
                            <span class="symbol-icon" aria-hidden="true">${iconHtml}</span>
                            <span class="symbol-name">${escapeHtml(symbol.toUpperCase())}</span>
                            <div class="fear-greed-badge" style="display: none;">
                                <span class="fg-dot"></span>
                                <span class="fg-label"></span>
                            </div>
                        </div>
                        <div class="crypto-chart-price" aria-live="polite" aria-atomic="true">
                            <span class="price-value shimmer-loading" aria-label="Current price">&nbsp;</span>
                            <span class="price-change shimmer-loading" aria-label="Price change">&nbsp;</span>
                            <span class="data-source-badge"></span>
                        </div>
                    </div>
                    <div class="crypto-chart-actions">
                        <div class="crypto-chart-ranges" role="group" aria-label="Chart time range">
                            <button class="range-btn" data-range="1D" aria-label="Show 1 day chart" aria-pressed="false">1D</button>
                            <button class="range-btn" data-range="1W" aria-label="Show 1 week chart" aria-pressed="false">1W</button>
                            <button class="range-btn" data-range="1M" aria-label="Show 1 month chart" aria-pressed="false">1M</button>
                            <button class="range-btn" data-range="1Y" aria-label="Show 1 year chart" aria-pressed="false">1Y</button>
                            <button class="range-btn active" data-range="MAX" aria-label="Show all-time chart" aria-pressed="true">ALL</button>
                        </div>
                        <button class="chart-action-btn chart-predict-btn" data-tooltip="Price Prediction" aria-label="Show price prediction" onclick="window.CryptoChart?.showPrediction('${chartId}')">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                                <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                            </svg>
                        </button>
                        <button class="chart-action-btn chart-share-btn" data-tooltip="Share as Image" aria-label="Share chart as image" onclick="window.CryptoChart?.shareChart('${chartId}')">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                                <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>
                                <polyline points="16 6 12 2 8 6"/>
                                <line x1="12" y1="2" x2="12" y2="15"/>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="crypto-chart-container" role="img" aria-label="${escapeHtml(symbol.toUpperCase())} price chart, drag to inspect points">
                    <div class="crypto-chart-canvas"></div>
                    <div class="crypto-chart-loading" aria-busy="true" aria-label="Loading chart data">
                        <div class="chart-spinner"></div>
                    </div>
                </div>
                <div class="crypto-chart-footer">
                    <div class="crypto-chart-stats">
                        <div class="stat-item tier-primary">
                            <span class="stat-label">Market Cap</span>
                            <span class="stat-value market-cap shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-primary">
                            <span class="stat-label">24h Volume</span>
                            <span class="stat-value volume shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-secondary">
                            <span class="stat-label">24h High</span>
                            <span class="stat-value high-24h shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-secondary">
                            <span class="stat-label">24h Low</span>
                            <span class="stat-value low-24h shimmer-loading">&nbsp;</span>
                        </div>
                    </div>
                    <div class="crypto-chart-stats stats-row-2">
                        <div class="stat-item tier-tertiary">
                            <span class="stat-label">All-Time High</span>
                            <span class="stat-value ath shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-tertiary">
                            <span class="stat-label">ATH Change</span>
                            <span class="stat-value ath-change shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-tertiary">
                            <span class="stat-label">Circulating Supply</span>
                            <span class="stat-value circulating-supply shimmer-loading">&nbsp;</span>
                        </div>
                        <div class="stat-item tier-tertiary">
                            <span class="stat-label">Max Supply</span>
                            <span class="stat-value max-supply shimmer-loading">&nbsp;</span>
                        </div>
                    </div>
                </div>
                <div class="news-feed-header">
                    <span class="news-live-badge"><span class="live-dot"></span>LIVE</span>
                    <span class="news-feed-title">News</span>
                    <span class="news-toggle-link" data-tooltip="Toggle news feed" onclick="window.CryptoChart?.toggleNews(this)">Show News</span>
                </div>
                <div class="crypto-chart-news collapsed">
                    <div class="news-list">
                        <div class="news-list-content">
                            <span class="news-ticker-item shimmer-loading">&nbsp;</span>
                            <span class="news-ticker-item shimmer-loading">&nbsp;</span>
                        </div>
                    </div>
                </div>
                ${assetType === 'crypto' ? `
                <div class="whale-ticker-bar">
                    <span class="whale-ticker-label">🐋</span>
                    <div class="whale-ticker">
                        <div class="whale-ticker-content">
                            <span class="whale-item shimmer-loading">Loading whale activity...</span>
                        </div>
                    </div>
                </div>
                ` : ''}
            </div>
        `;
    }

    /**
     * Initialize and render a chart (supports both crypto and stocks)
     */
    async function renderChart(container, asset, options = {}) {
        // Validate inputs
        if (!container) {
            console.error('[ChartModule] No container provided');
            return null;
        }
        if (!asset) {
            console.error('[ChartModule] No asset provided');
            return null;
        }

        // Check if Lightweight Charts is available
        if (typeof LightweightCharts === 'undefined') {
            console.error('[ChartModule] LightweightCharts library not loaded');
            container.innerHTML = '<div class="crypto-chart-error">Chart library not available</div>';
            return null;
        }

        // Fetch config from API (ensures logos and valid cryptos are loaded)
        await fetchConfig();

        const chartId = `crypto-chart-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const currency = options.currency || 'usd';
        const initialRange = options.range || 'MAX';
        const assetType = options.assetType || 'crypto';

        // Insert chart card HTML
        container.innerHTML = createChartCardHTML(chartId, asset, assetType);

        const cardElement = document.getElementById(chartId);
        const chartContainer = cardElement.querySelector('.crypto-chart-canvas');
        const loadingElement = cardElement.querySelector('.crypto-chart-loading');

        // Initialize Lightweight Chart
        const chart = LightweightCharts.createChart(chartContainer, {
            ...getDefaultChartOptions(),
            width: chartContainer.clientWidth,
            height: 280,
        });

        // Create area series with gradient.
        // Lightweight Charts v5 API: addSeries(SeriesType, options) replaces
        // the v4 addAreaSeries(options) shorthand. Using v5 unlocks the
        // plugins API and ~30% bundle reduction.
        const areaSeries = chart.addSeries(LightweightCharts.AreaSeries, {
            lineColor: '#22c55e',
            topColor: 'rgba(34, 197, 94, 0.18)',
            bottomColor: 'rgba(34, 197, 94, 0.0)',
            lineWidth: 2,
            priceFormat: {
                type: 'price',
                precision: 2,
                minMove: 0.01,
            },
        });

        // Handle window resize. Stored on chartInstances so destroyChart
        // can disconnect it — without this, observers leak each time a
        // chart is destroyed and the container removed from DOM.
        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                const { width } = entry.contentRect;
                chart.applyOptions({ width });
            }
        });
        resizeObserver.observe(chartContainer);

        // Store chart instance (resizeObserver included for cleanup)
        chartInstances.set(chartId, {
            chart,
            series: areaSeries,
            asset,
            assetType,
            currency,
            currentRange: initialRange,
            resizeObserver
        });

        // Setup range button handlers with rate limiting
        const rangeButtons = cardElement.querySelectorAll('.range-btn');
        rangeButtons.forEach(btn => {
            btn.addEventListener('click', async () => {
                // Rate limiting - prevent rapid clicks
                const now = Date.now();
                const lastTime = lastRequestTime.get(chartId) || 0;
                if (now - lastTime < MIN_REQUEST_INTERVAL) {
                    console.log('[Chart] Rate limited - please wait');
                    return;
                }
                lastRequestTime.set(chartId, now);

                const range = btn.dataset.range;

                // Wrap in View Transitions API for a smooth cross-fade
                // when switching range. Only Chrome/Edge/Safari 18+ support
                // it natively; falls back to instant switch on other
                // browsers via the typeof guard. The animation makes
                // 1D → 1W → 1M feel like a navigation, not a flash.
                const updateLogic = async () => {
                    rangeButtons.forEach(b => {
                        b.classList.remove('active');
                        b.setAttribute('aria-pressed', 'false');
                    });
                    btn.classList.add('active');
                    btn.setAttribute('aria-pressed', 'true');
                    await updateChartData(chartId, range);
                };
                if (typeof document.startViewTransition === 'function') {
                    document.startViewTransition(updateLogic);
                } else {
                    await updateLogic();
                }
            });
        });

        // Setup crosshair tooltip
        chart.subscribeCrosshairMove(param => {
            const priceElement = cardElement.querySelector('.price-value');
            const instance = chartInstances.get(chartId);

            if (!param.time || !param.seriesData.get(areaSeries)) {
                // Reset to current price when not hovering
                if (instance.currentPrice) {
                    priceElement.textContent = formatPrice(instance.currentPrice, currency);
                }
                return;
            }

            const price = param.seriesData.get(areaSeries).value;
            priceElement.textContent = formatPrice(price, currency);
        });

        // Lazy-load: defer chart-data + stats + news fetches until the
        // chart actually scrolls into view. Saves API quota and shaves
        // page-load time when the chat history has multiple charts the
        // user hasn't scrolled to yet. Threshold 0.1 = trigger when 10%
        // visible; rootMargin '100px' starts loading slightly before
        // the chart appears so it feels instant.
        let dataLoaded = false;
        const loadDataIfNeeded = async () => {
            if (dataLoaded) return;
            dataLoaded = true;
            await loadChartData(chartId, initialRange);
            await loadPriceStats(chartId);
            loadNews(chartId);
        };

        if (typeof IntersectionObserver !== 'undefined') {
            const lazyObserver = new IntersectionObserver((entries) => {
                for (const entry of entries) {
                    if (entry.isIntersecting) {
                        loadDataIfNeeded();
                        lazyObserver.disconnect();
                    }
                }
            }, { rootMargin: '100px', threshold: 0.1 });
            lazyObserver.observe(cardElement);
        } else {
            // Fallback for browsers without IntersectionObserver.
            await loadDataIfNeeded();
        }

        return chartId;
    }

    /**
     * Load chart data for a specific range (supports both crypto and stocks)
     */
    async function loadChartData(chartId, range) {
        const instance = chartInstances.get(chartId);
        if (!instance) return;

        const cardElement = document.getElementById(chartId);
        const loadingElement = cardElement.querySelector('.crypto-chart-loading');
        const changeElement = cardElement.querySelector('.price-change');

        loadingElement.style.display = 'flex';

        // Cancel any in-flight chart fetch for this chart instance.
        // Without this, rapidly clicking range buttons (1D → 1W → 1M)
        // lets all three fetches resolve out-of-order; the LAST to
        // complete wins, not the LATEST clicked. AbortController makes
        // every range change cancel the prior one.
        if (instance.fetchAbortController) {
            instance.fetchAbortController.abort();
        }
        instance.fetchAbortController = new AbortController();
        const signal = instance.fetchAbortController.signal;

        try {
            let data;
            if (instance.assetType === 'stock') {
                // Fetch stock chart data
                data = await fetchStockChartData(instance.asset, range, signal);
            } else {
                // Fetch crypto chart data
                let days = TIME_RANGES[range]?.days || '1';
                // Calculate YTD days for crypto (CoinGecko doesn't support YTD directly)
                if (days === 'ytd') {
                    const now = new Date();
                    const startOfYear = new Date(now.getFullYear(), 0, 1);
                    days = Math.ceil((now - startOfYear) / (1000 * 60 * 60 * 24));
                }
                data = await fetchChartData(instance.asset, days, instance.currency, signal);
            }

            if (data.success && data.data.length > 0) {
                instance.series.setData(data.data);
                instance.chart.timeScale().fitContent();
                instance.currentPrice = data.current_price;

                // Update symbol display from API response
                if (data.symbol) {
                    const symbolNameEl = cardElement.querySelector('.symbol-name');
                    if (symbolNameEl) {
                        // Use proper ticker from API (e.g., "VET" not "VECHAIN")
                        symbolNameEl.textContent = data.symbol;
                    }

                    // Update symbol icon initials (e.g., "VE" -> "VET")
                    const iconFallback = cardElement.querySelector('.symbol-icon-fallback');
                    if (iconFallback) {
                        iconFallback.textContent = data.symbol.substring(0, 2);
                    }

                    // Also update the intro text above the chart
                    const introTextEl = cardElement.closest('.message-block, .ai-message')?.querySelector('.chart-intro-text');
                    if (introTextEl && instance.assetType === 'crypto') {
                        // Get display name from config or capitalize coin_id
                        const displayName = (configCache?.asset_names?.[instance.asset.toLowerCase()])
                            || instance.asset.charAt(0).toUpperCase() + instance.asset.slice(1);
                        introTextEl.textContent = `Here's the current ${displayName} price (${data.symbol}) as of the latest live market data:`;
                    }
                }

                // Update colors based on price direction
                const isPositive = data.is_positive;
                const lineColor = isPositive ? '#22c55e' : '#ef4444';
                const topColor = isPositive ? 'rgba(34, 197, 94, 0.18)' : 'rgba(239, 68, 68, 0.18)';
                const bottomColor = isPositive ? 'rgba(34, 197, 94, 0.0)' : 'rgba(239, 68, 68, 0.0)';

                instance.series.applyOptions({
                    lineColor,
                    topColor,
                    bottomColor
                });

                // Set trend for glow effect
                cardElement.setAttribute('data-trend', isPositive ? 'up' : 'down');

                // Update price display with animation
                const priceElement = cardElement.querySelector('.price-value');
                animatePrice(priceElement, data.current_price, instance.currency);

                // Update period change with timeframe label so the user
                // knows which range the percentage refers to.
                setLoadedContent(changeElement, formatChange(data.change_percent, range), `price-change ${isPositive ? 'positive' : 'negative'}`);

                // Update data source badge
                updateDataSourceBadge(cardElement, data.data_source || 'live', data.cache_age_seconds || 0);
            }
        } catch (error) {
            // AbortError = a newer range click cancelled this fetch.
            // Don't show an error; the newer fetch will paint the UI.
            if (error.name === 'AbortError') {
                return;
            }
            console.error('[Chart] Load error:', error);
            const priceElement = cardElement.querySelector('.price-value');
            priceElement.classList.remove('shimmer-loading');
            changeElement.classList.remove('shimmer-loading');
            // Show user-friendly error message
            if (error.message && error.message.includes('429')) {
                priceElement.textContent = 'Rate limited - try again';
            } else {
                priceElement.textContent = 'Error loading data';
            }
            changeElement.textContent = '--';
        } finally {
            loadingElement.style.display = 'none';
        }
    }

    /**
     * Format supply numbers with appropriate suffix
     */
    function formatSupply(value, symbol = '') {
        if (!value || value === 0) return '∞';
        const formatted = formatLargeNumber(value);
        return symbol ? `${formatted} ${symbol}` : formatted;
    }

    /**
     * Load price statistics (market cap, volume, 24h change) - supports both crypto and stocks
     */
    async function loadPriceStats(chartId) {
        const instance = chartInstances.get(chartId);
        if (!instance) return;

        const cardElement = document.getElementById(chartId);

        try {
            let data;
            let detailsData = null;

            if (instance.assetType === 'stock') {
                data = await fetchStockPriceData(instance.asset);
            } else {
                // For crypto, fetch both price and detailed data
                data = await fetchPriceData(instance.asset, instance.currency);
                try {
                    detailsData = await fetchCryptoDetails(instance.asset, instance.currency);
                } catch (detailsError) {
                    console.warn('[Chart] Could not fetch details:', detailsError);
                }
            }

            if (data.success) {
                const marketCapEl = cardElement.querySelector('.market-cap');
                const volumeEl = cardElement.querySelector('.volume');
                const high24hEl = cardElement.querySelector('.high-24h');
                const low24hEl = cardElement.querySelector('.low-24h');
                const athEl = cardElement.querySelector('.ath');
                const athChangeEl = cardElement.querySelector('.ath-change');
                const circulatingSupplyEl = cardElement.querySelector('.circulating-supply');
                const maxSupplyEl = cardElement.querySelector('.max-supply');

                if (instance.assetType === 'stock') {
                    // Stocks: show exchange and market state
                    setLoadedContent(marketCapEl, data.exchange || '--');
                    setLoadedContent(volumeEl, data.market_state || '--');

                    // Hide crypto-specific stat items for stocks
                    [high24hEl, low24hEl].forEach(el => {
                        if (el?.parentElement) el.parentElement.style.display = 'none';
                    });

                    // Hide the entire second stats row for stocks
                    const statsRow2 = cardElement.querySelector('.stats-row-2');
                    if (statsRow2) statsRow2.style.display = 'none';
                } else {
                    // Crypto: show market cap and volume
                    setLoadedContent(marketCapEl, data.market_cap ? '$' + formatLargeNumber(data.market_cap) : '--');
                    setLoadedContent(volumeEl, data.volume_24h ? '$' + formatLargeNumber(data.volume_24h) : '--');

                    // Update detailed stats if available
                    if (detailsData && detailsData.success) {
                        const symbol = detailsData.symbol || instance.asset.toUpperCase();

                        // 24h High/Low
                        setLoadedContent(high24hEl, detailsData.high_24h ? formatPrice(detailsData.high_24h) : '--');
                        setLoadedContent(low24hEl, detailsData.low_24h ? formatPrice(detailsData.low_24h) : '--');

                        // All-Time High
                        setLoadedContent(athEl, detailsData.ath ? formatPrice(detailsData.ath) : '--');

                        // ATH Change percentage
                        if (athChangeEl && detailsData.ath_change_percentage) {
                            const athChange = detailsData.ath_change_percentage;
                            const isPositive = athChange >= 0;
                            setLoadedContent(athChangeEl, formatChange(athChange), `stat-value ath-change ${isPositive ? 'positive' : 'negative'}`);
                        } else if (athChangeEl) {
                            setLoadedContent(athChangeEl, '--');
                        }

                        // Supply info
                        setLoadedContent(circulatingSupplyEl, formatSupply(detailsData.circulating_supply, symbol));
                        setLoadedContent(maxSupplyEl, formatSupply(detailsData.max_supply, symbol));

                        // Update logo from API response and cache it permanently
                        if (detailsData.image) {
                            // Cache logo URL to localStorage for future use
                            cacheLogoUrl(instance.asset, detailsData.image);

                            const symbolIcon = cardElement.querySelector('.symbol-icon');
                            if (symbolIcon) {
                                const existingImg = symbolIcon.querySelector('.symbol-logo');
                                const fallback = symbolIcon.querySelector('.symbol-icon-fallback');

                                if (!existingImg) {
                                    // Create new image element
                                    const img = document.createElement('img');
                                    img.className = 'symbol-logo';
                                    img.src = detailsData.image;
                                    img.alt = symbol;
                                    img.onerror = function() {
                                        this.style.display = 'none';
                                        if (fallback) fallback.style.display = 'flex';
                                    };
                                    img.onload = function() {
                                        this.classList.add('loaded');
                                        if (fallback) fallback.style.display = 'none';
                                    };
                                    symbolIcon.insertBefore(img, fallback);
                                } else {
                                    // Update existing image
                                    existingImg.src = detailsData.image;
                                    existingImg.classList.add('loaded');
                                }
                            }
                        }
                    } else {
                        // No details available, clear shimmer
                        [high24hEl, low24hEl, athEl, athChangeEl, circulatingSupplyEl, maxSupplyEl].forEach(el => {
                            if (el) {
                                el.classList.remove('shimmer-loading');
                                el.textContent = '--';
                            }
                        });
                    }
                }

                // 24h price change SKIPPED here (used to write to .price-change,
                // which was clobbering the range-aware label loadChartData
                // had already set with the correct timeframe). loadChartData
                // owns .price-change exclusively now.

                // Fetch and display Fear & Greed Index (crypto only)
                if (instance.assetType !== 'stock') {
                    fetchFearGreed().then(fgData => {
                        const fgBadge = cardElement.querySelector('.fear-greed-badge');
                        if (fgBadge && fgData && fgData.success) {
                            const fgDot = fgBadge.querySelector('.fg-dot');
                            const fgLabel = fgBadge.querySelector('.fg-label');
                            fgDot.style.background = fgData.color;
                            fgLabel.textContent = fgData.classification;
                            fgBadge.style.display = 'flex';
                            fgBadge.title = `Fear & Greed Index: ${fgData.value}/100`;
                        }
                    }).catch(() => {});
                }
            }
        } catch (error) {
            console.error('[Chart] Stats error:', error);
            // Remove shimmer on error
            const allStatEls = cardElement.querySelectorAll('.stat-value');
            allStatEls.forEach(el => {
                if (el) {
                    el.classList.remove('shimmer-loading');
                    el.textContent = '--';
                }
            });
        }
    }

    /**
     * Setup hover tooltips for news items (appended to body to avoid overflow clipping)
     */
    function setupNewsTooltips(container) {
        // Create or get global tooltip element
        let tooltip = document.getElementById('news-preview-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'news-preview-tooltip';
            tooltip.className = 'news-preview-tooltip';
            document.body.appendChild(tooltip);
        }

        const newsItems = container.querySelectorAll('[data-preview]');
        newsItems.forEach(item => {
            item.addEventListener('mouseenter', (e) => {
                const preview = item.getAttribute('data-preview');
                if (!preview) return;

                tooltip.textContent = preview;
                tooltip.classList.add('visible');

                // Position tooltip above the item
                const rect = item.getBoundingClientRect();
                const tooltipRect = tooltip.getBoundingClientRect();

                let left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
                // Keep tooltip within viewport
                left = Math.max(10, Math.min(left, window.innerWidth - tooltipRect.width - 10));

                tooltip.style.left = `${left}px`;
                tooltip.style.top = `${rect.top - tooltipRect.height - 10}px`;
            });

            item.addEventListener('mouseleave', () => {
                tooltip.classList.remove('visible');
            });
        });
    }

    /**
     * Fetch stock news from Finnhub API
     */
    async function fetchStockNews(symbol, limit = 5) {
        try {
            const response = await fetch(
                `/api/stock/news?symbol=${encodeURIComponent(symbol)}&limit=${limit}`,
                { credentials: 'same-origin' }
            );
            if (response.ok) {
                return await response.json();
            }
            return { success: false, articles: [] };
        } catch (error) {
            console.error('[StockNews] Fetch error:', error);
            return { success: false, articles: [] };
        }
    }

    /**
     * Load and display news + whale alerts for a crypto asset (ticker style)
     */
    async function loadNews(chartId) {
        const instance = chartInstances.get(chartId);
        const cardElement = document.getElementById(chartId);
        const newsSection = cardElement?.querySelector('.crypto-chart-news');

        if (!instance) {
            return;
        }

        const newsListContent = cardElement?.querySelector('.news-list-content');
        const whaleTickerContent = cardElement?.querySelector('.whale-ticker-content');

        if (!newsListContent) return;

        try {
            // Fetch news based on asset type, whale alerts only for crypto
            const isStock = instance.assetType === 'stock';
            let newsData, whaleData;

            if (isStock) {
                // Stock: fetch stock news only, no whale alerts
                newsData = await fetchStockNews(instance.asset, 5).catch(() => ({ success: false, articles: [] }));
                whaleData = { success: false, alerts: [] };
            } else {
                // Crypto: fetch news and whale alerts in parallel
                [newsData, whaleData] = await Promise.all([
                    fetchCryptoNews(instance.asset, 5).catch(() => ({ success: false, articles: [] })),
                    fetchWhaleAlerts('', 10).catch(() => ({ success: false, alerts: [] }))
                ]);
            }

            console.log(`[NewsTicker] ${isStock ? 'Stock' : 'Crypto'} News:`, newsData?.articles?.length || 0, 'articles, Whales:', whaleData?.alerts?.length || 0, 'alerts');

            // === NEWS SECTION (vertical list) ===
            if (newsData.success && newsData.articles && newsData.articles.length > 0) {
                const newsItems = newsData.articles.map(article => {
                    const timeAgo = article.published_on ? formatRelativeTime(article.published_on) : '';
                    const snippet = article.body ?
                        (article.body.length > 150 ? article.body.substring(0, 150) + '...' : article.body) : '';

                    return `
                        <a href="${escapeHtml(safeUrl(article.url))}" target="_blank" rel="noopener noreferrer" class="news-ticker-item" data-preview="${escapeHtml(snippet)}">
                            <span class="ticker-headline">${escapeHtml(article.title)}</span>
                            <div class="news-ticker-meta">
                                ${timeAgo ? `<span class="ticker-time">${escapeHtml(timeAgo)}</span>` : ''}
                                <span class="ticker-source">${escapeHtml(article.source)}</span>
                            </div>
                        </a>
                    `;
                }).join('');

                newsListContent.innerHTML = newsItems;
                setupNewsTooltips(newsListContent);
            } else {
                newsListContent.innerHTML = '<div class="news-empty">No recent news available</div>';
            }

            // === WHALE TICKER (bottom bar) ===
            if (whaleTickerContent) {
                if (whaleData.success && whaleData.alerts && whaleData.alerts.length > 0) {
                    const whaleItems = whaleData.alerts.map(alert => {
                        const timeAgo = alert.timestamp ? formatRelativeTime(alert.timestamp) : '';
                        const previewText = `${alert.amount_formatted} ${alert.symbol} (${alert.amount_usd_formatted}) transferred from ${alert.from} to ${alert.to}`;

                        return `
                            <span class="whale-item" data-type="${escapeHtml(alert.tx_type)}" data-preview="${escapeHtml(previewText)}">
                                <span class="whale-emoji">${escapeHtml(alert.emoji)}</span>
                                <span class="whale-amount">${escapeHtml(alert.amount_formatted)} ${escapeHtml(alert.symbol)}</span>
                                <span class="whale-usd">${escapeHtml(alert.amount_usd_formatted)}</span>
                                <span class="whale-flow">${escapeHtml(alert.from)} → ${escapeHtml(alert.to)}</span>
                                ${timeAgo ? `<span class="whale-time">${escapeHtml(timeAgo)}</span>` : ''}
                            </span>
                        `;
                    }).join('<span class="whale-separator">•</span>');

                    // Duplicate content for seamless infinite scroll
                    // We duplicate the items so when the first set scrolls out, the second set appears
                    const fullContent = whaleItems + '<span class="whale-separator">•</span>';
                    // Re-enable scroll animation in case empty-state added the no-scroll guard.
                    whaleTickerContent.classList.remove('no-scroll');
                    whaleTickerContent.innerHTML = fullContent + fullContent;

                    // Setup tooltips for whale items
                    setupNewsTooltips(whaleTickerContent);
                } else {
                    // Show "no activity" message. Add 'no-scroll' class
                    // so the whale-scroll animation doesn't leave the
                    // ticker blank for half each cycle (the animation
                    // moves -50% which only works for duplicated content).
                    whaleTickerContent.classList.add('no-scroll');
                    whaleTickerContent.innerHTML = '<span class="whale-item whale-empty">No whale activity in last 10 min</span>';
                }
            }
        } catch (error) {
            console.error('[Chart] News/Whale error:', error);
            if (newsListContent) {
                newsListContent.innerHTML = '<span class="news-ticker-item">Unable to load news</span>';
            }
            if (whaleTickerContent) {
                whaleTickerContent.innerHTML = '<span class="whale-item whale-empty">🐋 Whale data unavailable</span>';
            }
        }
    }

    /**
     * Update chart data for a new range
     */
    async function updateChartData(chartId, range) {
        const instance = chartInstances.get(chartId);
        if (!instance) return;

        instance.currentRange = range;
        await loadChartData(chartId, range);
    }

    /**
     * Destroy a chart instance
     */
    function destroyChart(chartId) {
        const instance = chartInstances.get(chartId);
        if (instance) {
            // Cancel any in-flight fetches so they don't try to paint
            // a destroyed chart's DOM.
            if (instance.fetchAbortController) {
                instance.fetchAbortController.abort();
            }
            // Disconnect ResizeObserver before removing chart so the
            // observer doesn't keep a reference to a detached DOM node.
            if (instance.resizeObserver) {
                instance.resizeObserver.disconnect();
            }
            instance.chart.remove();
            chartInstances.delete(chartId);
        }
    }

    /**
     * Toggle news section visibility
     */
    function toggleNews(linkElement) {
        const header = linkElement.closest('.news-feed-header');
        const newsSection = header?.nextElementSibling;
        if (newsSection && newsSection.classList.contains('crypto-chart-news')) {
            const isCollapsed = newsSection.classList.toggle('collapsed');
            linkElement.textContent = isCollapsed ? 'Show News' : 'Hide News';
        }
    }

    /**
     * Share chart as PNG image
     */
    async function shareChart(chartId) {
        const cardElement = document.getElementById(chartId);
        if (!cardElement) return;

        const instance = chartInstances.get(chartId);
        if (!instance) return;

        // Show loading state on button
        const shareBtn = cardElement.querySelector('.chart-share-btn');
        if (shareBtn) {
            shareBtn.classList.add('sharing');
            shareBtn.disabled = true;
        }

        try {
            // Use html2canvas if available, otherwise use native approach
            if (typeof html2canvas !== 'undefined') {
                const canvas = await html2canvas(cardElement, {
                    backgroundColor: '#1a1a1f',
                    scale: 2,
                    logging: false,
                    useCORS: true,
                    allowTaint: true
                });
                downloadCanvas(canvas, instance.asset);
            } else {
                // Fallback: capture just the chart canvas
                const chartCanvas = cardElement.querySelector('.crypto-chart-canvas canvas');
                if (chartCanvas) {
                    // Create a new canvas with header info
                    const exportCanvas = document.createElement('canvas');
                    const ctx = exportCanvas.getContext('2d');
                    const padding = 40;
                    const headerHeight = 80;

                    exportCanvas.width = chartCanvas.width + padding * 2;
                    exportCanvas.height = chartCanvas.height + headerHeight + padding * 2;

                    // Dark background
                    ctx.fillStyle = '#1a1a1f';
                    ctx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);

                    // Get price info
                    const priceEl = cardElement.querySelector('.price-value');
                    const changeEl = cardElement.querySelector('.price-change');
                    const symbolEl = cardElement.querySelector('.symbol-name');

                    const symbol = symbolEl?.textContent || instance.asset.toUpperCase();
                    const price = priceEl?.textContent || '';
                    const change = changeEl?.textContent || '';
                    const isPositive = changeEl?.classList.contains('positive');

                    // Draw header
                    ctx.fillStyle = '#ffffff';
                    ctx.font = 'bold 24px system-ui, -apple-system, sans-serif';
                    ctx.fillText(symbol, padding, padding + 30);

                    ctx.font = 'bold 20px system-ui, -apple-system, sans-serif';
                    ctx.fillText(price, padding, padding + 60);

                    ctx.fillStyle = isPositive ? '#4ade80' : '#f87171';
                    ctx.font = '16px system-ui, -apple-system, sans-serif';
                    ctx.fillText(change, padding + ctx.measureText(price).width + 15, padding + 60);

                    // Draw chart
                    ctx.drawImage(chartCanvas, padding, headerHeight + padding);

                    // Add watermark
                    ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
                    ctx.font = '12px system-ui, -apple-system, sans-serif';
                    ctx.fillText('xelasphere.io', exportCanvas.width - padding - 80, exportCanvas.height - 15);

                    downloadCanvas(exportCanvas, instance.asset);
                }
            }
        } catch (error) {
            console.error('[Chart] Share error:', error);
            alert('Failed to export chart. Please try again.');
        } finally {
            if (shareBtn) {
                shareBtn.classList.remove('sharing');
                shareBtn.disabled = false;
            }
        }
    }

    /**
     * Download canvas as PNG
     */
    function downloadCanvas(canvas, asset) {
        const link = document.createElement('a');
        const timestamp = new Date().toISOString().split('T')[0];
        link.download = `${asset.toUpperCase()}-chart-${timestamp}.png`;
        link.href = canvas.toDataURL('image/png');
        link.click();
    }

    /**
     * Calculate RSI (Relative Strength Index)
     */
    function calculateRSI(prices, period = 14) {
        // Wilder's smoothing (the canonical RSI). Seed with simple
        // average of the first 14-period gains/losses, then apply
        // exponential-style smoothing for every subsequent bar:
        //   avgGain = (prevAvgGain * (period - 1) + currentGain) / period
        // Was previously a simple average over the trailing 14 bars,
        // which under-reports RSI extremes on trending data and produced
        // wrong overbought/oversold signals on parabolic moves.
        if (prices.length < period + 1) return 50;

        // Initial averages over the first `period` price changes.
        let avgGain = 0, avgLoss = 0;
        for (let i = 1; i <= period; i++) {
            const change = prices[i] - prices[i - 1];
            if (change > 0) avgGain += change;
            else avgLoss -= change;
        }
        avgGain /= period;
        avgLoss /= period;

        // Wilder smoothing for all subsequent bars.
        for (let i = period + 1; i < prices.length; i++) {
            const change = prices[i] - prices[i - 1];
            const gain = change > 0 ? change : 0;
            const loss = change < 0 ? -change : 0;
            avgGain = (avgGain * (period - 1) + gain) / period;
            avgLoss = (avgLoss * (period - 1) + loss) / period;
        }

        if (avgLoss === 0) return 100;
        const rs = avgGain / avgLoss;
        return 100 - (100 / (1 + rs));
    }

    /**
     * Calculate Simple Moving Average
     */
    function calculateSMA(prices, period) {
        if (prices.length < period) return prices[prices.length - 1];
        const slice = prices.slice(-period);
        return slice.reduce((a, b) => a + b, 0) / period;
    }

    /**
     * Calculate MACD
     */
    function calculateMACD(prices) {
        const ema12 = calculateEMA(prices, 12);
        const ema26 = calculateEMA(prices, 26);
        return ema12 - ema26;
    }

    /**
     * Calculate EMA (Exponential Moving Average)
     */
    function calculateEMA(prices, period) {
        if (prices.length < period) return prices[prices.length - 1];
        const k = 2 / (period + 1);
        let ema = prices.slice(0, period).reduce((a, b) => a + b, 0) / period;
        for (let i = period; i < prices.length; i++) {
            ema = prices[i] * k + ema * (1 - k);
        }
        return ema;
    }

    /**
     * Generate price prediction based on technical indicators
     */
    function generatePrediction(prices, currentPrice, fgData = null) {
        const indicators = [];
        let bullishSignals = 0;
        let bearishSignals = 0;

        // RSI
        const rsi = calculateRSI(prices);
        let rsiSignal = 'Neutral';
        let rsiColor = '#eab308';
        if (rsi < 30) {
            rsiSignal = 'Oversold';
            rsiColor = '#22c55e';
            bullishSignals += 2;
        } else if (rsi > 70) {
            rsiSignal = 'Overbought';
            rsiColor = '#ef4444';
            bearishSignals += 2;
        } else if (rsi < 45) {
            bullishSignals += 1;
        } else if (rsi > 55) {
            bearishSignals += 1;
        }
        indicators.push({ name: 'RSI', value: rsi.toFixed(0), signal: rsiSignal, color: rsiColor });

        // Moving Averages
        const sma20 = calculateSMA(prices, 20);
        const sma50 = calculateSMA(prices, 50);
        const maSignal = currentPrice > sma20 ? 'Above MA' : 'Below MA';
        const maColor = currentPrice > sma20 ? '#22c55e' : '#ef4444';
        if (currentPrice > sma20) bullishSignals += 1;
        else bearishSignals += 1;
        if (currentPrice > sma50) bullishSignals += 1;
        else bearishSignals += 1;
        indicators.push({ name: 'MA (20)', value: formatPrice(sma20), signal: maSignal, color: maColor });

        // MACD
        const macd = calculateMACD(prices);
        const macdSignal = macd > 0 ? 'Bullish' : 'Bearish';
        const macdColor = macd > 0 ? '#22c55e' : '#ef4444';
        if (macd > 0) bullishSignals += 1;
        else bearishSignals += 1;
        indicators.push({ name: 'MACD', value: macd.toFixed(2), signal: macdSignal, color: macdColor });

        // Price momentum (7-day change)
        // Guard against weekAgo == 0 (would yield Infinity / NaN). For
        // legitimate crypto prices this is rare but possible for very
        // new or de-listed coins.
        if (prices.length >= 7 && prices[prices.length - 7] > 0) {
            const weekAgo = prices[prices.length - 7];
            const momentum = ((currentPrice - weekAgo) / weekAgo) * 100;
            const momSignal = momentum > 0 ? 'Positive' : 'Negative';
            const momColor = momentum > 0 ? '#22c55e' : '#ef4444';
            if (momentum > 2) bullishSignals += 1;
            else if (momentum < -2) bearishSignals += 1;
            indicators.push({ name: 'Momentum', value: (momentum > 0 ? '+' : '') + momentum.toFixed(1) + '%', signal: momSignal, color: momColor });
        }

        // Fear & Greed Index (market sentiment - contrarian indicator)
        if (fgData && fgData.success) {
            const fgValue = fgData.value;
            let fgSignal, fgColor;

            // Contrarian logic: extreme fear = buy, extreme greed = sell
            if (fgValue <= 25) {
                fgSignal = 'Buy Signal';
                fgColor = '#22c55e';
                bullishSignals += 2;
            } else if (fgValue <= 40) {
                fgSignal = 'Accumulate';
                fgColor = '#4ade80';
                bullishSignals += 1;
            } else if (fgValue >= 75) {
                fgSignal = 'Sell Signal';
                fgColor = '#ef4444';
                bearishSignals += 2;
            } else if (fgValue >= 60) {
                fgSignal = 'Caution';
                fgColor = '#f97316';
                bearishSignals += 1;
            } else {
                fgSignal = 'Neutral';
                fgColor = '#eab308';
            }

            indicators.push({ name: 'Market F&G', value: fgValue.toString(), signal: fgSignal, color: fgColor });
        }

        // Calculate overall signal
        const totalSignals = bullishSignals + bearishSignals;
        const confidence = totalSignals > 0 ? Math.round((Math.max(bullishSignals, bearishSignals) / totalSignals) * 100) : 50;

        let outlook, outlookColor, outlookEmoji;
        if (bullishSignals > bearishSignals + 1) {
            outlook = 'BULLISH';
            outlookColor = '#22c55e';
            outlookEmoji = '🟢';
        } else if (bearishSignals > bullishSignals + 1) {
            outlook = 'BEARISH';
            outlookColor = '#ef4444';
            outlookEmoji = '🔴';
        } else {
            outlook = 'NEUTRAL';
            outlookColor = '#eab308';
            outlookEmoji = '🟡';
        }

        return { outlook, outlookColor, outlookEmoji, confidence, indicators };
    }

    /**
     * Show price prediction popup
     */
    async function showPrediction(chartId) {
        const cardElement = document.getElementById(chartId);
        const instance = chartInstances.get(chartId);
        if (!cardElement || !instance) return;

        // Check if popup already exists, toggle it
        const existingPopup = cardElement.querySelector('.prediction-popup');
        if (existingPopup) {
            existingPopup.remove();
            return;
        }

        const predictBtn = cardElement.querySelector('.chart-predict-btn');
        if (predictBtn) {
            predictBtn.classList.add('loading');
        }

        try {
            // Fetch 30 days of data and Fear & Greed in parallel
            const [chartData, fgData] = await Promise.all([
                fetchChartData(instance.asset, '30', instance.currency),
                fetchFearGreed().catch(() => null)
            ]);
            console.log('[Prediction] Chart data:', chartData);

            // Handle different response formats
            let prices = [];
            if (chartData.prices && Array.isArray(chartData.prices)) {
                // Format: [[timestamp, price], ...]
                prices = chartData.prices.map(p => {
                    const val = Array.isArray(p) ? p[1] : (p.value || p.price || p.close || p);
                    return parseFloat(val);
                }).filter(p => !isNaN(p) && p > 0);
            } else if (chartData.data && Array.isArray(chartData.data)) {
                // Alternate format
                prices = chartData.data.map(p => {
                    const val = p.price || p.close || p.value || (Array.isArray(p) ? p[1] : p);
                    return parseFloat(val);
                }).filter(p => !isNaN(p) && p > 0);
            }

            console.log('[Prediction] Parsed prices:', prices.length, 'samples, last:', prices[prices.length - 1]);

            if (prices.length < 14) {
                console.error('[Prediction] Not enough price points:', prices.length);
                throw new Error('Not enough data for prediction');
            }

            const currentPrice = prices[prices.length - 1];
            const prediction = generatePrediction(prices, currentPrice, fgData);

            // Create popup with meter visualization
            const popup = document.createElement('div');
            popup.className = 'prediction-popup';

            // Calculate meter position (0-100, 50 is neutral)
            const meterPos = prediction.outlook === 'BULLISH' ? 50 + (prediction.confidence / 2) :
                            prediction.outlook === 'BEARISH' ? 50 - (prediction.confidence / 2) : 50;
            const meterWidth = Math.abs(meterPos - 50);
            const meterClass = prediction.outlook === 'BULLISH' ? 'bullish' :
                              prediction.outlook === 'BEARISH' ? 'bearish' : '';

            popup.innerHTML = `
                <div class="prediction-header">
                    <span class="prediction-title">24h Outlook</span>
                    <button class="prediction-close" onclick="this.closest('.prediction-popup').remove()">×</button>
                </div>
                <div class="prediction-meter">
                    <span style="font-size:9px;color:rgba(255,255,255,0.3)">SELL</span>
                    <div class="meter-bar">
                        <div class="meter-center"></div>
                        <div class="meter-fill ${meterClass}" style="width: ${meterWidth}%"></div>
                    </div>
                    <span style="font-size:9px;color:rgba(255,255,255,0.3)">BUY</span>
                    <div class="prediction-signal">
                        <span class="signal-label" style="color: ${prediction.outlookColor}">${prediction.outlook}</span>
                        <span class="prediction-confidence">${prediction.confidence}%</span>
                    </div>
                </div>
                <div class="prediction-indicators">
                    ${prediction.indicators.map(ind => `<div class="prediction-indicator">
                        <span class="ind-name">${ind.name}</span>
                        <span class="ind-value">${ind.value}</span>
                        <span class="ind-signal" style="color: ${ind.color}">${ind.signal}</span>
                    </div>`).join('')}
                </div>
                <div class="prediction-disclaimer">Technical analysis · Not financial advice</div>
            `;

            // Insert after header
            const header = cardElement.querySelector('.crypto-chart-header');
            header.after(popup);

        } catch (error) {
            console.error('[Prediction] Error:', error);
            alert('Could not generate prediction. Please try again.');
        } finally {
            if (predictBtn) {
                predictBtn.classList.remove('loading');
            }
        }
    }

    // Public API
    return {
        render: renderChart,
        destroy: destroyChart,
        fetchPrice: fetchPriceData,
        fetchChart: fetchChartData,
        fetchConfig,
        formatPrice,
        formatChange,
        toggleNews,
        shareChart,
        showPrediction,
        // Get current config (for debugging)
        getConfig: () => configCache
    };

})();

// Export for global access
window.CryptoChartModule = CryptoChartModule;
window.CryptoChart = CryptoChartModule;

// Pre-fetch config on module load
CryptoChartModule.fetchConfig().then(config => {
    if (config) {
        console.log('[CryptoChartModule] Loaded with API config');
        // Share asset config with codeWindow.js for consistent symbol mapping
        if (window.loadAssetConfig) {
            window.loadAssetConfig(config);
        }
    } else {
        console.log('[CryptoChartModule] Loaded with fallback config');
    }
});
