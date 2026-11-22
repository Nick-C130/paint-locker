// Small paint/mix picker used by recipe steps, projects and the shopping list.
// Usage: <div x-data="picker({kinds: 'paint,mix', kind: '', id: null, label: ''})"> … see macros.html picker().
function picker(opts) {
  return {
    kinds: opts.kinds || 'paint,mix', kind: opts.kind || '', id: opts.id || null, label: opts.label || '',
    hex: opts.hex || '', q: '', res: [], sel: 0, open: false,
    async search() {
      const q = this.q.trim();
      if (!q) { this.res = []; return; }
      this.res = await (await fetch('/api/search?kinds=' + this.kinds + '&q=' + encodeURIComponent(q))).json();
      this.sel = 0; this.open = true;
    },
    choose(r) {
      if (!r) return;
      this.kind = r.kind; this.id = r.id; this.label = r.label; this.hex = r.hex || ''; this.q = ''; this.res = []; this.open = false;
    },
    clear() { this.kind = ''; this.id = null; this.label = ''; this.hex = ''; },
  };
}
