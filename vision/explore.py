"""Self-training look-around (arm only, wheels stay disabled): scan -> look at each candidate up close -> self-label.
Per cycle: run scan.py (7-stop pan scan, OWLv2, --no-tuck) and read its scan.json (kept objects + dropped candidates, each
with an xyz in base_link); then aim the camera at every candidate (Wall-E style: small anticipation, overshoot, settle),
detect again and label it `confirmed` (same label found near the image centre) or `rejected`. Everything is saved to
~/vision/dataset/<UTC>/ (frames + labels.json) as training data for teach-by-showing (#1) and fine-tuning (#9).

Aiming (measured 2026-09-29 from scan TF): camera yaw = 1.0 + 0.24 * (pan - 500) deg; camera pitch in the default arm
pose = -10.1 + 0.24 * (wrist - 178) deg; wrist clamped to 150..280 (model-checked, no contacts over the pan range).
Safety: aborts on e-stop or wheels enabled; stops if any servo > --max-temp C; ends centred, then tucks.
Run on the robot in ~/vision/venv with ROS sourced, arm recovered:  python explore.py [--cycles 1] [--max-temp 55]"""
import argparse, json, math, os, subprocess, time

import cv2
import numpy as np
import PIL.Image
import rclpy
from std_srvs.srv import Trigger

from scan import HOME_PAN, WRIST_DEFAULT, Scan, make_detector

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = ['a microphone', 'a television', 'a speaker']
DECOYS = ['a lamp', 'a tower fan']
CAM_Z = 0.33              # camera height above base_link in the default pose (model)
# floor pass (owner: cluttered room, objects change): prompts for things on the floor, no rug decoy (the rug fills the
# frame and would suppress everything). Poses model-checked clear over pans 140..860 (2026-09-29).
FLOOR = ['a shoe', 'a cable', 'a box', 'a bag', 'clothes', 'a toy', 'an object on the floor']
DRIVE = {2: 793, 3: 51, 4: 160}          # camera -29 deg: floor 0.32-4.2 m ahead
UP = {2: 500, 3: 500, 4: 500}            # straight up: the checked route into the look-down pose
LOOK_B = {2: 170, 3: 470, 4: 231}        # camera 0.28 m ahead, -61 deg: floor right in front
DEFAULT = {2: 860, 3: 45, 4: 178}


def aim_for(xyz):
    """(pan, wrist) that point the camera at a base_link point."""
    x, y, z = xyz
    yaw = math.degrees(math.atan2(y, x))
    pan = int(round(HOME_PAN + (yaw - 1.0) / 0.24))
    pitch = math.degrees(math.atan2(z - CAM_Z, math.hypot(x, y)))
    wrist = int(round(WRIST_DEFAULT + (pitch + 10.1) / 0.24))
    return max(34, min(966, pan)), max(150, min(280, wrist))


def temps(n):
    """Servo temperatures via ~/arm/read_state (read-only)."""
    c = n.create_client(Trigger, '/rosorin_board_driver/arm/read_state')
    c.wait_for_service(timeout_sec=5)
    f = c.call_async(Trigger.Request())
    n.spin_until(f.done, 20, 'read_state')
    st = json.loads(f.result().message or '{}')
    return {k: v.get('temp_c') for k, v in st.items() if isinstance(v, dict)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cycles', type=int, default=1)
    ap.add_argument('--max-temp', type=float, default=55.0)
    ap.add_argument('--th', type=float, default=0.15)
    a = ap.parse_args()
    out = os.path.join(HERE, 'dataset', time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out)
    labels = []
    for cycle in range(a.cycles):
        print(f'== cycle {cycle + 1}: scan', flush=True)
        r = subprocess.run(['python', 'scan.py', '--wrist', '260', '--no-tuck'], cwd=HERE, capture_output=True, text=True)
        saved = [l for l in r.stdout.splitlines() if l.startswith('saved ')]
        if r.returncode or not saved:
            raise SystemExit('scan failed:\n' + r.stdout[-800:] + r.stderr[-800:])
        scan = json.load(open(os.path.join(saved[-1].split(' ', 1)[1], 'scan.json')))
        cands = [dict(o, kept=True) for o in scan['objects']] + [dict(o, kept=False) for o in scan.get('dropped', [])]
        cands = [c for c in cands if 'xyz' in c]
        print(f'   {len(cands)} candidates ({len(scan["objects"])} kept by the filter)', flush=True)

        detect = make_detector('owlv2', '16', TARGETS + DECOYS, decoys=DECOYS)
        rclpy.init()
        n = Scan()
        n.spin_until(lambda: all(k in n.last for k in ('rgb', 'depth', 'info', 'scan', 'state')), 15, 'topics')
        s = n.state()
        if s['enabled'] or s['estop']:
            raise SystemExit(f'refusing: wheels enabled or e-stop: {s}')
        from std_srvs.srv import SetBool
        if not n.call('arm/enable', SetBool.Request(data=True)).success:
            raise SystemExit('arm enable refused')
        n.spin_until(lambda: (n.state() or {}).get('arm_enabled'), 5, 'arm enabled')
        try:
            cur = (n.state()['arm_pose'] or {}).get('1', HOME_PAN)
            for i, c in enumerate(cands):
                pan, wrist = aim_for(c['xyz'])
                d = 1 if pan > cur else -1
                if abs(pan - cur) > 20:                              # Wall-E: anticipation, overshoot, settle
                    n.pan(max(34, min(966, cur - 8 * d)))
                    n.pan(max(34, min(966, pan + 6 * d)), {4: wrist})
                n.pan(pan, {4: wrist})
                cur = pan
                rgb = n.last['rgb']
                img = np.frombuffer(rgb.data, np.uint8).reshape(rgb.height, rgb.width, -1)
                pil = PIL.Image.fromarray(img[:, :, ::-1] if rgb.encoding == 'bgr8' else img)
                dets = detect(pil, a.th)
                w, h = pil.size
                near = [(nm, sc) for nm, sc, b in dets if nm == c['label']
                        and abs((b[0] + b[2]) / 2 - w / 2) < 0.25 * w and abs((b[1] + b[3]) / 2 - h / 2) < 0.3 * h]
                verdict = 'confirmed' if near else 'rejected'
                fn = f'c{cycle}_{i:02d}_{c["label"].replace("a ", "").replace(" ", "_")}_{verdict}.png'
                vis = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                cv2.imwrite(os.path.join(out, fn.replace('.png', '_raw.png')), vis.copy())
                for nm, sc, b in dets:
                    x0, y0, x1, y1 = [int(v) for v in b]
                    col = (0, 200, 0) if nm == c['label'] else (120, 120, 120)
                    cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
                    cv2.putText(vis, f'{nm} {sc:.2f}', (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
                cv2.imwrite(os.path.join(out, fn), vis)
                labels.append({'cycle': cycle, 'label': c['label'], 'scan_score': c['score'], 'scan_views': c['views'],
                               'kept_by_filter': c['kept'], 'xyz': c['xyz'], 'aim': {'pan': pan, 'wrist': wrist},
                               'verdict': verdict, 'close_score': max([sc for _, sc in near], default=0.0),
                               'close_dets': [(nm, round(sc, 3)) for nm, sc, _ in dets], 'frame': fn})
                print(f'   {c["label"]:14} scan {c["score"]:.2f} x{c["views"]} {"kept" if c["kept"] else "drop"} '
                      f'-> aim pan {pan} wrist {wrist} -> {verdict}' + (f' ({near[0][1]:.2f})' if near else ''), flush=True)
                json.dump(labels, open(os.path.join(out, 'labels.json'), 'w'), indent=1)
                t = temps(n)
                if max(v for v in t.values() if v is not None) > a.max_temp:
                    print(f'   STOP: servo temperature over {a.max_temp} C: {t}', flush=True)
                    raise StopIteration
            # ---- floor pass: mid-range floor (drive pose sweep), then the floor right in front (look-down sweep)
            floor = make_detector('owlv2', '16', FLOOR)
            def floor_frame(tag, pan, pose_name):
                rgb = n.last['rgb']
                img = np.frombuffer(rgb.data, np.uint8).reshape(rgb.height, rgb.width, -1)
                pil = PIL.Image.fromarray(img[:, :, ::-1] if rgb.encoding == 'bgr8' else img)
                dets = floor(pil, a.th)
                vis = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                fn = f'c{cycle}_floor_{tag}_pan{pan}.png'
                cv2.imwrite(os.path.join(out, fn.replace('.png', '_raw.png')), vis.copy())
                for nm, sc, b in dets:
                    x0, y0, x1, y1 = [int(v) for v in b]
                    cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 160, 255), 2)
                    cv2.putText(vis, f'{nm} {sc:.2f}', (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 160, 255), 1)
                cv2.imwrite(os.path.join(out, fn), vis)
                labels.append({'cycle': cycle, 'floor': tag, 'pose': pose_name, 'pan': pan, 'frame': fn,
                               'dets': [(nm, round(sc, 3), [int(v) for v in b]) for nm, sc, b in dets]})
                json.dump(labels, open(os.path.join(out, 'labels.json'), 'w'), indent=1)
                print(f'   floor {tag} pan {pan}: ' + (', '.join(f'{nm} {sc:.2f}' for nm, sc, _ in dets[:5]) or 'nothing'), flush=True)
            for pan in (260, 380, 500, 620, 740):
                n.pan(pan, DRIVE)
                floor_frame('mid', pan, 'drive')
            n.pan(HOME_PAN, UP)
            for pan in (380, 500, 620):
                n.pan(pan, LOOK_B)
                floor_frame('near', pan, 'look_down_b')
            n.pan(HOME_PAN, UP)
            n.pan(HOME_PAN, DEFAULT)
            t = temps(n)
            print(f'   servo temps after floor pass: {t}', flush=True)
        except StopIteration:
            pass
        finally:
            try:
                n.pan(HOME_PAN, {4: WRIST_DEFAULT})
            finally:
                print('arm disable:', n.call('arm/enable', SetBool.Request(data=False)).message)
                n.destroy_node(); rclpy.shutdown()
    rclpy.init(); n = Scan()
    print('tuck:', n.call('arm/tuck', Trigger.Request()).success)
    conf = sum(l['verdict'] == 'confirmed' for l in labels)
    print(f'dataset {out}: {len(labels)} candidates, {conf} confirmed, {len(labels) - conf} rejected')
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    from mind_pause import mind_paused
    with mind_paused('explore'):              # the robot mind hands over the arm, gets it back after
        main()
