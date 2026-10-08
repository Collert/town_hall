// Shared console behaviour. Prefer HTMX attributes in templates; this file only
// holds the few interactions HTMX cannot express.
(function () {
    // <dialog> helpers: [data-dialog-open="id"] opens, [data-dialog-close] closes,
    // clicking the backdrop closes.
    document.addEventListener('click', (e) => {
        const opener = e.target.closest('[data-dialog-open]');
        if (opener) {
            const dialog = document.getElementById(opener.dataset.dialogOpen);
            if (dialog) dialog.showModal();
            return;
        }
        const closer = e.target.closest('[data-dialog-close]');
        if (closer) {
            closer.closest('dialog').close();
            return;
        }
        if (e.target.tagName === 'DIALOG') e.target.close();
    });

    // HTMX-loaded dialogs open themselves once swapped in.
    document.body.addEventListener('htmx:afterSwap', (e) => {
        const dialog = e.target.matches('dialog') ? e.target : e.target.querySelector('dialog[data-autoshow]');
        if (dialog && !dialog.open) dialog.showModal();
    });

    // Server asks to close the open dialog (HX-Trigger: closeDialog).
    document.body.addEventListener('closeDialog', () => {
        document.querySelectorAll('dialog[open]').forEach((d) => d.close());
    });

    // Removable rows/chips: <button data-remove-closest=".selector">.
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-remove-closest]');
        if (btn) btn.closest(btn.dataset.removeClosest).remove();
    });

    // Language tabs for translated fields (console/partials/translated_fields.html).
    document.addEventListener('click', (e) => {
        const tab = e.target.closest('[data-lang-tab]');
        if (!tab) return;
        const group = tab.closest('[data-lang-group]');
        group.querySelectorAll('[data-lang-tab]').forEach((t) => t.setAttribute('aria-selected', t === tab));
        group.querySelectorAll('[data-lang-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.langPanel !== tab.dataset.langTab;
        });
    });

    // Markdown toolbar: <button data-md="bold" data-md-target="textarea-id">.
    const MD = {
        bold: ['**', '**', 'bold text'],
        italic: ['_', '_', 'italic text'],
        heading: ['\n## ', '\n', 'Heading'],
        list: ['\n- ', '', 'List item'],
        link: ['[', '](https://)', 'link text'],
    };
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-md]');
        if (!btn) return;
        const area = document.getElementById(btn.dataset.mdTarget);
        const [before, after, placeholder] = MD[btn.dataset.md];
        const { selectionStart: start, selectionEnd: end, value } = area;
        const selected = value.slice(start, end) || placeholder;
        area.setRangeText(before + selected + after, start, end, 'end');
        area.focus();
        area.dispatchEvent(new Event('input', { bubbles: true }));
    });

    // Markdown editor Write/Preview toggle (preview HTML is fetched by HTMX). The preview opens
    // under the text and re-renders as you keep typing, so it never shows a stale version.
    document.addEventListener('click', (e) => {
        const tab = e.target.closest('[data-md-mode]');
        if (!tab) return;
        const editor = tab.closest('.md-editor');
        editor.querySelectorAll('[data-md-mode]').forEach((t) => t.setAttribute('aria-selected', t === tab));
        editor.querySelector('.md-preview').hidden = tab.dataset.mdMode !== 'preview';
    });

    document.addEventListener('input', (e) => {
        const editor = e.target.closest('.md-editor');
        if (!editor || e.target.tagName !== 'TEXTAREA' || editor.querySelector('.md-preview').hidden) return;
        clearTimeout(editor.previewTimer);
        editor.previewTimer = setTimeout(() => {
            htmx.trigger(editor.querySelector('[data-md-mode="preview"]'), 'click');
        }, 400);
    });

    // Icon pickers: keep the preview in sync with a free-text icon input.
    document.addEventListener('input', (e) => {
        if (!e.target.matches('[data-icon-preview]')) return;
        const preview = document.getElementById(e.target.dataset.iconPreview);
        if (preview) preview.textContent = e.target.value || 'help';
    });

    // Unsaved-changes guard: <form data-unsaved-warning>. Editing it, or a successful
    // HTMX request from a [data-marks-dirty] control inside it (auto-translate), marks
    // it dirty; submitting it, or clicking a [data-discard] link, clears it. Leaving
    // the page while a form is dirty asks the browser to confirm first, and any
    // [data-dirty-notice] inside it is shown.
    const dirtyForms = new Set();
    const markDirty = (el) => {
        const form = el.closest && el.closest('form[data-unsaved-warning]');
        if (!form) return;
        dirtyForms.add(form);
        form.querySelectorAll('[data-dirty-notice]').forEach((notice) => { notice.hidden = false; });
    };
    document.addEventListener('input', (e) => markDirty(e.target));
    document.addEventListener('change', (e) => markDirty(e.target));
    // HTMX forms are cleared in htmx:afterRequest instead, once the save succeeds.
    document.addEventListener('submit', (e) => {
        if (!e.target.hasAttribute('hx-post')) dirtyForms.delete(e.target);
    });
    document.body.addEventListener('htmx:afterRequest', (e) => {
        const elt = e.detail.elt;
        if (!e.detail.successful) return;
        if (elt.matches('form[data-unsaved-warning]')) dirtyForms.delete(elt);
        else if (elt.matches('[data-marks-dirty]')) markDirty(elt);
    });
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-discard]')) dirtyForms.clear();
    });
    window.addEventListener('beforeunload', (e) => {
        if ([...dirtyForms].some((form) => form.isConnected)) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    // Image inputs: preview the chosen file in place.
    document.addEventListener('change', (e) => {
        if (!e.target.matches('input[type=file][data-image-preview]')) return;
        const file = e.target.files[0];
        const target = document.getElementById(e.target.dataset.imagePreview);
        if (!file || !target) return;
        target.style.backgroundImage = `url(${URL.createObjectURL(file)})`;
        target.classList.add('has-image');
    });
})();
