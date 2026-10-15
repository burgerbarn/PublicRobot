"""Build a self-contained HTML report ("what the robot sees") from an explore dataset folder.
Usage (robot, ~/vision/venv):  python report.py dataset/<UTC> [--out report.html]
Embeds each annotated frame as a small JPEG (data URI), so the page stands alone. Output is page content without
<html>/<body> wrappers (the Artifact publisher adds them)."""
import argparse, base64, html, json, math, os, time

import cv2


def thumb(path, width=560, q=74):
    img = cv2.imread(path)
    if img is None:
        return ''
    h, w = img.shape[:2]
    img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.tobytes()).decode() if ok else ''


def bearing(xyz):
    return math.degrees(math.atan2(xyz[1], xyz[0]))


CSS = """
/* layout: run log - summary band, room bearing strip, then evidence cards grouped by verdict */
:root {
  --bg: #f3f4f1; --panel: #ffffff; --ink: #1d2320; --muted: #5f6b64; --line: #d9ddd6;
  --accent: #c7621c; --ok: #2f7d4f; --bad: #b3433a; --floor: #3d6f99;
  --display: "Bricolage Grotesque", "Helvetica Neue", Arial, sans-serif;
  --body: "Atkinson Hyperlegible", "Helvetica Neue", Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #141816; --panel: #1c211e; --ink: #e6ebe7; --muted: #9aa69f; --line: #2d3430;
  --accent: #e0853f; --ok: #5cb582; --bad: #e0776c; --floor: #7aa9d4; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #141816; --panel: #1c211e; --ink: #e6ebe7; --muted: #9aa69f; --line: #2d3430;
  --accent: #e0853f; --ok: #5cb582; --bad: #e0776c; --floor: #7aa9d4; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--body); }
.wrap { max-width: 1120px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 56px; display: grid; gap: 28px; }
h1 { font: 700 clamp(28px, 5vw, 44px)/1.05 var(--display); letter-spacing: -0.02em; margin: 0; text-wrap: balance; }
h2 { font: 600 20px/1.2 var(--display); margin: 0; text-wrap: balance; }
.eyebrow { font: 500 12px/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
header { display: grid; gap: 10px; }
.lede { color: var(--muted); max-width: 68ch; margin: 0; }
.stats { display: flex; flex-wrap: wrap; gap: 10px 28px; font: 500 14px var(--mono); font-variant-numeric: tabular-nums; }
.stats b { font: 700 22px var(--display); margin-right: 6px; }
.stats .ok b { color: var(--ok); } .stats .bad b { color: var(--bad); } .stats .fl b { color: var(--floor); }
.note { border-left: 3px solid var(--accent); padding: 8px 14px; background: var(--panel); max-width: 80ch; }
.strip { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px 10px; overflow-x: auto; }
.strip svg { display: block; width: 100%; min-width: 520px; height: auto; }
section { display: grid; gap: 14px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 16px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; display: grid; min-width: 0; }
.card img { width: 100%; display: block; aspect-ratio: 16 / 10; object-fit: cover; background: var(--line); }
.meta { padding: 10px 12px 12px; display: grid; gap: 6px; min-width: 0; }
.title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.title span:first-child { font: 600 16px var(--display); }
.pill { font: 600 11px/1 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; padding: 4px 7px; border-radius: 999px; border: 1px solid currentColor; }
.pill.ok { color: var(--ok); } .pill.bad { color: var(--bad); } .pill.fl { color: var(--floor); }
dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; font: 12.5px/1.45 var(--mono); font-variant-numeric: tabular-nums; }
dt { color: var(--muted); } dd { margin: 0; overflow-wrap: anywhere; }
footer { color: var(--muted); font: 12px var(--mono); }
.review { display: grid; gap: 8px; padding: 10px 12px 12px; border-top: 1px solid var(--line); }
.review .row { display: flex; gap: 8px; flex-wrap: wrap; }
.review button { font: 600 13px var(--body); padding: 7px 12px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--bg); color: var(--ink); cursor: pointer; }
.review button:focus-visible, .review input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.review button[aria-pressed="true"].yes { background: var(--ok); border-color: var(--ok); color: var(--panel); }
.review button[aria-pressed="true"].no { background: var(--bad); border-color: var(--bad); color: var(--panel); }
.review input { font: 14px var(--body); padding: 7px 10px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--bg); color: var(--ink); width: 100%; min-width: 0; }
.review .saved { font: 12px var(--mono); color: var(--muted); min-height: 1em; }
.progress { font: 500 13px var(--mono); color: var(--muted); }
"""


SCRIPT = r"""<script>
(async () => {
  const cards = [...document.querySelectorAll('.review')];
  const progress = document.getElementById('progress');
  const db = window.claude ? await window.claude.use('db') : null;
  if (!db) {
    progress.textContent = 'Reviews save only when this page is open in Claude.';
    cards.forEach(c => c.querySelectorAll('button, input').forEach(el => el.disabled = true));
    return;
  }
  const col = db.collection('reviews'), state = {};
  const count = () => {
    const done = cards.filter(c => { const s = state[c.dataset.id]; return s && (s.verdict || s.actual); }).length;
    progress.textContent = `${done} of ${cards.length} reviewed`;
  };
  const show = (card, s) => {
    card.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(s.verdict === b.dataset.verdict)));
    const inp = card.querySelector('input');
    if (inp && document.activeElement !== inp) inp.value = s.actual || '';
  };
  const save = async (card, patch) => {
    const id = card.dataset.id, next = { ...(state[id] || {}), ...patch, updated_at: Date.now() };
    state[id] = next; show(card, next); count();
    const msg = card.querySelector('.saved');
    try { await col.doc(id).set(next); msg.textContent = 'Saved'; }
    catch (e) { msg.textContent = 'Not saved: ' + (e && e.message ? e.message : 'try again'); }
  };
  cards.forEach(card => {
    card.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      const cur = (state[card.dataset.id] || {}).verdict;
      save(card, { verdict: cur === b.dataset.verdict ? null : b.dataset.verdict });
    }));
    const inp = card.querySelector('input');
    if (inp) inp.addEventListener('change', () => save(card, { actual: inp.value.trim() }));
  });
  col.onSnapshot(snap => {
    snap.docs.forEach(d => { if (d.exists) { state[d.id] = d.data(); const c = cards.find(c => c.dataset.id === d.id); if (c) show(c, state[d.id]); } });
    count();
  }, () => { progress.textContent = 'Could not load saved reviews.'; });
})();
</script>
"""


def card(e, cls, pill, rows, img):
    dl = ''.join(f'<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>' for k, v in rows)
    rid = os.path.splitext(e['frame'])[0]
    if 'floor' in e:
        review = (f'<div class="review" data-id="{rid}"><input id="note-{rid}" aria-label="What is on the floor here"'
                  f' placeholder="What\'s on the floor here? (optional)" data-field="actual"><div class="saved"></div></div>')
    else:
        review = (f'<div class="review" data-id="{rid}"><div class="row">'
                  f'<button type="button" class="yes" data-verdict="right" aria-pressed="false">Right</button>'
                  f'<button type="button" class="no" data-verdict="wrong" aria-pressed="false">Wrong</button></div>'
                  f'<input id="actual-{rid}" aria-label="What it really is" placeholder="What is it really? (e.g. subwoofer)"'
                  f' data-field="actual"><div class="saved"></div></div>')
    return (f'<article class="card"><img src="{img}" alt="{html.escape(e.get("label", e.get("floor", "floor")))} frame" loading="lazy">'
            f'<div class="meta"><div class="title"><span>{html.escape(pill[0])}</span><span class="pill {cls}">{pill[1]}</span></div>'
            f'<dl>{dl}</dl></div>{review}</article>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dataset')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    d = a.dataset.rstrip('/')
    L = json.load(open(os.path.join(d, 'labels.json')))
    cands = [e for e in L if 'verdict' in e]
    floors = [e for e in L if 'floor' in e]
    conf = [e for e in cands if e['verdict'] == 'confirmed']
    rej = [e for e in cands if e['verdict'] == 'rejected']
    run = os.path.basename(d)
    when = time.strftime('%d %b %Y, %H:%M UTC', time.strptime(run, '%Y%m%dT%H%M%SZ'))

    # bearing strip: robot at centre, +left / -right, candidates as ticks coloured by verdict
    W, H, m = 1000, 150, 40
    x_of = lambda b: m + (90 - max(-90, min(90, b))) / 180 * (W - 2 * m)     # +90 (left) at the left edge
    marks = []
    for i, e in enumerate(sorted(cands, key=lambda e: bearing(e['xyz']))):
        b = bearing(e['xyz']); x = x_of(b); col = 'var(--ok)' if e['verdict'] == 'confirmed' else 'var(--bad)'
        y = 46 + (i % 3) * 22
        marks.append(f'<line x1="{x:.1f}" y1="104" x2="{x:.1f}" y2="{y + 6}" stroke="{col}" stroke-width="1.5"/>'
                     f'<circle cx="{x:.1f}" cy="{y}" r="5" fill="{col}"/>'
                     f'<text x="{x + 8:.1f}" y="{y + 4}" font-size="12" fill="var(--ink)" font-family="var(--mono)">'
                     f'{html.escape(e["label"].replace("a ", ""))}</text>')
    ticks = ''.join(f'<line x1="{x_of(t):.1f}" y1="104" x2="{x_of(t):.1f}" y2="112" stroke="var(--muted)"/>'
                    f'<text x="{x_of(t):.1f}" y="130" font-size="12" text-anchor="middle" fill="var(--muted)" font-family="var(--mono)">'
                    f'{"+" if t > 0 else ""}{t}°</text>' for t in (-90, -60, -30, 0, 30, 60, 90))
    strip = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Candidate bearings from the robot, left to right">'
             f'<line x1="{m}" y1="104" x2="{W - m}" y2="104" stroke="var(--muted)"/>{ticks}'
             f'<text x="{m}" y="20" font-size="12" fill="var(--muted)" font-family="var(--mono)">LEFT</text>'
             f'<text x="{W - m}" y="20" font-size="12" text-anchor="end" fill="var(--muted)" font-family="var(--mono)">RIGHT</text>'
             f'<text x="{x_of(0):.1f}" y="20" font-size="12" text-anchor="middle" fill="var(--muted)" font-family="var(--mono)">AHEAD</text>'
             f'{"".join(marks)}</svg>')

    def cand_card(e):
        b = bearing(e['xyz'])
        rows = [('bearing', f'{b:+.0f}° ({"left" if b > 0 else "right"})'), ('distance', f'{math.hypot(*e["xyz"][:2]):.2f} m'),
                ('height', f'{e["xyz"][2]:.2f} m'), ('scan', f'{e["scan_score"]:.2f} in {e["scan_views"]} view(s), '
                 + ('kept' if e['kept_by_filter'] else 'dropped') + ' by filter'),
                ('close look', f'{e["close_score"]:.2f}' if e['verdict'] == 'confirmed' else 'not found near centre'),
                ('arm aim', f'pan {e["aim"]["pan"]}, wrist {e["aim"]["wrist"]}')]
        ok = e['verdict'] == 'confirmed'
        return card(e, 'ok' if ok else 'bad', (e['label'].replace('a ', '').capitalize(), e['verdict']), rows,
                    thumb(os.path.join(d, e['frame'])))

    def floor_card(e):
        dets = ', '.join(f'{nm.replace("a ", "")} {sc:.2f}' for nm, sc, _ in e['dets'][:6]) or 'nothing detected'
        rows = [('view', 'floor right in front' if e['floor'] == 'near' else 'floor 0.3-4 m ahead'),
                ('arm pose', f'{e["pose"]}, pan {e["pan"]}'), ('found', dets)]
        return card(e, 'fl', (f'Floor, pan {e["pan"]}', e['floor']), rows, thumb(os.path.join(d, e['frame'])))

    floor_html = (''.join(floor_card(e) for e in floors) if floors else '')
    page = f"""<title>Den Look-Around</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=JetBrains+Mono:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <div class="eyebrow">ROSOrin Pro · self-training run {html.escape(run)}</div>
  <h1>What the robot saw in the Den</h1>
  <p class="lede">Arm-only run on the stand, {when}. The robot panned the camera across the room, ran open-vocabulary
  detection (OWLv2) for microphones, TVs and speakers, then aimed straight at every candidate for a closer second look.</p>
  <div class="stats"><span><b>{len(cands)}</b>candidates</span><span class="ok"><b>{len(conf)}</b>confirmed</span>
  <span class="bad"><b>{len(rej)}</b>rejected</span><span><b>{sum(e['kept_by_filter'] for e in cands)}</b>passed the scan filter</span>
  <span class="fl"><b>{len(floors)}</b>floor views</span></div>
</header>
<p class="note">"Confirmed" means the close look found the same kind of object near the centre of the frame. The bar is
low for now (score 0.15), so each card needs your check: mark it Right or Wrong and, if wrong, say what it really is.
Your answers save as you go and become Buddy's memory and the robot's example pictures.</p>
<div class="progress" id="progress">Loading your reviews...</div>
<section><h2>Where the candidates are</h2><div class="strip">{strip}</div></section>
<section><h2>Confirmed ({len(conf)})</h2><div class="cards">{''.join(cand_card(e) for e in conf)}</div></section>
<section><h2>Rejected ({len(rej)})</h2><div class="cards">{''.join(cand_card(e) for e in rej)}</div></section>
<section><h2>Floor</h2>{('<div class="cards">' + floor_html + '</div>') if floors else '<p class="lede">This run had no floor pass. The next cycle sweeps the floor 0.3-4 m ahead and right in front of the robot, looking for shoes, cables, boxes, bags, clothes and toys.</p>'}</section>
{SCRIPT}<footer>Generated by vision/report.py from {html.escape(d)} · robot on its stand · wheels disabled</footer>
</div>"""
    out = a.out or os.path.join(d, 'report.html')
    open(out, 'w').write(page)
    print('wrote', out, f'{os.path.getsize(out) / 1e6:.2f} MB')


if __name__ == '__main__':
    main()
