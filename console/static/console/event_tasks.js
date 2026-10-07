// Event > Tasks drag and drop. HTMX can't express dragging, so this file only works out
// what was dropped where and hands the request to htmx.ajax:
// - a task dropped on another list's card moves there (both cards re-render);
// - a day-of list dragged by its handle onto another day-of list merges into it.
// Cards carry data-list-id, data-move-url and (day-of lists) data-merge-url.
(function () {
    let drag = null; // {type: 'task' | 'list', card, el, taskId}

    const clearTargets = () => {
        document.querySelectorAll('.task-card.is-drop-target').forEach((c) => c.classList.remove('is-drop-target'));
    };

    const dropTarget = (e) => {
        if (!drag) return null;
        const card = e.target.closest('.task-card[data-list-id]');
        if (!card || card === drag.card) return null;
        if (drag.type === 'list' && !card.dataset.mergeUrl) return null;
        return card;
    };

    document.addEventListener('dragstart', (e) => {
        const handle = e.target.closest && e.target.closest('[data-list-handle]');
        const task = !handle && e.target.closest && e.target.closest('.task-item[data-task-id]');
        if (!handle && !task) return;
        const card = (handle || task).closest('.task-card[data-list-id]');
        if (!card) return;
        if (handle) {
            drag = { type: 'list', card, el: card };
            e.dataTransfer.setDragImage(card, 24, 24);
        } else {
            e.stopPropagation(); // a subtask shouldn't also drag its parent
            drag = { type: 'task', card, el: task, taskId: task.dataset.taskId };
        }
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', ''); // Firefox only starts a drag with data
        drag.el.classList.add('is-dragging');
        document.body.classList.add(`tasks-dragging-${drag.type}`);
    });

    document.addEventListener('dragend', () => {
        if (drag) drag.el.classList.remove('is-dragging');
        document.body.classList.remove('tasks-dragging-task', 'tasks-dragging-list');
        clearTargets();
        drag = null;
    });

    document.addEventListener('dragover', (e) => {
        const card = dropTarget(e);
        if (!card) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (!card.classList.contains('is-drop-target')) {
            clearTargets();
            card.classList.add('is-drop-target');
        }
    });

    document.addEventListener('dragleave', (e) => {
        const card = e.target.closest && e.target.closest('.task-card.is-drop-target');
        if (card && !card.contains(e.relatedTarget)) card.classList.remove('is-drop-target');
    });

    document.addEventListener('drop', (e) => {
        const card = dropTarget(e);
        if (!card) return;
        e.preventDefault();
        const { type, card: source, taskId } = drag;
        clearTargets();
        if (type === 'list') {
            const layout = document.querySelector('[data-merge-confirm]');
            const question = (layout ? layout.dataset.mergeConfirm : 'Merge "%(source)s" into "%(target)s"?')
                .replace('%(source)s', source.dataset.listName)
                .replace('%(target)s', card.dataset.listName);
            if (!window.confirm(question)) return;
            htmx.ajax('POST', card.dataset.mergeUrl, {
                source: card, target: card, swap: 'outerHTML', values: { source: source.dataset.listId },
            });
        } else {
            htmx.ajax('POST', card.dataset.moveUrl, {
                source: card, target: card, swap: 'outerHTML', values: { task: taskId, source: source.dataset.listId },
            });
        }
    });
})();
