// Colour capture from a photo. Tap the reference card, then the paint. The card's known colour
// gives a per-channel white balance (done in linear light), which is applied to the paint sample.
// Samples use the per-channel median of a small disc, so glare and grain don't skew them.

const toLin = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const toSrgb = l => { l = Math.min(1, Math.max(0, l)); const c = l <= 0.0031308 ? 12.92 * l : 1.055 * Math.pow(l, 1 / 2.4) - 0.055; return Math.round(c * 255); };
const hexOf = rgb => '#' + rgb.map(v => v.toString(16).padStart(2, '0')).join('');
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));

// Reference cards: colour the card really is (sRGB).
const CARDS = {
  grey18: { label: '18% grey card', hex: '#777777' },
  white: { label: 'White paper / white card', hex: '#f2f2f2' },
  custom: { label: 'Card of a known colour', hex: '#808080' },
};

function capture(opts) {
  return {
    cards: CARDS, card: localStorage.getItem('pl-card') || 'grey18', customHex: localStorage.getItem('pl-card-hex') || '#808080',
    step: 'photo', img: null, refRaw: null, sampleRaw: null, radius: 2, marks: [],
    compare: opts.compare || null, saveUrl: opts.saveUrl || null,

    get cardHex() { return this.card === 'custom' ? this.customHex : CARDS[this.card].hex; },
    get corrected() {
      if (!this.sampleRaw) return null;
      if (!this.refRaw) return hexOf(this.sampleRaw);
      const want = rgbOf(this.cardHex).map(toLin);
      const got = this.refRaw.map(toLin);
      return hexOf(this.sampleRaw.map((v, i) => toSrgb(toLin(v) * (got[i] > 0.002 ? want[i] / got[i] : 1))));
    },
    get rawHex() { return this.sampleRaw ? hexOf(this.sampleRaw) : null; },
    get warning() {
      if (this.refRaw) {
        const m = Math.max(...this.refRaw);
        if (m > 250) return 'The card looks blown out (too bright). Retake with less light or exposure, or the colour will be off.';
        if (m < 25) return 'The card is very dark in this photo. Retake in better light.';
      }
      if (this.sampleRaw && Math.max(...this.sampleRaw) > 250) return 'The paint sample is clipped. Avoid glare and shiny spots.';
      return '';
    },

    saveCard() { localStorage.setItem('pl-card', this.card); localStorage.setItem('pl-card-hex', this.customHex); },

    load(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      const im = new Image();
      im.onload = () => {
        const canvas = this.$refs.canvas;
        const scale = Math.min(1, 1600 / Math.max(im.width, im.height));
        canvas.width = Math.round(im.width * scale); canvas.height = Math.round(im.height * scale);
        canvas.getContext('2d', { willReadFrequently: true }).drawImage(im, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        this.img = true; this.refRaw = null; this.sampleRaw = null; this.marks = [];
        this.step = this.card ? 'card' : 'paint';
      };
      im.src = url;
    },

    medianAt(x, y) {
      const canvas = this.$refs.canvas;
      const r = Math.max(3, Math.round(Math.min(canvas.width, canvas.height) * this.radius / 100));
      const x0 = Math.max(0, x - r), y0 = Math.max(0, y - r);
      const w = Math.min(canvas.width - x0, 2 * r), h = Math.min(canvas.height - y0, 2 * r);
      const data = canvas.getContext('2d').getImageData(x0, y0, w, h).data;
      const ch = [[], [], []];
      for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
        const dx = x0 + i - x, dy = y0 + j - y;
        if (dx * dx + dy * dy > r * r) continue;
        const k = (j * w + i) * 4;
        ch[0].push(data[k]); ch[1].push(data[k + 1]); ch[2].push(data[k + 2]);
      }
      return { rgb: ch.map(a => { a.sort((p, q) => p - q); return a[a.length >> 1] || 0; }), r };
    },

    tap(ev) {
      const canvas = this.$refs.canvas;
      const rect = canvas.getBoundingClientRect();
      const x = Math.round((ev.clientX - rect.left) * canvas.width / rect.width);
      const y = Math.round((ev.clientY - rect.top) * canvas.height / rect.height);
      const { rgb, r } = this.medianAt(x, y);
      const mark = { x: x / canvas.width * 100, y: y / canvas.height * 100, r: r / canvas.width * 100 };
      if (this.step === 'card') {
        this.refRaw = rgb; this.marks = [{ ...mark, kind: 'card' }, ...this.marks.filter(m => m.kind !== 'card')]; this.step = 'paint';
      } else {
        this.sampleRaw = rgb; this.marks = [...this.marks.filter(m => m.kind !== 'paint'), { ...mark, kind: 'paint' }]; this.step = 'done';
      }
    },
    retapCard() { this.step = 'card'; },
    retapPaint() { this.step = 'paint'; },
    skipCard() { this.refRaw = null; this.marks = this.marks.filter(m => m.kind !== 'card'); this.step = 'paint'; },
  };
}
