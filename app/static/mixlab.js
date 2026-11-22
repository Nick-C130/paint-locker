// Mix lab: live spectral mix preview (spectral.js), ΔE2000 against a target, save via JSON API.

function hexToLab(hex) {
  const n = parseInt(hex.slice(1), 16);
  const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => {
    v /= 255;
    return v > 0.04045 ? Math.pow((v + 0.055) / 1.055, 2.4) : v / 12.92;
  });
  const X = 0.41239079926595934 * rgb[0] + 0.357584339383878 * rgb[1] + 0.1804807884018343 * rgb[2];
  const Y = 0.21263900587151027 * rgb[0] + 0.715168678767756 * rgb[1] + 0.07219231536073371 * rgb[2];
  const Z = 0.01933081871559182 * rgb[0] + 0.11919477979462598 * rgb[1] + 0.9505321522496607 * rgb[2];
  const f = t => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  const fx = f(X / 0.95047), fy = f(Y / 1.0), fz = f(Z / 1.08883);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

function deltaE2000(l1, l2) {
  const [L1, a1, b1] = l1, [L2, a2, b2] = l2;
  const rad = Math.PI / 180, deg = 180 / Math.PI;
  const C1 = Math.hypot(a1, b1), C2 = Math.hypot(a2, b2), Cm = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)));
  const a1p = (1 + G) * a1, a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1), C2p = Math.hypot(a2p, b2);
  const h1p = C1p ? ((Math.atan2(b1, a1p) * deg) + 360) % 360 : 0;
  const h2p = C2p ? ((Math.atan2(b2, a2p) * deg) + 360) % 360 : 0;
  const dLp = L2 - L1, dCp = C2p - C1p;
  let dhp = 0;
  if (C1p * C2p) { dhp = h2p - h1p; if (dhp > 180) dhp -= 360; else if (dhp < -180) dhp += 360; }
  const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin(dhp * rad / 2);
  const Lmp = (L1 + L2) / 2, Cmp = (C1p + C2p) / 2;
  let hmp;
  if (!(C1p * C2p)) hmp = h1p + h2p;
  else if (Math.abs(h1p - h2p) <= 180) hmp = (h1p + h2p) / 2;
  else if (h1p + h2p < 360) hmp = (h1p + h2p + 360) / 2;
  else hmp = (h1p + h2p - 360) / 2;
  const T = 1 - 0.17 * Math.cos((hmp - 30) * rad) + 0.24 * Math.cos(2 * hmp * rad) + 0.32 * Math.cos((3 * hmp + 6) * rad) - 0.2 * Math.cos((4 * hmp - 63) * rad);
  const dTheta = 30 * Math.exp(-(((hmp - 275) / 25) ** 2));
  const Rc = 2 * Math.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7));
  const Sl = 1 + (0.015 * (Lmp - 50) ** 2) / Math.sqrt(20 + (Lmp - 50) ** 2);
  const Sc = 1 + 0.045 * Cmp, Sh = 1 + 0.015 * Cmp * T;
  const Rt = -Math.sin(2 * dTheta * rad) * Rc;
  return Math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh));
}

function normHex(h) {
  if (!h) return '';
  h = String(h).trim();
  if (!h.startsWith('#')) h = '#' + h;
  if (/^#[0-9a-fA-F]{3}$/.test(h)) h = '#' + h.slice(1).split('').map(c => c + c).join('');
  return /^#[0-9a-fA-F]{6}$/.test(h) ? h.toUpperCase() : '';
}

function mixLab(initial, refs, nonMixing, dpm) {
  return {
    m: initial, refs, nonMixing, dpm,
    q: '', res: [], ownedOnly: false, makeMl: 10,
    saving: false, error: '', targetNote: '',
    init() {},
    get total() { return this.m.components.reduce((s, c) => s + (Number(c.amount) || 0), 0); },
    share(c) { return this.total ? (Number(c.amount) || 0) / this.total : 0; },
    pct(c) { return Math.round(this.share(c) * 100); },
    get mixed() {
      const parts = this.m.components.filter(c => c.hex && Number(c.amount) > 0);
      if (!parts.length) return '';
      if (parts.length === 1) return parts[0].hex.toUpperCase();
      return spectral.mix(...parts.map(c => [new spectral.Color(c.hex), Number(c.amount)])).toString();
    },
    get de() {
      const t = normHex(this.m.target_hex);
      return t && this.mixed ? deltaE2000(hexToLab(t), hexToLab(this.mixed)) : 0;
    },
    deWords(d) {
      return d < 1 ? 'indistinguishable' : d < 3 ? 'very close' : d < 6 ? 'close' : d < 12 ? 'noticeably different' : 'different';
    },
    async lookup() {
      const q = this.q.trim();
      if (!q) { this.res = []; return; }
      this.res = await (await fetch('/api/search?q=' + encodeURIComponent(q) + (this.ownedOnly ? '&owned=1' : ''))).json();
      if (this.m.id) this.res = this.res.filter(r => !(r.kind === 'mix' && r.id === this.m.id));
    },
    add(r) {
      this.m.components.push({ kind: r.kind, id: r.id, label: r.label, hex: r.hex, type: r.type, amount: 1 });
      this.q = ''; this.res = [];
    },
    async setTarget(h) {
      h = normHex(h);
      this.m.target_hex = h;
      if (!h) { this.targetNote = ''; return; }
      // Log the colour request (feeds the phase-2 purchase recommender)
      const r = await fetch('/api/colour-requests', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hex: h, source: 'mix_target', context: 'Mix lab target' + (this.m.name ? ': ' + this.m.name : '') }),
      });
      const d = await r.json().catch(() => ({}));
      this.targetNote = d.best_label ? 'Closest you already own: ' + d.best_label + ' (ΔE ' + d.best_delta_e + ')' : '';
    },
    async save() {
      this.error = '';
      if (!this.m.name.trim()) { this.error = 'Give the mix a name first.'; return; }
      if (!this.m.components.length) { this.error = 'Add at least one paint.'; return; }
      this.saving = true;
      const body = { ...this.m, target_hex: normHex(this.m.target_hex), measured_hex: normHex(this.m.measured_hex),
        components: this.m.components.map(c => ({ kind: c.kind, id: c.id, amount: c.amount })) };
      const r = await fetch('/api/mixes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      this.saving = false;
      if (!r.ok) { this.error = d.detail || 'Save failed'; return; }
      window.location = d.url + '?msg=' + encodeURIComponent('Recipe saved.');
    },
  };
}
