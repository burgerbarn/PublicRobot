// Corner face over the jukebox / video pages: appears while Buddy listens, thinks, works or speaks.
(() => {
  const BASE = 'http://127.0.0.1:8290';
  const s = document.createElement('script'); s.src = BASE + '/face.js';
  s.onload = () => {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;right:2vw;bottom:3vh;width:26vw;max-width:520px;aspect-ratio:16/10;z-index:2147483647;' +
      'pointer-events:none;opacity:0;transform:translateY(12px);transition:opacity .35s,transform .35s;' +
      'border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.6);overflow:hidden;';
    const c = document.createElement('canvas'); c.style.cssText = 'width:100%;height:100%;display:block;';
    box.appendChild(c); document.body.appendChild(box);
    const ctx = c.getContext('2d'), model = BuddyFace.connect(BASE + '/events');
    let hideAt = 0;
    function frame(ts) {
      const t = ts / 1000, active = model.state !== 'idle';
      if (active) hideAt = t + 1.5;
      const show = active || t < hideAt;
      box.style.opacity = show ? '1' : '0'; box.style.transform = show ? 'none' : 'translateY(12px)';
      if (show) {
        const dpr = devicePixelRatio || 1, w = box.clientWidth, h = box.clientHeight;
        if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        BuddyFace.draw(ctx, w, h, model, t, { transparent: true, noScan: true });
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  };
  document.head.appendChild(s);

  // Desktop button (same as the face page): shows on mouse movement, hides after 3 s idle; minimizes the kiosk via
  // the face server (POST text/plain = simple CORS request, no preflight)
  const btn = document.createElement('button');
  btn.textContent = 'Desktop ⤓';
  btn.title = 'Minimize Buddy and show the desktop (Meta+Shift+B brings him back)';
  btn.style.cssText = 'position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:2147483647;padding:12px 22px;' +
    'font:600 18px/1 ui-monospace,monospace;color:#ffb020;background:rgba(20,20,24,.85);' +
    'border:1px solid rgba(255,176,32,.5);border-radius:10px;cursor:pointer;opacity:0;pointer-events:none;' +
    'transition:opacity .3s;';
  btn.onclick = () => fetch(BASE + '/mode', { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: 'desktop' })
    .catch(() => {});
  // Widget/Full button beside it: the kiosk as a 25 % window bottom-right (kept above) <-> full screen
  const size = btn.cloneNode(); size.title = 'Shrink to a 25 % window (Alt+drag to move/resize) or back to full screen';
  size.style.transform = 'translateX(calc(-100% - 10px))';
  btn.style.transform = 'translateX(10px)';
  const small = () => innerWidth < screen.width * 0.75;
  const label = () => { size.textContent = small() ? '⤢ Full' : '⤡ Widget'; };
  addEventListener('resize', label); label();
  size.onclick = () => fetch(BASE + '/mode', { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: small() ? 'full' : 'widget' })
    .catch(() => {});
  let hideT;
  const wake = () => {
    for (const b of [btn, size]) { b.style.opacity = '1'; b.style.pointerEvents = 'auto'; }
    clearTimeout(hideT);
    hideT = setTimeout(() => { for (const b of [btn, size]) { b.style.opacity = '0'; b.style.pointerEvents = 'none'; } }, 3000);
  };
  const mount = () => { document.body.appendChild(size); document.body.appendChild(btn); addEventListener('mousemove', wake, { passive: true }); };
  document.body ? mount() : addEventListener('DOMContentLoaded', mount);
})();
