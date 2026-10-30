// Web push on this device (base/push.py). Shows the "Stay in the loop" prompt to
// signed-in people who haven't decided yet, keeps an existing subscription registered
// to whoever is signed in, and drives the switch in Account settings
// ([data-push-device]). Markup and strings: base/partials/push_prompt.html.
(function () {
    const config = document.getElementById('push-config');
    if (!config) return;
    const prompt = document.getElementById('push-prompt');
    const DISMISS_KEY = 'th-push-dismissed-until';
    const OFF_KEY = 'th-push-off';
    const SNOOZE_DAYS = 14;

    const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

    function store(key, value) {
        try {
            if (value === undefined) return localStorage.getItem(key);
            if (value === null) localStorage.removeItem(key);
            else localStorage.setItem(key, value);
        } catch (e) { /* private mode or blocked storage: nothing is remembered */ }
        return null;
    }

    function keyBytes(key) {
        const raw = atob((key + '='.repeat((4 - key.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from(raw, (c) => c.charCodeAt(0));
    }

    function sameKey(subscription) {
        const current = subscription.options && subscription.options.applicationServerKey;
        if (!current) return true;
        const a = new Uint8Array(current);
        const b = keyBytes(config.dataset.publicKey);
        return a.length === b.length && a.every((v, i) => v === b[i]);
    }

    function post(url, body) {
        let headers = { 'Content-Type': 'application/json' };
        try {
            headers = { ...headers, ...JSON.parse(document.body.getAttribute('hx-headers') || '{}') };
        } catch (e) { /* no CSRF header: the request fails and nothing changes */ }
        return fetch(url, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'same-origin' })
            .then((response) => {
                if (!response.ok) throw new Error(response.status);
                return response;
            });
    }

    function registration() {
        return navigator.serviceWorker.ready;
    }

    async function currentSubscription() {
        return (await registration()).pushManager.getSubscription();
    }

    // 'unsupported' | 'ios' (needs Add to Home Screen first) | 'denied' | 'on' | 'off'
    async function state() {
        if (!supported) return isIOS && !standalone ? 'ios' : 'unsupported';
        if (Notification.permission === 'denied') return 'denied';
        const subscription = await currentSubscription();
        return Notification.permission === 'granted' && subscription ? 'on' : 'off';
    }

    async function save(subscription) {
        await post(config.dataset.subscribeUrl, subscription.toJSON());
        config.dataset.registered = subscription.endpoint;
    }

    async function subscribe() {
        const reg = await registration();
        let subscription = await reg.pushManager.getSubscription();
        if (subscription && !sameKey(subscription)) {
            await subscription.unsubscribe();
            subscription = null;
        }
        if (!subscription) {
            subscription = await reg.pushManager.subscribe({
                userVisibleOnly: true, applicationServerKey: keyBytes(config.dataset.publicKey),
            });
        }
        await save(subscription);
    }

    async function enable() {
        if ((await Notification.requestPermission()) !== 'granted') return false;
        await subscribe();
        store(OFF_KEY, null);
        return true;
    }

    async function disable() {
        const subscription = await currentSubscription();
        store(OFF_KEY, '1');
        if (!subscription) return;
        await post(config.dataset.unsubscribeUrl, { endpoint: subscription.endpoint }).catch(() => {});
        await subscription.unsubscribe();
    }

    function toast(kind) {
        if (window.showToast) {
            window.showToast(config.dataset[`${kind}Message`], kind === 'error' ? 'error' : 'success',
                kind === 'off' ? 'notifications_off' : null);
        }
    }

    // ----- Prompt -----
    function hidePrompt() {
        if (prompt) prompt.hidden = true;
    }

    function showPrompt(variant) {
        if (!prompt) return;
        prompt.querySelectorAll('[data-push-variant]').forEach((p) => { p.hidden = p.dataset.pushVariant !== variant; });
        prompt.querySelector('[data-push-enable]').hidden = variant !== 'ask';
        setTimeout(() => { prompt.hidden = false; }, 1500);
    }

    if (prompt) {
        prompt.querySelector('[data-push-dismiss]').addEventListener('click', () => {
            store(DISMISS_KEY, String(Date.now() + SNOOZE_DAYS * 864e5));
            hidePrompt();
        });
        prompt.querySelector('[data-push-enable]').addEventListener('click', async () => {
            hidePrompt();
            try {
                if (await enable()) toast('on');
            } catch (e) {
                toast('error');
            }
            renderDevice();
        });
    }

    // ----- Account settings switch -----
    const device = document.querySelector('[data-push-device]');

    async function renderDevice() {
        if (!device) return;
        const current = await state();
        const toggle = device.querySelector('[data-push-toggle]');
        toggle.checked = current === 'on';
        toggle.disabled = !['on', 'off'].includes(current);
        device.querySelectorAll('[data-push-note]').forEach((note) => { note.hidden = note.dataset.pushNote !== current; });
    }

    if (device) {
        device.querySelector('[data-push-toggle]').addEventListener('change', async (e) => {
            e.target.disabled = true;
            try {
                if (e.target.checked) toast((await enable()) ? 'on' : 'error');
                else {
                    await disable();
                    toast('off');
                }
            } catch (err) {
                toast('error');
            }
            renderDevice();
        });
        renderDevice();
    }

    // ----- On every page -----
    (async () => {
        const current = await state();
        if (current === 'on') {
            // Registered in this sign-in session already? Otherwise (new sign-in, another
            // account) register it to whoever is signed in now.
            const subscription = await currentSubscription();
            if (!sameKey(subscription)) await subscribe().catch(() => {});
            else if (config.dataset.registered !== subscription.endpoint) await save(subscription).catch(() => {});
            return;
        }
        // Allowed before (say, until they signed out), and not switched off here: turn back on quietly.
        if (current === 'off' && Notification.permission === 'granted' && !store(OFF_KEY)) {
            await subscribe().catch(() => {});
            renderDevice();
            return;
        }
        const snoozed = Number(store(DISMISS_KEY) || 0) > Date.now();
        if (snoozed || device) return;
        if (current === 'off' && Notification.permission === 'default') showPrompt('ask');
        else if (current === 'ios') showPrompt('ios');
    })();
})();
