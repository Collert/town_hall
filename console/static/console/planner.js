// "Help me plan" panel (console/partials/planner.html). HTMX loads and polls the
// conversation; this only opens and closes the panel, keeps the log scrolled, and
// sends on Enter.
(function () {
    const root = document.getElementById('planner');
    const panel = document.getElementById('planner-panel');
    if (!root || !panel) return;
    const opener = root.querySelector('[data-planner-open]');
    const form = panel.querySelector('.planner-compose');
    const input = form.querySelector('textarea');
    const send = form.querySelector('.planner-send');
    const reload = panel.querySelector('.planner-reload');
    let baseline = null;  // the event's change count when the log first loaded

    const remember = (open) => {
        try { sessionStorage.setItem('planner-open', open ? '1' : ''); } catch (e) { /* storage off */ }
    };
    const log = () => document.getElementById('planner-log');
    const busy = () => log().dataset.busy === 'true';
    const syncSend = () => { send.disabled = busy() || !input.value.trim(); };

    const open = () => {
        panel.hidden = false;
        root.classList.add('is-open');
        htmx.trigger(opener, 'planner-load');
        remember(true);
        input.focus();
    };
    const close = () => {
        panel.hidden = true;
        root.classList.remove('is-open');
        remember(false);
    };

    opener.addEventListener('click', open);
    panel.querySelector('[data-planner-close]').addEventListener('click', close);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !panel.hidden && !document.querySelector('dialog[open]')) close();
    });

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 160) + 'px';
        syncSend();
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            if (!send.disabled) form.requestSubmit();
        }
    });
    form.addEventListener('htmx:afterRequest', () => {
        input.style.height = 'auto';
        syncSend();
    });

    // Each poll replaces the log, so react to whichever log element is new.
    let seen = null;
    document.body.addEventListener('htmx:afterSettle', () => {
        const current = log();
        if (!current || current === seen) return;
        seen = current;
        current.scrollTop = current.scrollHeight;
        const changes = Number(current.dataset.changes || 0);
        if (baseline === null) baseline = changes;
        reload.hidden = changes <= baseline;
        syncSend();
    });

    let reopen = false;
    try { reopen = sessionStorage.getItem('planner-open') === '1'; } catch (e) { /* storage off */ }
    // Stay open across the event's tabs (after HTMX has wired up the page).
    if (reopen) document.addEventListener('DOMContentLoaded', open);
})();
