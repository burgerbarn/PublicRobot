#!/usr/bin/python3
"""Laptop-side emulation of ssh / scp / rsync to the robot inside lightbot-sim.
The robot names map to the local user burgerbarn (/home/burgerbarn). `bigbuddy` maps to a READ-ONLY copy of
bigbuddy's ~/rosorin-vendor mounted at /bigbuddy/rosorin-vendor (commands run as uid 1001 = the owner of those files on bigbuddy, HOME=/bigbuddy; the
absolute path /home/burgerbarn/ in them is rewritten to /bigbuddy/). Any other host fails (and the container runs
with --network none)."""
import os, shlex, subprocess, sys

ROBOT = {'rosorin', 'rosorin-wifi', '192.168.1.108', '192.168.1.109', 'burgerbarn@rosorin',
         'burgerbarn@rosorin-wifi', 'burgerbarn@192.168.1.108', 'burgerbarn@192.168.1.109'}
HOME = '/home/burgerbarn'
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]


def die(msg):
    print(f'{tool} (lightbot-sim): {msg}', file=sys.stderr)
    sys.exit(255)


def remote_path(spec):
    """host:path -> local path, or None if not remote."""
    if ':' not in spec or spec.startswith('/'):
        return None
    host, path = spec.split(':', 1)
    if host not in ROBOT:
        die(f'host {host!r} is not emulated')
    if path.startswith('~/'):
        path = path[2:]
    elif path == '~':
        path = ''
    return path if path.startswith('/') else os.path.join(HOME, path)


if tool == 'ssh':
    i = 0
    while i < len(args) and args[i].startswith('-'):
        i += 2 if args[i] in ('-o', '-p', '-i', '-l', '-J', '-F') else 1
    if i >= len(args):
        die('no host')
    host, cmd = args[i], ' '.join(args[i + 1:])
    if host in ('bigbuddy', 'burgerbarn@bigbuddy'):
        if not cmd:
            die('interactive ssh is not emulated')
        cmd = cmd.replace('/home/burgerbarn/', '/bigbuddy/')
        sys.exit(subprocess.call(['sudo', '-u', 'mac', 'env', 'HOME=/bigbuddy', 'bash', '-c',
                                  f'cd /bigbuddy && {cmd}'], stdin=sys.stdin))
    if host not in ROBOT:
        die(f'host {host!r} is not emulated')
    if not cmd:
        die('interactive ssh is not emulated')
    sys.exit(subprocess.call(['sudo', '-u', 'burgerbarn', '-H', 'bash', '-c', f'cd {HOME} && {cmd}'],
                             stdin=sys.stdin))

if tool in ('scp', 'rsync'):
    out, touched = [], []
    for a in args:
        if a.startswith('-'):
            out.append(a)
            continue
        p = remote_path(a)
        if p is not None:
            touched.append(p)
            out.append(p + ('/' if a.endswith('/') and not p.endswith('/') else ''))
        else:
            out.append(a)
    if not touched:
        if tool == 'rsync':
            os.execv('/usr/bin/rsync', ['rsync'] + args)
        os.execv('/bin/cp', ['cp'] + [a for a in args if a not in ('-p', '-q')])
    if tool == 'scp':
        # scp [-r] [-p] [-q] src... dest  ->  cp -r src... dest   (as root, then chown to the owner of $HOME files)
        flags = [a for a in out if a.startswith('-')]
        files = [a for a in out if not a.startswith('-')]
        cmd = ['sudo', 'cp'] + (['-r'] if '-r' in flags else []) + files
    else:
        cmd = ['sudo', '/usr/bin/rsync', '--chown=burgerbarn:burgerbarn'] + out
    rc = subprocess.call(cmd)
    if rc == 0 and tool == 'scp':
        dest = out[-1]
        if dest.startswith(HOME):
            subprocess.call(['sudo', 'chown', '-R', 'burgerbarn:burgerbarn', dest])
            # copies from the robot to the laptop: give them to the laptop user
        else:
            subprocess.call(['sudo', 'chown', '-R', f'{os.getuid()}:{os.getgid()}', dest])
    sys.exit(rc)
die('unknown tool')
