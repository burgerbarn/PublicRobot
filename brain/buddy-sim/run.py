#!/usr/bin/env python3
"""buddy-sim chapter runner: guide chapters 25-28 (bigbuddy) in a Fedora 44 container, --network none.

Usage (on bigbuddy):  python3 ~/buddy-sim/run.py [--fresh] ch25 ch26 ch27 ch28
  --fresh   remove the container and its home volume first (a new, empty bigbuddy)

Every block of the chapter with a host tag is handled by the plan in plans.py, keyed by the block's first line
number in the chapter file (the guide snapshot in ~/buddy-sim/repo):
  run      the block text, verbatim, as burgerbarn in a fresh bash (like a new `ssh bigbuddy` + `bash`)
  file     the block written to the path the prose names; compared with the repo file and the live bigbuddy file
  snippet  code shown for reading: must appear in the repo file (and is looked up in the live file)
  mac      an @mac block that copies repo files: emulated with the given commands (/repo = the laptop's repo)
  skip     not run, with a class and a reason
  fixup    an action between blocks that is not a guide step (recorded as such)
Unplanned blocks are listed as UNPLANNED. Results: ~/buddy-sim/results/chNN.jsonl and chNN.md.
The live system is only read (diff of files under the live home); nothing on it is written.
"""
import difflib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plans  # noqa: E402

IMAGE, NAME, VOL = 'buddy-sim:f44', 'buddy-sim', 'buddy-sim-home'
REPO = os.path.join(HERE, 'repo')
RES = os.path.join(HERE, 'results')
LIVE_HOME = '/home/burgerbarn'          # the live bigbuddy home (read only)
CHFILES = {c: next(f for f in os.listdir(os.path.join(REPO, 'guide/chapters')) if f.startswith(c + '-'))
           for c in ('ch25', 'ch26', 'ch27', 'ch28')}


def sh(cmd, inp=None, timeout=900):
    t = time.time()
    try:
        p = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + (('\n[stderr]\n' + p.stderr) if p.stderr.strip() else ''), time.time() - t
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        return 124, out + f'\n[runner] TIMEOUT after {timeout}s', time.time() - t


def container(fresh):
    if fresh:
        sh(['docker', 'rm', '-f', NAME]); sh(['docker', 'volume', 'rm', '-f', VOL])
    rc, out, _ = sh(['docker', 'inspect', '-f', '{{.State.Running}}', NAME])
    if rc != 0:
        rc, out, _ = sh(['docker', 'run', '-d', '--name', NAME, '--hostname', 'bigbuddy', '--network', 'none',
                         '-v', f'{VOL}:/home/burgerbarn', '-v', f'{REPO}:/repo:ro',
                         '-e', 'XDG_RUNTIME_DIR=/run/user/1000', IMAGE])
        assert rc == 0, out
        # a fresh volume is owned by root: give the home to burgerbarn and seed the skeleton like useradd does
        sh(['docker', 'exec', NAME, 'bash', '-c',
            'chown burgerbarn:burgerbarn /home/burgerbarn && [ -f /home/burgerbarn/.bashrc ] || '
            'sudo -u burgerbarn cp -rT /etc/skel /home/burgerbarn'])
    elif out.strip() != 'true':
        sh(['docker', 'start', NAME])
    rc, out, _ = sh(['docker', 'exec', NAME, 'bash', '-c', 'ip -br link; id burgerbarn'])
    return out


def cexec(script, stdin='', timeout=900, user='burgerbarn'):
    """Run script text in the container as a new bash (like a new ssh session + `bash`)."""
    sh(['docker', 'exec', '-i', NAME, 'bash', '-c', 'mkdir -p /tmp/sim && chmod 1777 /tmp/sim && cat > /tmp/sim/blk.sh && chmod 644 /tmp/sim/blk.sh'],
       inp=script)
    wrapper = '. ~/.bashrc >/dev/null 2>&1; . /tmp/sim/blk.sh'
    return sh(['docker', 'exec', '-i', '-u', user, '-w', '/home/burgerbarn', '-e', 'HOME=/home/burgerbarn',
               NAME, 'bash', '-c', wrapper], inp=stdin, timeout=timeout)


def parse(path):
    L = open(path).read().split('\n'); out = []; i = 0
    while i < len(L):
        m = re.match(r'^(\s*)```(\S*)(?:\s+@(\w+))?\s*$', L[i])
        if m and (m.group(2) or m.group(3)):
            ind = m.group(1); j = i + 1; body = []
            while j < len(L) and not re.match(r'^\s*```\s*$', L[j]):
                body.append(L[j][len(ind):] if L[j].startswith(ind) else L[j].lstrip()); j += 1
            k = i - 1
            while k >= 0 and L[k].strip() == '':
                k -= 1
            sec = next((L[s] for s in range(i, -1, -1) if L[s].startswith('#')), '')
            out.append(dict(line=i + 1, end=j + 1, lang=m.group(2), host=m.group(3), body='\n'.join(body) + '\n',
                            prev=L[k].strip(), section=sec.strip('# ').strip()))
            i = j + 1
        else:
            i += 1
    return out, L


def check_after(L, blk, nxt_line):
    """Expected output lines of the first `!!! check` box after the block (before the next host-tagged block)."""
    exp = []; inbox = False; fence = False
    for n in range(blk['end'], (nxt_line or len(L)) - 1):
        s = L[n]
        if s.startswith('!!! check'):
            if exp:
                break
            inbox = True; continue
        if inbox:
            if s and not s.startswith(' '):
                if exp:
                    break
                inbox = False; continue
            if re.match(r'^\s*```', s):
                fence = not fence; continue
            if (fence or s.startswith('        ')) and s.strip():
                exp.append(s.strip())
    return exp


def norm(s):
    return re.sub(r'\s+', ' ', s).strip()


def found(exp, out):
    o = norm(out)
    return [e for e in exp if norm(e) in o]


def read(path):
    try:
        return open(os.path.expanduser(path), errors='replace').read()
    except OSError as e:
        return None


def cmp_text(guide, other):
    if other is None:
        return 'missing'
    if guide == other:
        return 'identical'
    if guide.rstrip('\n') == other.rstrip('\n'):
        return 'identical except trailing newline'
    d = list(difflib.unified_diff(other.splitlines(), guide.splitlines(), 'other', 'guide', n=0, lineterm=''))
    return 'DIFFERS:\n' + '\n'.join(d[:40]) + ('\n...' if len(d) > 40 else '')


def snippet_in(snip, text):
    if text is None:
        return 'missing'
    if snip.strip('\n') in text:
        return 'verbatim'
    # line-by-line, in order, ignoring indentation
    lines = [l.strip() for l in snip.splitlines() if l.strip()]
    tl = [l.strip() for l in text.splitlines()]
    pos = 0
    for l in lines:
        try:
            pos = tl.index(l, pos) + 1
        except ValueError:
            near = difflib.get_close_matches(l, tl, 1, 0.6)
            return f'NOT FOUND line {l!r}' + (f'\n    closest in file: {near[0]!r}' if near else '')
    return 'all lines in order (indent differs)'


def run_chapter(ch):
    path = os.path.join(REPO, 'guide/chapters', CHFILES[ch])
    blocks, L = parse(path)
    plan = plans.PLAN[ch]
    recs = []
    tagged = [b for b in blocks if b['host']]
    for idx, b in enumerate(tagged):
        nxt = tagged[idx + 1]['line'] if idx + 1 < len(tagged) else None
        for fx in plan.get(f"{b['line']}-pre", []):
            rc, out, dt = cexec(fx[1], timeout=fx[3] if len(fx) > 3 else 900)
            recs.append(dict(line=b['line'], kind='FIXUP', status='FIXUP' if rc == 0 else 'FIXUP-FAIL', why=fx[2],
                             cmd=fx[1], rc=rc, secs=round(dt, 1), out=out[-3000:], section=b['section']))
        p = plan.get(b['line'])
        r = dict(line=b['line'], host=b['host'], lang=b['lang'], section=b['section'],
                 first=b['body'].splitlines()[0][:100] if b['body'].strip() else '')
        exp = check_after(L, b, nxt)
        if p is None:
            if b['host'] == 'robot':
                p = ('skip', 'OTHERHOST', 'robot block: never contact the robot')
            elif b['lang'] == 'text':
                p = ('skip', 'TEXT', 'text shown for reading')
            else:
                p = ('unplanned',)
        kind = p[0]; r['kind'] = kind
        if kind in ('run', 'mac'):
            if kind == 'mac':
                script, opt = p[1], (p[2] if len(p) > 2 else {})
                r['translation'] = p[3] if len(p) > 3 else 'laptop copy emulated from /repo'
            else:
                opt = p[1] if len(p) > 1 else {}
                script = opt.get('script', b['body'])
                if 'script' in opt:
                    r['translation'] = opt.get('why', 'changed for the container')
            rc, out, dt = cexec(script, stdin=opt.get('stdin', ''), timeout=opt.get('timeout', 900))
            r.update(rc=rc, secs=round(dt, 1), out=out[-6000:], status='OK' if rc == 0 else 'FAIL',
                     note=opt.get('note', ''))
            allout = out
            if opt.get('check_cmd'):
                rc2, out2, _ = cexec(opt['check_cmd'], timeout=300)
                r.update(check_cmd=opt['check_cmd'], check_rc=rc2, check_out=out2[-4000:]); allout += '\n' + out2
            if exp:
                f = found(exp, allout); r['check'] = f'{len(f)}/{len(exp)}'; r['check_missing'] = [e for e in exp if e not in f]
            if kind == 'mac':
                r['status'] = 'MAC-EMULATED' if rc == 0 else 'MAC-EMULATED-FAIL'
            if script != b['body'] and kind == 'run':
                r['status'] = 'OK-TRANSLATED' if rc == 0 else 'FAIL-TRANSLATED'
        elif kind == 'file':
            dest, opt = p[1], (p[2] if len(p) > 2 else {})
            body = b['body']
            if opt.get('write', True):
                d = dest.replace('~', '/home/burgerbarn', 1)
                if d.startswith('/home/'):
                    script = f'mkdir -p "$(dirname {d})" && cat > {d}'
                    rc, out, dt = cexec(script, stdin=body)
                else:
                    rc, out, dt = sh(['docker', 'exec', '-i', '-u', 'burgerbarn', NAME, 'bash', '-c',
                                      f'sudo mkdir -p "$(dirname {d})" && sudo tee {d} >/dev/null'], inp=body)
                r.update(rc=rc, status='FILE-OK' if rc == 0 else 'FILE-FAIL', dest=dest)
            else:
                r.update(status='FILE-SHOWN', dest=dest)
                if not opt.get('repo') and not opt.get('piece_of') and opt.get('live'):
                    r['live'] = opt['live']; r['vs_live'] = snippet_in(body, read(opt['live']))
                    opt = {}
            if opt.get('repo'):
                r['vs_repo'] = cmp_text(body, read(os.path.join(REPO, opt['repo']))); r['repo'] = opt['repo']
            if opt.get('live'):
                r['vs_live'] = cmp_text(body, read(opt['live'])); r['live'] = opt['live']
            if opt.get('piece_of'):
                r['vs_repo'] = snippet_in(body, read(os.path.join(REPO, opt['piece_of']))); r['repo'] = opt['piece_of']
                if opt.get('live'):
                    r['vs_live'] = snippet_in(body, read(opt['live']))
        elif kind == 'snippet':
            r['status'] = 'SNIPPET'
            rp = p[1]; lv = p[2] if len(p) > 2 else None
            r['repo'] = rp; r['vs_repo'] = snippet_in(b['body'], read(os.path.join(REPO, rp)))
            if lv:
                r['live'] = lv; r['vs_live'] = snippet_in(b['body'], read(lv))
        elif kind == 'skip':
            r.update(status='SKIPPED-' + p[1], why=p[2])
            if len(p) > 3:       # an extra non-guide probe that informs the skip
                rc, out, dt = cexec(p[3], timeout=300)
                r.update(probe=p[3], probe_rc=rc, probe_out=out[-3000:])
        else:
            r['status'] = 'UNPLANNED'
        r['expected'] = exp
        recs.append(r)
        print(f"{ch} L{b['line']:<5} {r['status']:<22} {r.get('rc', '')!s:>4} {r.get('secs', '')!s:>6}s "
              f"{r.get('check', ''):>6} {r['first'][:60]}", flush=True)
    for fx in plan.get('post', []):
        rc, out, dt = cexec(fx[1], timeout=fx[3] if len(fx) > 3 else 900)
        recs.append(dict(line=0, kind='EXTRA', status='EXTRA' if rc == 0 else 'EXTRA-FAIL', why=fx[2], cmd=fx[1],
                         rc=rc, secs=round(dt, 1), out=out[-4000:], section='(after the chapter)'))
        print(f"{ch} post {recs[-1]['status']} rc={rc} {fx[2][:70]}", flush=True)
    return recs


def report(ch, recs, secs):
    os.makedirs(RES, exist_ok=True)
    with open(os.path.join(RES, f'{ch}.jsonl'), 'w') as f:
        for r in recs:
            f.write(json.dumps(r) + '\n')
    cnt = {}
    for r in recs:
        k = r['status'].split('-')[0] if r['status'].startswith('SKIPPED') else r['status']
        cnt[r['status']] = cnt.get(r['status'], 0) + 1
    o = [f'# {ch} in buddy-sim (raw runner output)', '',
         f'Chapter file: `guide/chapters/{CHFILES[ch]}` at repo commit {open(os.path.join(REPO, ".commit")).read().strip()}. '
         f'Run {time.strftime("%Y-%m-%d %H:%M %Z")}, {secs:.0f} s. Container `{IMAGE}`, `--network none`.', '',
         'Counts: ' + ', '.join(f'{k} {v}' for k, v in sorted(cnt.items())), '']
    for r in recs:
        o.append(f"## L{r['line']} {r['status']}  ({r.get('section', '')})")
        if r.get('first'):
            o.append(f"`{r['first']}`  host @{r.get('host')} lang {r.get('lang')}")
        for k in ('why', 'translation', 'note', 'dest', 'repo', 'vs_repo', 'live', 'vs_live'):
            if r.get(k):
                v = r[k]
                o.append(f'- {k}: ' + (v if '\n' not in v else '\n\n```\n' + v + '\n```'))
        if 'rc' in r:
            o.append(f"- rc {r['rc']}, {r.get('secs', '')} s" + (f", check lines found {r['check']}" if r.get('check') else ''))
        if r.get('check_missing'):
            o.append('- check lines not in output: ' + '; '.join(f'`{m}`' for m in r['check_missing'][:8]))
        if r.get('check_cmd'):
            o.append(f"- check command (from the Check box) rc {r['check_rc']}:\n\n```\n$ " + r['check_cmd'].strip() + '\n' +
                     '\n'.join(r['check_out'].rstrip().splitlines()[-25:]) + '\n```')
        if r.get('cmd'):
            o.append('\n```\n' + r['cmd'].strip() + '\n```')
        if r.get('out'):
            t = '\n'.join(r['out'].rstrip().splitlines()[-30:])
            o.append('\noutput (last 30 lines):\n\n```\n' + t + '\n```')
        if r.get('probe'):
            o.append(f"- probe (not a guide step) rc {r['probe_rc']}:\n\n```\n$ " + r['probe'].strip() + '\n' +
                     '\n'.join(r['probe_out'].rstrip().splitlines()[-20:]) + '\n```')
        o.append('')
    open(os.path.join(RES, f'{ch}.md'), 'w').write('\n'.join(o))


def main():
    args = sys.argv[1:]
    fresh = '--fresh' in args
    chs = [a for a in args if a.startswith('ch')]
    print(container(fresh), flush=True)
    for ch in chs:
        t = time.time()
        recs = run_chapter(ch)
        report(ch, recs, time.time() - t)
        print(f'{ch} done in {time.time() - t:.0f} s', flush=True)


if __name__ == '__main__':
    main()
