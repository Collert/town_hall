// Toast notification system. Server-rendered toasts come from Django messages;
// HTMX responses deliver theirs through the `toasts` HX-Trigger event
// (see base.middleware.HtmxMessagesMiddleware).
(function () {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const TOAST_DURATION = 5000;
    const ICONS = { success: 'check_circle', error: 'error', warning: 'warning', info: 'info' };

    function dismissToast(toast) {
        if (toast.classList.contains('toast-exit')) return;
        toast.classList.remove('toast-enter', 'toast-enter-active');
        toast.classList.add('toast-exit');
        toast.addEventListener('animationend', (e) => {
            if (e.animationName === 'toastBounceOut') toast.remove();
        }, { once: true });
        // Fallback in case animationend doesn't fire
        setTimeout(() => toast.remove(), 600);
    }

    function initToast(toast) {
        toast.classList.add('toast-enter');
        requestAnimationFrame(() => toast.classList.add('toast-enter-active'));

        const dismissBtn = toast.querySelector('.toast-dismiss');
        if (dismissBtn) dismissBtn.addEventListener('click', () => dismissToast(toast));

        if (toast.dataset.autoDismiss === 'true') {
            let remaining = TOAST_DURATION;
            let startedAt = Date.now();
            let timeoutId;
            const start = () => {
                startedAt = Date.now();
                timeoutId = setTimeout(() => dismissToast(toast), remaining);
            };
            const pause = () => {
                clearTimeout(timeoutId);
                remaining -= Date.now() - startedAt;
            };
            toast.addEventListener('mouseenter', pause);
            toast.addEventListener('mouseleave', start);
            start();
        }
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    window.showToast = function (message, type = 'info', icon = null, autoDismiss = true) {
        const toast = el('div', `toast toast-${type}`);
        toast.dataset.autoDismiss = autoDismiss;

        const iconWrap = el('div', 'toast-icon');
        iconWrap.appendChild(el('span', 'material-symbols-outlined', icon || ICONS[type] || 'notifications'));

        const content = el('div', 'toast-content');
        content.appendChild(el('span', 'toast-message', message));

        const dismiss = el('button', 'toast-dismiss');
        dismiss.setAttribute('aria-label', container.dataset.dismissLabel || 'Dismiss');
        dismiss.appendChild(el('span', 'material-symbols-outlined', 'close'));

        toast.append(iconWrap, content, dismiss, el('div', 'toast-progress'));
        container.appendChild(toast);
        initToast(toast);
    };

    container.querySelectorAll('.toast').forEach(initToast);

    document.body.addEventListener('toasts', (e) => {
        (e.detail.items || []).forEach((t) => window.showToast(t.message, t.tags));
    });
})();
