// Drag and drop bottles between cells on the storage grid.
(function () {
  let dragItem = null;
  document.addEventListener('dragstart', e => {
    const el = e.target.closest('[data-item]');
    if (!el) return;
    dragItem = el.dataset.item;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragItem);
  });
  document.addEventListener('dragover', e => {
    const cell = e.target.closest('.cell[data-cell]');
    if (!cell || !dragItem) return;
    e.preventDefault();
    cell.classList.add('dragover');
  });
  document.addEventListener('dragleave', e => {
    const cell = e.target.closest('.cell[data-cell]');
    if (cell) cell.classList.remove('dragover');
  });
  document.addEventListener('drop', async e => {
    const cell = e.target.closest('.cell[data-cell]');
    if (!cell || !dragItem) return;
    e.preventDefault();
    cell.classList.remove('dragover');
    const swap = cell.classList.contains('filled');
    const res = await fetch('/items/' + dragItem + '/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cell_id: cell.dataset.cell, swap: swap }),
    });
    const data = await res.json().catch(() => ({}));
    dragItem = null;
    const params = new URLSearchParams();
    if (!data.ok) params.set('warn', data.error || 'Move failed');
    else if (data.warning) params.set('warn', data.warning);
    window.location = window.location.pathname + (params.toString() ? '?' + params : '');
  });
})();
