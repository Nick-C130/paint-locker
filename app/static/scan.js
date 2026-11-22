// Barcode scanning. USB/Bluetooth scanners act as keyboards: they type the code into the box and press Enter.
// Phone cameras use the browser's BarcodeDetector where available (Chrome/Android), otherwise ZXing.

const BARCODE_FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code'];

function loadZXing() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/zxing.min.js';
    s.onload = () => resolve(window.ZXing);
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

function scanner(linkPaint) {
  return {
    linkPaint, code: '', result: null, q: '', res: [], history: [],
    camera: false, cameraError: '', secure: window.isSecureContext,
    autoAdd: !!localStorage.getItem('pl-autoadd'), added: '', addWarning: '',
    _stream: null, _timer: null, _zx: null, _last: '', _lastAt: 0, _busy: false,

    init() { this.focusBox(); },
    focusBox() { this.$nextTick(() => { if (this.result && !this.result.found) return; this.$refs.box && this.$refs.box.focus(); }); },
    beep() {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const o = ctx.createOscillator(); o.frequency.value = 880; o.connect(ctx.destination);
        o.start(); o.stop(ctx.currentTime + 0.08);
      } catch (e) {}
      if (navigator.vibrate) navigator.vibrate(60);
    },

    async lookup(raw) {
      const code = String(raw || '').replace(/[^0-9A-Za-z]/g, '');
      this.code = '';
      if (!code) return;
      this.added = ''; this.addWarning = ''; this.q = ''; this.res = [];
      if (this.linkPaint) { await this.link(code, this.linkPaint.id, false); this.linkPaint = null; return; }
      const r = await fetch('/api/barcode/' + encodeURIComponent(code));
      if (!r.ok) return;
      this.result = await r.json();
      if (this.result.found) {
        if (this.autoAdd) await this.addBottle(this.result.paint.id);
        else this.history.unshift({ hex: this.result.paint.hex, text: 'Scanned ' + this.result.paint.label });
        this.focusBox();
      } else {
        this.$nextTick(() => this.$refs.linkbox && this.$refs.linkbox.focus());
      }
    },

    async search() {
      const q = this.q.trim();
      this.res = q ? (await (await fetch('/api/search?kinds=paint&q=' + encodeURIComponent(q))).json()) : [];
    },

    async link(barcode, paintId, alsoAdd) {
      const r = await fetch('/api/barcode/link', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ barcode, paint_id: paintId }) });
      if (!r.ok) return;
      const d = await r.json();
      this.result = { found: true, barcode: d.barcode, paint: d.paint };
      this.history.unshift({ hex: d.paint.hex, text: 'Linked ' + d.barcode + ' to ' + d.paint.label });
      if (alsoAdd) await this.addBottle(paintId);
      this.focusBox();
    },

    async addBottle(paintId) {
      const r = await fetch('/api/barcode/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paint_id: paintId }) });
      if (!r.ok) { this.addWarning = 'Could not add the bottle.'; return; }
      const d = await r.json();
      this.added = 'Added to ' + d.where + '.';
      this.addWarning = d.warning || '';
      this.result.paint = d.paint;
      this.history.unshift({ hex: d.paint.hex, text: 'Added ' + d.paint.label + ' → ' + d.where });
      this.focusBox();
    },

    onDetected(text) {
      const now = Date.now();
      if (this._busy || (text === this._last && now - this._lastAt < 3000)) return;
      this._last = text; this._lastAt = now; this._busy = true;
      this.beep();
      this.lookup(text).finally(() => { this._busy = false; });
    },

    async startCamera() {
      this.cameraError = '';
      if (!window.isSecureContext || !navigator.mediaDevices) {
        this.cameraError = 'The camera only works when Paint Locker is opened over HTTPS (or on localhost). Use a USB/Bluetooth scanner or type the code instead.';
        return;
      }
      this.camera = true;
      await this.$nextTick();
      const video = this.$refs.video;
      try {
        let useNative = false, supported = [];
        if ('BarcodeDetector' in window) {
          supported = await BarcodeDetector.getSupportedFormats();
          useNative = supported.includes('ean_13');
        }
        if (useNative) {
          this._stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
          video.srcObject = this._stream;
          await video.play();
          const detector = new BarcodeDetector({ formats: BARCODE_FORMATS.filter(f => supported.includes(f)) });
          const tick = async () => {
            if (!this.camera) return;
            try {
              const codes = await detector.detect(video);
              if (codes.length) this.onDetected(codes[0].rawValue);
            } catch (e) {}
            this._timer = setTimeout(tick, 200);
          };
          tick();
        } else {
          const ZX = await loadZXing();
          this._zx = new ZX.BrowserMultiFormatReader();
          await this._zx.decodeFromConstraints({ video: { facingMode: 'environment' } }, video, (res) => {
            if (res) this.onDetected(res.getText());
          });
        }
      } catch (e) {
        this.cameraError = 'Could not start the camera: ' + (e && e.message ? e.message : e);
        this.stopCamera();
      }
    },

    stopCamera() {
      this.camera = false;
      clearTimeout(this._timer);
      if (this._zx) { try { this._zx.reset(); } catch (e) {} this._zx = null; }
      if (this._stream) { this._stream.getTracks().forEach(t => t.stop()); this._stream = null; }
      const v = this.$refs.video; if (v) v.srcObject = null;
    },
  };
}
