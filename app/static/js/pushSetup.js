// Reminders M1.3 — Push notification frontend bootstrap.
//
// Runs on DOMContentLoaded. Detects browser support, registers the
// service worker if needed, and exposes `window.RemindersModule.push`
// with helper methods used by the inline banner + Settings toggle.

(function () {
    'use strict';

    function getCsrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        const input = document.querySelector('input[name="csrf_token"]');
        if (input) return input.value;
        const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function urlBase64ToUint8Array(base64String) {
        // Web Push spec wants the VAPID public key as a Uint8Array.
        // Convert base64url → Uint8Array.
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const raw = window.atob(base64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; ++i) {
            arr[i] = raw.charCodeAt(i);
        }
        return arr;
    }

    function isIOSSafariNonPWA() {
        const ua = navigator.userAgent;
        const isIOS = /iPad|iPhone|iPod/.test(ua);
        const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                             window.navigator.standalone === true;
        return isIOS && !isStandalone;
    }

    function isSupported() {
        return ('Notification' in window) &&
               ('serviceWorker' in navigator) &&
               ('PushManager' in window) &&
               !isIOSSafariNonPWA();
    }

    async function getRegistration() {
        if (!('serviceWorker' in navigator)) return null;
        let reg = await navigator.serviceWorker.getRegistration('/');
        if (!reg) {
            reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        }
        return reg;
    }

    async function isSubscribed() {
        if (!isSupported()) return false;
        const reg = await getRegistration();
        if (!reg) return false;
        const sub = await reg.pushManager.getSubscription();
        return sub !== null;
    }

    async function subscribe() {
        if (!isSupported()) return { ok: false, reason: 'unsupported' };

        const perm = await Notification.requestPermission();
        if (perm !== 'granted') return { ok: false, reason: 'denied' };

        const reg = await getRegistration();
        const vapidResp = await fetch('/push/vapid-public-key', { credentials: 'same-origin' });
        if (!vapidResp.ok) return { ok: false, reason: 'vapid-unavailable' };
        const { key } = await vapidResp.json();
        if (!key) return { ok: false, reason: 'vapid-missing' };

        let sub;
        try {
            sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(key),
            });
        } catch (err) {
            console.warn('pushManager.subscribe failed', err);
            return { ok: false, reason: 'subscribe-failed', error: String(err) };
        }

        const payload = sub.toJSON();
        const resp = await fetch('/push/subscribe', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
            body: JSON.stringify({
                endpoint: sub.endpoint,
                keys: payload.keys,
                userAgent: navigator.userAgent,
            }),
        });
        if (!resp.ok) return { ok: false, reason: 'server', status: resp.status };
        return { ok: true, subId: (await resp.json()).subId };
    }

    async function unsubscribeAll() {
        await fetch('/push/subscribe', {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': getCsrf() },
        });
        if ('serviceWorker' in navigator) {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (reg) {
                const sub = await reg.pushManager.getSubscription();
                if (sub) await sub.unsubscribe();
            }
        }
    }

    async function dismissPrompt() {
        await fetch('/push/dismiss-prompt', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': getCsrf() },
        });
    }

    // Expose on the existing RemindersModule namespace.
    window.RemindersModule = window.RemindersModule || {};
    window.RemindersModule.push = {
        isSupported,
        isSubscribed,
        subscribe,
        unsubscribeAll,
        dismissPrompt,
    };

    // On DOMContentLoaded, pre-register the SW for users who already have
    // permission granted — so notifications can fire even before they
    // hit Settings. No-op otherwise.
    document.addEventListener('DOMContentLoaded', async () => {
        if (!isSupported()) return;
        if (Notification.permission !== 'granted') return;
        try {
            await getRegistration();
        } catch (err) {
            console.warn('SW pre-register failed', err);
        }
    });
})();
