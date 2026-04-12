/**
 * Thinking Sounds — subtle synthetic clicks while AI is working.
 * Messenger-style typing indicator audio feedback.
 *
 * Usage:
 *   ThinkingSounds.start()  — begin clicking pattern
 *   ThinkingSounds.stop()   — fade out and stop
 *
 * Synthesizes audio with Web Audio API — no files needed.
 */
const ThinkingSounds = (function () {
    'use strict';

    let _ctx = null;
    let _gain = null;
    let _timer = null;
    let _active = false;

    // Base rhythm fragments — shuffled and combined at runtime so it never loops
    // Each fragment is a "typing burst" (2-4 fast clicks) + a pause
    const FRAGMENTS = [
        [110, 85, 340],           // quick double-tap, pause
        [100, 120, 75, 380],      // triple burst, longer pause
        [130, 90, 300],           // double, medium pause
        [95, 105, 80, 420],       // triple, long pause
        [140, 280],               // single tap, pause
        [105, 90, 110, 85, 450],  // quad burst, long rest
        [120, 350],               // single, pause
        [90, 100, 300],           // double, medium
        [115, 80, 95, 370],       // triple, pause
        [135, 75, 310],           // double, medium
        [100, 260],               // single, short pause
        [85, 110, 90, 400],       // triple, long rest
    ];
    const VOLUME = 0.07;

    function _ensureContext() {
        if (_ctx && _ctx.state !== 'closed') return true;
        try {
            _ctx = new (window.AudioContext || window.webkitAudioContext)();
            return true;
        } catch (e) {
            return false;
        }
    }

    // Pre-baked key press samples — generated once, reused every click
    let _keySamples = null;

    function _generateKeySamples() {
        // Create 5 variations of a realistic keyboard key press
        // Real key sound = sharp transient "thock" + brief plastic resonance + surface thud
        const rate = _ctx.sampleRate;
        const samples = [];

        for (let v = 0; v < 5; v++) {
            const duration = 0.025 + Math.random() * 0.015; // 25-40ms
            const size = Math.ceil(rate * duration);
            const buf = _ctx.createBuffer(1, size, rate);
            const data = buf.getChannelData(0);

            // Each variation has slightly different character
            const thockFreq = 3200 + v * 400 + Math.random() * 300;  // 3200-5200 Hz
            const thudFreq = 150 + Math.random() * 80;               // 150-230 Hz low thud
            const resonanceFreq = 1200 + v * 200 + Math.random() * 200;
            const thockDecay = 18 + Math.random() * 8;               // fast
            const thudDecay = 6 + Math.random() * 3;                 // slower
            const resonanceDecay = 10 + Math.random() * 5;

            for (let i = 0; i < size; i++) {
                const t = i / rate; // time in seconds

                // 1. Sharp initial transient — the key bottoming out ("thock")
                const thock = Math.sin(2 * Math.PI * thockFreq * t) * Math.exp(-t * thockDecay / 0.001);

                // 2. Low frequency thud — key hitting the plate/desk
                const thud = Math.sin(2 * Math.PI * thudFreq * t) * Math.exp(-t * thudDecay / 0.001) * 0.4;

                // 3. Plastic resonance — brief body of the key housing
                const res = Math.sin(2 * Math.PI * resonanceFreq * t) * Math.exp(-t * resonanceDecay / 0.001) * 0.3;

                // 4. Tiny noise burst — surface friction/rattle
                const noise = (Math.random() * 2 - 1) * Math.exp(-t * 25 / 0.001) * 0.15;

                data[i] = thock + thud + res + noise;
            }

            samples.push(buf);
        }
        return samples;
    }

    function _click() {
        if (!_ctx || _ctx.state === 'closed' || !_gain) return;
        if (_ctx.state === 'suspended') _ctx.resume();

        // Generate samples lazily on first click
        if (!_keySamples) _keySamples = _generateKeySamples();

        // Pick a random sample variation
        const buf = _keySamples[Math.floor(Math.random() * _keySamples.length)];

        const src = _ctx.createBufferSource();
        src.buffer = buf;

        // Slight pitch shift per click for natural variation (±8%)
        src.playbackRate.value = 0.92 + Math.random() * 0.16;

        // Highpass to remove any DC offset, lowpass to soften
        const hp = _ctx.createBiquadFilter();
        hp.type = 'highpass';
        hp.frequency.value = 80;

        const lp = _ctx.createBiquadFilter();
        lp.type = 'lowpass';
        lp.frequency.value = 6000 + Math.random() * 2000;
        lp.Q.value = 0.7;

        src.connect(hp);
        hp.connect(lp);
        lp.connect(_gain);
        src.start();
    }

    // Current sequence — built by shuffling fragments
    let _seq = [];
    let _seqIdx = 0;

    function _buildSequence() {
        // Pick 4-6 random fragments, concatenate into a non-repeating sequence
        const count = 4 + Math.floor(Math.random() * 3);
        const picks = [];
        let lastPick = -1;
        for (let i = 0; i < count; i++) {
            let pick;
            do { pick = Math.floor(Math.random() * FRAGMENTS.length); } while (pick === lastPick);
            lastPick = pick;
            picks.push(...FRAGMENTS[pick]);
        }
        return picks;
    }

    function _scheduleNext() {
        if (!_active) return;

        // Rebuild sequence when exhausted — always fresh
        if (_seqIdx >= _seq.length) {
            _seq = _buildSequence();
            _seqIdx = 0;
        }

        const base = _seq[_seqIdx++];
        // ±15% jitter on each interval
        const delay = base + (Math.random() * base * 0.3 - base * 0.15);
        _timer = setTimeout(() => {
            if (!_active) return;
            _click();
            _scheduleNext();
        }, delay);
    }

    function start() {
        if (_active) return;
        if (!_ensureContext()) return;

        _active = true;
        _seq = _buildSequence();
        _seqIdx = 0;

        _gain = _ctx.createGain();
        _gain.gain.value = VOLUME;
        _gain.connect(_ctx.destination);

        // Start after a brief delay
        _timer = setTimeout(() => {
            if (!_active) return;
            _click();
            _scheduleNext();
        }, 200);
    }

    function stop() {
        _active = false;
        if (_timer) {
            clearTimeout(_timer);
            _timer = null;
        }
        if (_gain) {
            // Quick fade out to avoid pop
            try {
                _gain.gain.linearRampToValueAtTime(0, (_ctx?.currentTime || 0) + 0.05);
                setTimeout(() => {
                    try { _gain.disconnect(); } catch (_) {}
                    _gain = null;
                }, 60);
            } catch (_) {
                try { _gain.disconnect(); } catch (_2) {}
                _gain = null;
            }
        }
    }

    // Expose globally
    return { start, stop };
})();

// Make available globally
if (typeof window !== 'undefined') {
    window.ThinkingSounds = ThinkingSounds;
}
