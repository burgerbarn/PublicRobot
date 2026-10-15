#!/usr/bin/env python3
"""For every code block a chapter shows for reading (SNIPPET, script LISTING, file pieces), check that its text is in
the repository: verbatim in some file, else line by line. Blocks whose text is not in the repo are reported: either
the guide quotes code that has changed since, or the block is new text (e.g. a ~/learn script). Usage:
  python3 snippet_check.py ch13 ch14 ...   (repo: $LIGHTBOT_REPO or ~/lightbot-sim/repo)"""
import glob, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_chapter as rc

EXT = {'python': ('.py',), 'yaml': ('.yaml', '.yml'), 'ini': ('.service', '.timer', '.cfg', '.ini', '.conf'),
       'xml': ('.xml', '.urdf', '.srdf'), 'bash': ('.sh', '.py'), 'json': ('.json',)}
files = {}
for d, dirs, fs in os.walk(rc.REPO):
    dirs[:] = [x for x in dirs if x not in ('.git', '__pycache__', 'guide')]
    for f in fs:
        p = os.path.join(d, f)
        try:
            files[p] = open(p, errors='replace').read()
        except OSError:
            pass
norm = lambda t: '\n'.join(l.rstrip() for l in t.strip('\n').split('\n'))
nfiles = {p: norm(t) for p, t in files.items()}
for ch in sys.argv[1:]:
    md = glob.glob(os.path.join(rc.CHAPTERS, f'{ch}-*.md'))[0]
    for b in rc.parse(md):
        if b['lang'] not in EXT or b['host'] not in ('robot', 'mac'):
            continue
        code = norm(b['code'])
        if b['lang'] == 'bash' and not code.startswith('#!'):
            continue                                    # commands, not listings
        cands = [p for p in nfiles if p.endswith(EXT[b['lang']])]
        if any(code in nfiles[p] for p in cands):
            continue
        lines = [l.strip() for l in code.split('\n') if l.strip() and not l.strip().startswith('#')]
        best = max(cands, key=lambda p: sum(l in nfiles[p] for l in lines)) if cands else None
        miss = [l for l in lines if best is None or l not in nfiles[best]]
        where = os.path.relpath(best, rc.REPO) if best else '-'
        print(f'{ch}:{b["line"]} {b["lang"]} @{b["host"]}: not verbatim; closest {where} '
              f'({len(lines) - len(miss)}/{len(lines)} lines){" missing: " + repr(miss[:3]) if miss else " (all lines present: whitespace/order only)"}')
