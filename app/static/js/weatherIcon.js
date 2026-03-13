/**
 * Weather Icon Module with PNG Clouds + CSS Animation
 * Uses real cloud images for realistic look
 * Uses Open-Meteo API (free, no API key required)
 */

(function() {
    'use strict';

    // Weather code to icon mapping
    // Based on WMO Weather interpretation codes
    const weatherIcons = {
        // Clear sky
        0: { day: 'clear-day', night: 'clear-night', label: 'Clear' },
        // Mainly clear
        1: { day: 'partly-cloudy-day', night: 'partly-cloudy-night', label: 'Mainly clear' },
        // Partly cloudy
        2: { day: 'partly-cloudy-day', night: 'partly-cloudy-night', label: 'Partly cloudy' },
        // Overcast
        3: { day: 'cloudy', night: 'cloudy', label: 'Overcast' },
        // Fog
        45: { day: 'fog', night: 'fog', label: 'Fog' },
        48: { day: 'fog', night: 'fog', label: 'Freezing fog' },
        // Drizzle
        51: { day: 'drizzle', night: 'drizzle', label: 'Light drizzle' },
        53: { day: 'drizzle', night: 'drizzle', label: 'Drizzle' },
        55: { day: 'drizzle', night: 'drizzle', label: 'Heavy drizzle' },
        56: { day: 'drizzle', night: 'drizzle', label: 'Freezing drizzle' },
        57: { day: 'drizzle', night: 'drizzle', label: 'Heavy freezing drizzle' },
        // Rain
        61: { day: 'rain', night: 'rain', label: 'Light rain' },
        63: { day: 'rain', night: 'rain', label: 'Rain' },
        65: { day: 'rain', night: 'rain', label: 'Heavy rain' },
        66: { day: 'rain', night: 'rain', label: 'Freezing rain' },
        67: { day: 'rain', night: 'rain', label: 'Heavy freezing rain' },
        // Snow
        71: { day: 'snow', night: 'snow', label: 'Light snow' },
        73: { day: 'snow', night: 'snow', label: 'Snow' },
        75: { day: 'snow', night: 'snow', label: 'Heavy snow' },
        77: { day: 'snow', night: 'snow', label: 'Snow grains' },
        // Rain showers
        80: { day: 'rain', night: 'rain', label: 'Light showers' },
        81: { day: 'rain', night: 'rain', label: 'Showers' },
        82: { day: 'rain', night: 'rain', label: 'Heavy showers' },
        // Snow showers
        85: { day: 'snow', night: 'snow', label: 'Light snow showers' },
        86: { day: 'snow', night: 'snow', label: 'Heavy snow showers' },
        // Thunderstorm
        95: { day: 'thunderstorm', night: 'thunderstorm', label: 'Thunderstorm' },
        96: { day: 'thunderstorm', night: 'thunderstorm', label: 'Thunderstorm with hail' },
        99: { day: 'thunderstorm', night: 'thunderstorm', label: 'Thunderstorm with heavy hail' }
    };

    // PNG cloud path
    const cloudPNG = '/static/img/clouds/50.png';

    // Icons that use PNG clouds
    const pngCloudTypes = ['cloudy', 'rain', 'drizzle', 'snow', 'thunderstorm', 'partly-cloudy-day', 'partly-cloudy-night'];

    // Local Lottie files (for sun, moon, fog)
    const localLottieFiles = {
        'clear-day': '/static/lottie/clear-day.json',
        'clear-night': '/static/lottie/clear-night.json',
        'fog': '/static/lottie/fog.json'
    };

    // SVG fallback icons
    const iconSVGs = {
        'clear-day': `<svg viewBox="0 0 24 24" fill="currentColor" width="28" height="28">
            <circle cx="12" cy="12" r="5"/>
            <line x1="12" y1="1" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="12" y1="21" x2="12" y2="23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="1" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="21" y1="12" x2="23" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>`,

        'clear-night': `<svg viewBox="0 0 24 24" fill="currentColor" width="28" height="28">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>`,

        'fog': `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" width="28" height="28" stroke-width="2" stroke-linecap="round">
            <line x1="3" y1="8" x2="21" y2="8"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
            <line x1="3" y1="16" x2="21" y2="16"/>
            <line x1="7" y1="20" x2="17" y2="20"/>
        </svg>`
    };

    // Cache key for localStorage
    const CACHE_KEY = 'weatherData';
    const CACHE_DURATION = 30 * 60 * 1000; // 30 minutes

    // Track loaded Lottie animations for cleanup
    let activeAnimations = [];

    /**
     * Check if it's daytime based on hour
     */
    function isDaytime() {
        const hour = new Date().getHours();
        return hour >= 6 && hour < 18;
    }

    /**
     * Check if Lottie library is loaded
     */
    function isLottieAvailable() {
        return typeof lottie !== 'undefined';
    }

    /**
     * Get cached weather data if still valid
     */
    function getCachedWeather() {
        try {
            const cached = localStorage.getItem(CACHE_KEY);
            if (!cached) return null;

            const data = JSON.parse(cached);
            if (Date.now() - data.timestamp < CACHE_DURATION) {
                return data;
            }
        } catch (e) {}
        return null;
    }

    /**
     * Save weather data to cache
     */
    function cacheWeather(data) {
        try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({
                ...data,
                timestamp: Date.now()
            }));
        } catch (e) {}
    }

    /**
     * Try to get browser geolocation
     */
    function getBrowserLocation() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject(new Error('Geolocation not supported'));
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (position) => {
                    resolve({
                        lat: position.coords.latitude,
                        lon: position.coords.longitude
                    });
                },
                (error) => reject(error),
                { timeout: 5000, enableHighAccuracy: false }
            );
        });
    }

    /**
     * Fetch weather with coordinates
     */
    async function fetchWeatherWithCoords(lat, lon) {
        const response = await fetch(`/weather?lat=${lat}&lon=${lon}`);
        if (!response.ok) throw new Error('Weather API request failed');
        return response.json();
    }

    /**
     * Fetch weather from server endpoint
     */
    async function fetchWeatherFromServer() {
        console.log('[Weather] Fetching from server using IP-based location...');

        // Use IP-based location (no browser prompt)
        const response = await fetch('/weather');
        if (!response.ok) throw new Error('Weather API request failed');
        return response.json();
    }

    /**
     * Get weather icon type based on weather code
     */
    function getWeatherIconType(weatherCode) {
        const iconData = weatherIcons[weatherCode];
        if (!iconData) {
            return isDaytime() ? 'clear-day' : 'clear-night';
        }
        return isDaytime() ? iconData.day : iconData.night;
    }

    // Lottie effect files
    const lottieEffects = {
        'rain': '/static/lottie/rain-effect.json',
        'drizzle': '/static/lottie/drizzle-effect.json',
        'snow': '/static/lottie/snow-effect.json',
        'thunderstorm': '/static/lottie/lightning-effect.json',
        'partly-cloudy-day': '/static/lottie/sun-peek.json'
    };

    /**
     * Create Lottie effect overlay
     */
    function createLottieEffect(effectType) {
        const effectContainer = document.createElement('div');
        effectContainer.className = 'weather-effect-overlay';
        effectContainer.style.position = 'absolute';
        effectContainer.style.top = '0';
        effectContainer.style.left = '0';
        effectContainer.style.width = '100%';
        effectContainer.style.height = '100%';
        effectContainer.style.pointerEvents = 'none';
        effectContainer.style.zIndex = '2';

        if (isLottieAvailable() && lottieEffects[effectType]) {
            try {
                const anim = lottie.loadAnimation({
                    container: effectContainer,
                    renderer: 'svg',
                    loop: true,
                    autoplay: true,
                    path: lottieEffects[effectType]
                });
                activeAnimations.push(anim);
            } catch (e) {
                console.log('[Weather] Lottie effect error:', e.message);
            }
        }

        return effectContainer;
    }

    /**
     * Create PNG cloud icon with Lottie effect overlay
     */
    function createPNGCloudIcon(iconType, weatherLabel) {
        const container = document.createElement('span');
        container.className = `weather-icon weather-png weather-${iconType}`;
        container.setAttribute('title', weatherLabel || '');
        container.style.width = '32px';
        container.style.height = '32px';
        container.style.display = 'inline-block';
        container.style.position = 'relative';
        container.style.overflow = 'visible';

        // Cloud image
        const img = document.createElement('img');
        img.src = cloudPNG;
        img.alt = weatherLabel || 'Weather';
        img.className = 'weather-cloud-img';
        img.style.position = 'absolute';
        img.style.top = '0';
        img.style.left = '0';
        img.style.width = '100%';
        img.style.height = '65%';
        img.style.objectFit = 'contain';
        img.style.zIndex = '1';

        // Apply CSS filter based on weather type
        switch(iconType) {
            case 'rain':
            case 'drizzle':
                img.style.filter = 'brightness(0.75) saturate(0.8)';
                break;
            case 'snow':
                img.style.filter = 'brightness(0.9) saturate(0.85)';
                break;
            case 'thunderstorm':
                img.style.filter = 'brightness(0.5) saturate(0.6)';
                break;
            default:
                // cloudy, partly-cloudy - use original cloud
                break;
        }

        container.appendChild(img);

        // Add Lottie effect overlay for weather types that need it
        if (['rain', 'drizzle', 'snow', 'thunderstorm', 'partly-cloudy-day'].includes(iconType)) {
            const effect = createLottieEffect(iconType);
            container.appendChild(effect);

            // For thunderstorm, also add rain effect
            if (iconType === 'thunderstorm') {
                const rainEffect = createLottieEffect('rain');
                container.appendChild(rainEffect);
            }
        }

        return container;
    }

    /**
     * Create Lottie animated icon (for sun, moon, fog)
     */
    function createLottieIcon(iconType, weatherLabel) {
        const container = document.createElement('span');
        container.className = `weather-icon weather-${iconType} weather-lottie`;
        container.setAttribute('title', weatherLabel || '');
        container.style.width = '32px';
        container.style.height = '32px';
        container.style.display = 'inline-block';

        const lottiePath = localLottieFiles[iconType];

        if (!isLottieAvailable() || !lottiePath) {
            console.log('[Weather] Lottie not available, using SVG fallback');
            container.innerHTML = iconSVGs[iconType] || iconSVGs['clear-day'];
            return container;
        }

        try {
            const anim = lottie.loadAnimation({
                container: container,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                path: lottiePath
            });

            activeAnimations.push(anim);

            anim.addEventListener('data_failed', () => {
                console.log('[Weather] Lottie failed to load, using SVG fallback');
                container.innerHTML = iconSVGs[iconType] || iconSVGs['clear-day'];
            });

            anim.addEventListener('error', () => {
                console.log('[Weather] Lottie error, using SVG fallback');
                container.innerHTML = iconSVGs[iconType] || iconSVGs['clear-day'];
            });

        } catch (e) {
            console.log('[Weather] Lottie error:', e.message);
            container.innerHTML = iconSVGs[iconType] || iconSVGs['clear-day'];
        }

        return container;
    }

    /**
     * Create weather icon element
     */
    function createWeatherIcon(iconType, weatherLabel) {
        // Use PNG cloud for cloud-based weather
        if (pngCloudTypes.includes(iconType)) {
            return createPNGCloudIcon(iconType, weatherLabel);
        }
        // Use Lottie for sun, moon, fog
        return createLottieIcon(iconType, weatherLabel);
    }

    /**
     * Get weather and create icon
     */
    async function getWeatherIcon() {
        console.log('[Weather] Starting weather icon fetch...');

        // Check cache first
        const cached = getCachedWeather();
        if (cached) {
            console.log('[Weather] Using cached weather:', cached.label);
            const iconType = getWeatherIconType(cached.weatherCode);
            return createWeatherIcon(iconType, cached.label);
        }

        try {
            const weather = await fetchWeatherFromServer();

            if (weather.error) throw new Error(weather.error);

            const weatherCode = weather.weather_code;
            const iconData = weatherIcons[weatherCode] || { label: 'Weather' };
            console.log('[Weather] City:', weather.city, '- Code:', weatherCode, '- Label:', iconData.label);

            cacheWeather({
                weatherCode,
                label: iconData.label,
                temp: weather.temperature,
                city: weather.city
            });

            const iconType = getWeatherIconType(weatherCode);
            console.log('[Weather] Creating icon type:', iconType);
            return createWeatherIcon(iconType, iconData.label);

        } catch (error) {
            console.log('[Weather] Error:', error.message);
            return null;
        }
    }

    /**
     * Create fallback time-based icon (sun/moon)
     */
    function createTimeIcon() {
        const isDay = isDaytime();
        const iconType = isDay ? 'clear-day' : 'clear-night';
        return createWeatherIcon(iconType, isDay ? 'Day' : 'Night');
    }

    /**
     * Cleanup all active Lottie animations
     */
    function cleanup() {
        activeAnimations.forEach(anim => {
            try { anim.destroy(); } catch (e) {}
        });
        activeAnimations = [];
    }

    // Expose functions globally
    window.WeatherIcon = {
        getWeatherIcon,
        createTimeIcon,
        isDaytime,
        cleanup
    };

})();
