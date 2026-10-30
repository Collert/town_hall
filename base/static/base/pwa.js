// Registers the service worker (base/templates/base/sw.js) that makes the site
// installable and shows push notifications.
(function () {
    const script = document.currentScript;
    if (!('serviceWorker' in navigator) || !script) return;
    navigator.serviceWorker.register(script.dataset.serviceWorker, { scope: '/' }).catch(() => {});
})();
