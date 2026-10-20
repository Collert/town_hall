// Shows a spinner on a submit button and disables it while its request runs, so a slow
// save (publishing, emails, uploads) can't be sent twice by clicking again.
// Covers every POST form and every non-GET HTMX request started by a button or form.
// Add data-no-busy to a form or button to opt out.
(function () {
    const SUBMITS = 'button:not([type="button"]):not([type="reset"]), input[type="submit"]';

    function markBusy(button) {
        if (!button || button.classList.contains('is-busy')) return;
        button.style.setProperty('--busy-color', getComputedStyle(button).color);
        button.classList.add('is-busy');
        button.setAttribute('aria-busy', 'true');
    }

    function unbusy(button) {
        button.classList.remove('is-busy');
        button.removeAttribute('aria-busy');
        button.style.removeProperty('--busy-color');
        if (button.dataset.busyDisabled) {
            button.disabled = false;
            delete button.dataset.busyDisabled;
        }
    }

    function disable(button) {
        if (button && !button.disabled) {
            button.disabled = true;
            button.dataset.busyDisabled = '1';
        }
    }

    // Full-page POST forms. This listener runs after the form's own handlers, so a
    // submit they cancelled (confirm dialogs, HTMX, validation) is left alone.
    document.addEventListener('submit', event => {
        const form = event.target;
        if (event.defaultPrevented || form.method !== 'post' || form.hasAttribute('data-no-busy')) return;
        if (form.dataset.busy) {  // Enter pressed again while the first submit is on its way
            event.preventDefault();
            return;
        }
        const button = event.submitter;
        if (button && button.hasAttribute('data-no-busy')) return;
        form.dataset.busy = '1';
        markBusy(button);
        // Disabling now would drop the clicked button's name/value from the request,
        // so wait until the browser has built the form data.
        setTimeout(() => {
            form.querySelectorAll(SUBMITS).forEach(disable);
            if (button) disable(button);  // a button outside the form (form="...")
        });
    });

    // HTMX requests. The spinner waits a moment so quick saves don't flicker.
    document.addEventListener('htmx:beforeRequest', event => {
        const { elt, requestConfig } = event.detail;
        if (!requestConfig || requestConfig.verb === 'get') return;
        let button = elt.matches('button, input[type="submit"]') ? elt : null;
        if (elt.tagName === 'FORM') {
            const trigger = requestConfig.triggeringEvent;
            if (trigger?.type !== 'submit') return;  // autosave on change/input
            button = trigger.submitter || elt.querySelector(SUBMITS);
        }
        if (!button || button.hasAttribute('data-no-busy') || elt.hasAttribute('data-no-busy')) return;
        disable(button);
        const timer = setTimeout(() => markBusy(button), 250);
        elt.addEventListener('htmx:afterRequest', () => {
            clearTimeout(timer);
            unbusy(button);
        }, { once: true });
    });

    // Back/forward restores the page from cache with the buttons still disabled.
    window.addEventListener('pageshow', event => {
        if (!event.persisted) return;
        document.querySelectorAll('.is-busy, [data-busy-disabled]').forEach(unbusy);
        document.querySelectorAll('form[data-busy]').forEach(form => delete form.dataset.busy);
    });
})();
