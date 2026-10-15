// Buddy's face: eyes + a mouth that is the spectrum analyzer of Buddy's own voice.
// window.BuddyFace.connect(eventsUrl) -> model; window.BuddyFace.draw(ctx, w, h, model, tSeconds)
(() => {
  const COLORS = { idle: '#ffb020', listening: '#46ff8a', thinking: '#33d6ff', working: '#b388ff', speaking: '#fff0d0' };
  const NB = 20;

  function connect(url) {
    const m = { state: 'idle', text: '', bands: new Array(NB).fill(0), target: new Array(NB).fill(0), level: 0,
                music: false, musicText: '', lastEvent: 0, lastActive: performance.now() / 1000, boredK: 0, yawnAt: 0, blinkAt: 0, blinkEnd: 0, gaze: [0, 0], gazeTarget: [0, 0], gazeNext: 0 };
    let es;
    const open = () => {
      es = new EventSource(url);
      es.onmessage = e => {
        const d = JSON.parse(e.data); m.lastEvent = performance.now() / 1000;
        if ((d.t === 'state' && d.state !== 'idle') || (d.t === 'music' && d.on)) m.lastActive = m.lastEvent;
        if (d.t === 'state') { m.state = d.state; m.text = d.text || ''; if (d.state !== 'speaking' && !m.music) m.target.fill(0); }
        else if (d.t === 'music') { m.music = d.on; m.musicText = d.text || ''; if (!d.on) m.target.fill(0); }
        else if (d.t === 'bands') m.target = d.b;
        else if (d.t === 'level') m.level = d.v;
        else if (d.t === 'speak') m.spk = { t0: m.lastEvent + d.delay, dur: d.dur };
      };
      es.onerror = () => { es.close(); setTimeout(open, 2000); };
    };
    open();
    return m;
  }

  // idle moments: a random expression about once a minute (45-75 s) while idle (not bored, no music). Each gets envelope e (0..1)
  // and progress p (0..1); returns per-eye [left, right] scale sx/sy, lift dy (x S), lid (fraction), gaze, mouth.
  const EXPR = {
    pulse:       (e, p) => { const k = 1 + 0.2 * Math.sin(p * Math.PI * 4) * e; return { sx: [k, k], sy: [k, k] }; },
    curious:     e => ({ sx: [1 + 0.25 * e, 1 - 0.12 * e], sy: [1 + 0.25 * e, 1 - 0.15 * e], dy: [-0.02 * e, 0.015 * e], gaze: [0.3, -0.2] }),
    surprised:   e => ({ sx: [1 + 0.2 * e, 1 + 0.2 * e], sy: [1 + 0.3 * e, 1 + 0.3 * e], o: 0.3 * e, smile: 1 - e }),
    happy:       e => (e > 0.5 ? { arc: true, smile: 1 + 1.3 * e } : { sy: [1 - 0.9 * e, 1 - 0.9 * e], smile: 1 + 1.3 * e }),  // ^ ^
    wink:        e => ({ sy: [1, 1 - 0.92 * e], smile: 1 + 0.7 * e }),
    suspicious:  e => ({ lid: [0.45 * e, 0.45 * e], gaze: [0.7, 0.1], smile: 1 - 1.4 * e }),
    lookaround:  (e, p) => ({ gaze: [Math.sin(p * Math.PI * 2) * 0.8, 0] }),
    doubleblink: (e, p) => { const c = (p > 0.2 && p < 0.32) || (p > 0.45 && p < 0.57) ? 0.08 : 1; return { sy: [c, c] }; },
  };
  const EXPR_S = { pulse: 2.4, curious: 2.8, surprised: 1.6, happy: 2.5, wink: 1.0, suspicious: 3.0, lookaround: 2.2, doubleblink: 1.0 };

  function rr(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function draw(ctx, w, h, m, t, opts = {}) {
    const col = m.music && m.state === 'idle' ? `hsl(${(t * 40) % 360}, 100%, 60%)`     // faceplay: eyes + caption cycle too,
      : COLORS[m.state] || COLORS.idle;                                            // matching the mouth's centre
    // easing is per 60 Hz frame; ease(k) keeps the same speed at any frame rate (127 fps in Chromium ran it 2x fast)
    const dt = Math.min(0.1, Math.max(0, t - (m._t ?? t))); m._t = t;
    const ease = k => 1 - Math.pow(1 - k, dt * 60);
    // smooth the spectrum: fast attack, slower release
    for (let i = 0; i < NB; i++) {
      const tgt = (m.state === 'speaking' || m.music) ? (m.target[i] || 0) : 0;   // voice (or music) spectrum
      m.bands[i] += (tgt - m.bands[i]) * ease(tgt > m.bands[i] ? 0.6 : 0.18);
    }
    // bored: idle, no music, nothing happened for opts.boredAfter s (default 5 min)
    if (m.music || m.state !== 'idle') m.lastActive = Math.max(m.lastActive, t);
    const bored = m.state === 'idle' && !m.music && t - m.lastActive > (opts.boredAfter ?? 300);
    m.boredK += ((bored ? 1 : 0) - m.boredK) * ease(bored ? 0.01 : 0.15);             // sinks in slowly, snaps out fast
    const bk = m.boredK;
    if (bored && !m.yawnAt) m.yawnAt = t + 8;
    if (!bored) m.yawnAt = 0;
    if (bored && t > m.yawnAt + 3.2) m.yawnAt = t + 35 + Math.random() * 40;       // a yawn every ~35-75 s
    const yp = bored && t > m.yawnAt ? (t - m.yawnAt) / 3.2 : -1;
    const yawn = yp >= 0 && yp <= 1 ? Math.sin(Math.PI * Math.min(1, yp * 1.3)) : 0;
    // blinking (slow, heavy blinks when bored)
    if (t > m.blinkAt + 0.15 && t > m.blinkEnd) { m.blinkAt = t + 2.5 + Math.random() * 4; m.blinkEnd = m.blinkAt + 0.14 + bk * 0.3; }
    const blink = (t > m.blinkAt && t < m.blinkEnd) ? 0.08 : 1;
    // idle moments (opts.expr forces one, for testing)
    const canExpr = m.state === 'idle' && !m.music && bk < 0.2;
    const exprGap = () => opts.expr ? 4 : 45 + Math.random() * 30;                      // ~every 60 s
    if (m.exprNext == null) m.exprNext = t + exprGap();
    if (!canExpr) { m.expr = null; m.exprNext = Math.max(m.exprNext, t + (opts.expr ? 4 : 20)); }
    if (canExpr && !m.expr && t > m.exprNext) {
      const names = Object.keys(EXPR);
      m.expr = { nm: opts.expr || names[Math.floor(Math.random() * names.length)], t0: t };
    }
    let X = {};
    if (m.expr) {
      const p = (t - m.expr.t0) / EXPR_S[m.expr.nm];
      if (p >= 1) { m.expr = null; m.exprNext = t + exprGap(); }
      else X = EXPR[m.expr.nm](Math.max(0, Math.min(1, p / 0.15, (1 - p) / 0.2)), p);
    }
    const breathe = m.state === 'idle' ? 1 + 0.035 * Math.sin(t * 1.3) : 1;       // eyes slowly breathe
    // gaze per state
    if (m.state === 'idle' && t > m.gazeNext) {
      m.gazeTarget = bk > 0.5
        ? [[-0.6, 0.3], [0.6, 0.3], [0.2, -0.5], [-0.3, 0.45]][Math.floor(Math.random() * 4)]   // looks around, bored
        : [(Math.random() - 0.5) * 0.6, (Math.random() - 0.5) * 0.35];
      m.gazeNext = t + (bk > 0.5 ? 4 + Math.random() * 5 : 1.5 + Math.random() * 3);
    }
    if (m.state === 'listening') m.gazeTarget = [0, 0];
    if (m.state === 'thinking') m.gazeTarget = [0.35, -0.35];
    if (m.state === 'working') m.gazeTarget = [Math.sin(t * 2.2) * 0.45, 0.05];
    if (m.state === 'speaking') m.gazeTarget = [Math.sin(t * 0.7) * 0.08, 0];
    if (X.gaze) m.gazeTarget = X.gaze;
    const gs = 0.08 - bk * 0.06;                                                  // bored eyes drift slowly
    m.gaze[0] += (m.gazeTarget[0] - m.gaze[0]) * ease(gs); m.gaze[1] += (m.gazeTarget[1] - m.gaze[1]) * ease(gs);

    // background
    if (opts.blackBg) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, w, h); }            // texture for the 3D head (additive)
    else if (!opts.transparent) {
      const g = ctx.createRadialGradient(w / 2, h * 0.45, h * 0.05, w / 2, h * 0.5, Math.max(w, h) * 0.7);
      g.addColorStop(0, '#15171b'); g.addColorStop(1, '#050506');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    } else {
      ctx.clearRect(0, 0, w, h); ctx.fillStyle = 'rgba(8,9,11,0.92)'; rr(ctx, 0, 0, w, h, h * 0.08); ctx.fill();
    }
    const S0 = Math.min(w, h * 1.6);           // base scale (caption); the face itself zooms:
    // Buddy drifts closer/further while idle, leans in when listening, sits back when bored (full-screen face only)
    const zt = opts.transparent ? 1
      : m.state === 'listening' ? 1.22 : m.state === 'speaking' ? 1.0 : m.state === 'thinking' || m.state === 'working' ? 0.95
      : m.music ? 1.1 : 1 + 0.1 * Math.sin(t * 2 * Math.PI / 37) + 0.05 * Math.sin(t * 2 * Math.PI / 13) - bk * 0.2;
    m.zoom = (m.zoom ?? zt) + (zt - (m.zoom ?? zt)) * ease(0.03);
    const S = S0 * m.zoom, fc = h * 0.5;      // face scale, face centre
    const bass = m.music ? (m.bands[0] + m.bands[1] + m.bands[2]) / 3 : 0;          // eyes bob to the beat
    const cx = w / 2, eyeY = fc - h * 0.16 * m.zoom - bass * S * 0.03, eyeDX = S * 0.17;
    const eyeW = S * 0.09 * (m.state === 'listening' ? 1.08 + m.level * 0.25 : 1);
    const eyeH = S * 0.2 * blink * (m.state === 'thinking' ? 0.8 : 1) * (m.state === 'listening' ? 1.08 : 1)
      * (m.music && m.state === 'idle' ? 0.6 : 1);                                   // happy squint while faceplaying
    ctx.shadowColor = col; ctx.shadowBlur = S * 0.05; ctx.fillStyle = col;
    for (const side of [-1, 1]) {
      const i = side < 0 ? 0 : 1;
      if (X.arc) {                                                                   // happy ^ ^ eyes
        const ax = cx + side * eyeDX + m.gaze[0] * S * 0.05, ay = eyeY + m.gaze[1] * S * 0.05 + S * 0.03;
        ctx.strokeStyle = col; ctx.lineWidth = eyeW * 0.42; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.arc(ax, ay, eyeW * 0.75, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
        continue;
      }
      const eW = eyeW * breathe * (X.sx ? X.sx[i] : 1), eH = eyeH * breathe * (X.sy ? X.sy[i] : 1);
      const ex = cx + side * eyeDX + m.gaze[0] * S * 0.05, ey = eyeY + m.gaze[1] * S * 0.05 + (X.dy ? X.dy[i] : 0) * S;
      const lid = eH * Math.min(0.92, bk * 0.5 + yawn * 0.4 + (X.lid ? X.lid[i] : 0));                     // heavy upper lids
      if (lid < 0.5) { rr(ctx, ex - eW / 2, ey - eH / 2, eW, eH, eW / 2); ctx.fill(); continue; }
      // lidded eye: flat top, round bottom (drawn as its own shape so the glow follows it)
      const x = ex - eW / 2, y = ey - eH / 2 + lid, eh = eH - lid, rb = Math.min(eW / 2, eh), rt = Math.min(eW * 0.12, eh / 2);
      ctx.beginPath(); ctx.moveTo(x + rt, y); ctx.lineTo(x + eW - rt, y); ctx.quadraticCurveTo(x + eW, y, x + eW, y + rt);
      ctx.arcTo(x + eW, y + eh, x, y + eh, rb); ctx.arcTo(x, y + eh, x, y, rb);
      ctx.lineTo(x, y + rt); ctx.quadraticCurveTo(x, y, x + rt, y); ctx.closePath(); ctx.fill();
    }
    // mouth = spectrum analyzer (mirrored around the centre, bars grow up and down from a gentle smile line)
    const mouthY = fc + h * 0.17 * m.zoom, mouthW = S * 0.62, n = NB * 2, gap = mouthW / n * 0.28, bw = mouthW / n - gap;
    const maxH = S * 0.2, x0 = cx - mouthW / 2;
    ctx.shadowBlur = S * 0.03;
    for (let k = 0; k < n; k++) {
      const band = k < NB ? NB - 1 - k : k - NB;                // low frequencies in the middle
      let v = m.bands[band];
      if (m.state === 'listening') v = Math.max(v, m.level * 0.35 * (1 - Math.abs(k - n / 2) / (n / 2)));
      const u = (k + 0.5) / n - 0.5;
      const smile = (m.state === 'speaking' || m.music ? 0.3 : 1) * (X.smile ?? 1) * S * 0.035 * (4 * u * u);       // corners up (canvas y grows downward)
      if (yawn) v = Math.max(v, yawn * 0.5 * Math.sqrt(Math.max(0, 1 - (u * 2.6) ** 2)));   // yawn: an O
      if (X.o) v = Math.max(v, X.o * Math.sqrt(Math.max(0, 1 - (u * 5) ** 2)));             // surprised: small o
      const hh = Math.max(S * 0.008, v * maxH);
      const flat = smile * (1 - bk) + bk * u * S * 0.03;                            // bored: flat, lopsided
      const x = x0 + k * (bw + gap) + gap / 2, y = mouthY - flat - hh / 2;
      ctx.globalAlpha = 0.55 + 0.45 * Math.min(1, v * 2 + 0.2);
      if (m.music) {                                                                 // faceplay: rainbow mouth, colours cycle
        const c = `hsl(${(Math.abs(u) * 2 * 300 + t * 40) % 360}, 100%, 60%)`;
        ctx.fillStyle = c; ctx.shadowColor = c;
      }
      rr(ctx, x, y, bw, hh, Math.min(bw / 2, S * 0.006)); ctx.fill();
    }
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
    // scanlines
    if (!opts.noScan) {
      ctx.fillStyle = 'rgba(0,0,0,0.18)';
      for (let y = 0; y < h; y += 4) ctx.fillRect(0, y, w, 1);
    }
    if (!opts.noCaption) drawCaption(ctx, w, h, m, t, S0);
  }

  // caption (own function: the 3D face draws it flat on screen while the face is a texture on the head)
  function drawCaption(ctx, w, h, m, t, S0 = Math.min(w, h * 1.6)) {
    const col = m.music && m.state === 'idle' ? `hsl(${(t * 40) % 360}, 100%, 60%)` : COLORS[m.state] || COLORS.idle;
    const bk = m.boredK || 0;
    const yp = bk > 0.5 && m.yawnAt && t > m.yawnAt ? (t - m.yawnAt) / 3.2 : -1;
    const yawn = yp >= 0 && yp <= 1 ? Math.sin(Math.PI * Math.min(1, yp * 1.3)) : 0;
    const label = { idle: m.music ? (m.musicText ? '♪ ' + m.musicText : '♪') : bk > 0.5 ? (yawn > 0.3 ? '*yawn*' : '…') : 'say "yo buddy"', listening: 'listening…', thinking: 'thinking…', working: m.text || 'working…',
                    speaking: '' }[m.state] ?? '';
    const txt = (m.state === 'speaking' || m.state === 'listening') && m.text ? m.text : label;
    if (!txt) return;
    // owner 2026-10-01: easier to read - near-white text, full opacity, a bit larger; the state colour is the glow
    ctx.font = `${Math.round(S0 * 0.034)}px "Share Tech Mono", ui-monospace, monospace`;
    ctx.fillStyle = '#f4f4f0'; ctx.globalAlpha = 1; ctx.textAlign = 'center';
    ctx.shadowColor = col; ctx.shadowBlur = Math.round(S0 * 0.012);
    const words = txt.split(' '), lines = []; let line = '';
    for (const wd of words) { const tl = line ? line + ' ' + wd : wd; if (ctx.measureText(tl).width > w * 0.94) { lines.push(line); line = wd; } else line = tl; }
    lines.push(line);
    // two lines at a time; while speaking, page through in step with the audio
    let page = Math.max(0, Math.ceil(lines.length / 2) - 1);
    if (m.state === 'speaking' && m.spk && m.spk.dur > 0) {
      const total = lines.reduce((n, l) => n + l.length + 1, 0);
      let pos = Math.max(0, Math.min(1, (t - m.spk.t0) / m.spk.dur)) * total, li = 0;
      while (li < lines.length - 1 && pos > lines[li].length + 1) { pos -= lines[li].length + 1; li++; }
      page = Math.floor(li / 2);
    }
    lines.slice(page * 2, page * 2 + 2).forEach((l, i, arr) => ctx.fillText(l, w / 2, h * 0.95 - (arr.length - 1 - i) * S0 * 0.044));
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
  }
  window.BuddyFace = { connect, draw, drawCaption };
})();
