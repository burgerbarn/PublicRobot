"""Look-around scan: the robot stays put (wheels disabled), the arm pans joint 1 (base yaw) through a list of
positions; at each stop one Aurora frame is run through NanoOWL. Each hit becomes a point in base_link:
direction from TF (joint_states -> robot_state_publisher, so it follows the real pan), distance from the Aurora
depth inside the box, else the LiDAR range along that direction. Hits of the same label within MERGE_M merge.

Preconditions (checked): board_driver running, wheels disabled, no e-stop, arm pose inside limits (run
~/arm/recover after a power cycle). Joint 1 range [34, 966] was mapped from the default pose (motion_safety.md).
The owner watches with the gamepad (any button = e-stop). At the end: pan back to the start, arm disabled
(holding), then ~/arm/tuck unless --no-tuck.

Run on the robot, ROS sourced, in ~/vision/venv, Aurora driver running:
  python scan.py [--pans 500,380,260,140,620,740,860] [--targets "a microphone,a television,a speaker"]
                 [--decoys "a lamp"] [--th 0.12] [--model 16] [--no-tuck]
Writes ~/vision/scans/<UTC time>/: view_<pan>.png (boxes), scan.json, top.png (top-down, LiDAR + hits)."""
import argparse, json, math, os, time

import cv2
import numpy as np
import PIL.Image
import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image, JointState, LaserScan
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformListener
from scan_filter import filter_objects

HERE = os.path.dirname(os.path.abspath(__file__))
DRV = '/rosorin_board_driver'
MERGE_M = 0.5                     # same label within this distance (base_link, xy) = same object
SETTLE_S = 1.5                    # after the pan stops, before a frame counts
HOME_PAN = 500                    # joint 1 centre; every scan ends here
WRIST_DEFAULT = 178               # joint 4 in the default pose (arm_rest_4); restored before tuck


def quat_to_R(q):
    x, y, z, w = q.x, q.y, q.z, q.w
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def make_detector(kind, model, text, iou=0.3, per_label=3, decoys=()):
    """-> detect(PIL image, threshold) = [(label, score, [x0, y0, x1, y1]), ...], per-label NMS, best first.
    owlv2 (default): google/owlv2-base-patch16-ensemble, HF transformers fp16, ~0.64 s/frame on the Orin, much
    better on small objects (2026-09-29, same frame: speakers 0.60/0.57, mic 0.17 vs NanoOWL 0.21/missed/missed).
    nanoowl: OWL-ViT + TensorRT, ~0.12 s/frame."""
    import torch
    from torchvision.ops import box_iou, nms
    if kind == 'owlv2':
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        name = 'google/owlv2-base-patch16-ensemble'
        proc = Owlv2Processor.from_pretrained(name)
        net = Owlv2ForObjectDetection.from_pretrained(name, torch_dtype=torch.float16).cuda().eval()

        def raw(img, th):
            inp = proc(text=[text], images=img, return_tensors='pt').to('cuda')
            inp['pixel_values'] = inp['pixel_values'].half()
            with torch.no_grad():
                out = net(**inp)
            s = max(img.size)                                 # OWLv2 pads to a square (top-left)
            # same maths as proc.post_process_object_detection (sigmoid of the best query logit, cxcywh -> xyxy
            # scaled to the padded square), done here so each box keeps its class embedding ("fingerprint")
            scores, labels = torch.sigmoid(out.logits[0].float()).max(-1)
            keep = scores > th
            b = out.pred_boxes[0][keep].float()
            boxes = torch.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2,
                                 b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1) * s
            emb = torch.nn.functional.normalize(out.class_embeds[0][keep].float(), dim=-1)
            return labels[keep], scores[keep], boxes, emb
    else:
        from nanoowl.owl_predictor import OwlPredictor
        pred = OwlPredictor(f'google/owlvit-base-patch{model}',
                            image_encoder_engine=os.path.join(HERE, 'engines', f'owl_image_encoder_patch{model}.engine'))
        st = {'enc': pred.encode_text(text)}

        def raw(img, th):
            o = pred.predict(image=img, text=text, text_encodings=st['enc'], threshold=th, pad_square=False)
            return o.labels, o.scores.float(), o.boxes.float(), None

    def detect(img, th, with_embeds=False):
        """with_embeds: 4-tuples (label, score, box, embedding) - owlv2 only (embedding None for nanoowl)."""
        labels, scores, boxes, emb = raw(img, th)
        out = []
        for li in set(labels.tolist()):
            idx = torch.nonzero(labels == li).squeeze(1)
            keep = nms(boxes[idx].cpu(), scores[idx].cpu(), iou)[:per_label]
            out += [(text[li], float(scores[idx[k]]), boxes[idx[k]].tolist(),
                     emb[idx[k]].cpu() if emb is not None else None) for k in keep]
        # a target box that mostly overlaps a stronger decoy box (lamp, tower fan) is that decoy, not the target
        dec = [d for d in out if d[0] in decoys]
        if dec:
            db = torch.tensor([d[2] for d in dec])
            out = [d for d in out if d[0] in decoys or not any(
                s >= d[1] and v >= 0.5 for s, v in zip((x[1] for x in dec), box_iou(torch.tensor([d[2]]), db)[0].tolist()))]
        out = sorted(out, key=lambda d: -d[1])
        return out if with_embeds else [d[:3] for d in out]

    def add_labels(new):
        """Teach new object names live (owner 2026-10-01: learn the moment the owner names something)."""
        new = [t for t in new if t and t not in text]
        if new:
            text.extend(new)
            if kind != 'owlv2':
                st['enc'] = pred.encode_text(text)
        return new
    detect.add_labels = add_labels
    return detect


class Scan(Node):
    def __init__(self):
        super().__init__('look_around_scan')
        self.last, self.n_state = {}, 0
        for k, t, T in (('rgb', '/aurora/rgb/image_raw', Image), ('depth', '/aurora/depth/image_raw', Image),
                        ('info', '/aurora/rgb/camera_info', CameraInfo), ('scan', '/scan', LaserScan)):
            self.create_subscription(T, t, lambda m, k=k: self.last.__setitem__(k, m), 5)
        self.create_subscription(String, f'{DRV}/state', self.on_state, 5)   # 2 Hz
        self.goal_pub = self.create_publisher(JointState, 'arm/goal', 10)
        self.tf = Buffer(cache_time=Duration(seconds=30)); TransformListener(self.tf, self)
        self.cli = {n: self.create_client(T, f'{DRV}/{n}') for n, T in
                    (('arm/enable', SetBool), ('arm/tuck', Trigger))}

    def spin_until(self, cond, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.05)
            if cond():
                return
        raise RuntimeError(f'timeout waiting for {what}')

    def call(self, name, req):
        c = self.cli[name]
        if not c.wait_for_service(timeout_sec=5):
            raise RuntimeError(f'service {name} not available')
        f = c.call_async(req)
        self.spin_until(f.done, 60, name)
        return f.result()

    def on_state(self, m):
        self.last['state'] = m; self.n_state += 1

    def state(self):
        return json.loads(self.last['state'].data) if 'state' in self.last else None

    def fresh_state(self, count=2):
        """Wait for `count` state messages published after now (the topic is 2 Hz; older ones are stale)."""
        n0 = self.n_state
        self.spin_until(lambda: self.n_state >= n0 + count, 5, 'fresh driver state')
        return self.state()

    def pan(self, pulse, extra=None):
        """Move joint 1 to `pulse` (plus any `extra` {servo id: pulse}), wait until still, then for fresh frames."""
        goal = {1: pulse, **(extra or {})}
        self.goal_pub.publish(JointState(name=[str(k) for k in goal], position=[float(v) for v in goal.values()]))
        t0 = time.time()
        # done = driver reports the commanded pose AT the goal and not moving. (Counting "fresh" messages was
        # wrong: state msgs queue up while detection runs, and old ones said "not moving" mid-swing.)
        def arrived():
            s = self.state()
            p = (s or {}).get('arm_pose') or {}
            return s and not s['arm_moving'] and all(abs(p.get(str(k), -999) - v) <= 1 for k, v in goal.items())
        self.spin_until(lambda: arrived() or (self.state() or {}).get('estop'), 60, f'pan to {pulse}')
        s = self.state()
        if s['estop'] or not s['arm_enabled']:
            raise RuntimeError(f'arm stopped: {s}')
        still = self.get_clock().now() + Duration(seconds=SETTLE_S)
        self.spin_until(lambda: 'rgb' in self.last and Time.from_msg(self.last['rgb'].header.stamp) > still
                        and 'depth' in self.last and Time.from_msg(self.last['depth'].header.stamp) > still
                        and Time.from_msg(self.last['scan'].header.stamp) > still, 10, 'fresh frames')
        return time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pans', default='500,380,260,140,620,740,860')
    ap.add_argument('--targets', default='a microphone,a television,a speaker')
    ap.add_argument('--decoys', default='a lamp,a tower fan')
    ap.add_argument('--th', type=float, default=0.15)
    ap.add_argument('--detector', default='owlv2', choices=['owlv2', 'nanoowl'])
    ap.add_argument('--model', default='16', choices=['16', '32'], help='nanoowl only')
    ap.add_argument('--no-tuck', action='store_true')
    ap.add_argument('--wrist', type=int, default=None,
                    help='joint 4 during the scan (model: +pulses tilt up 0.24 deg/p; 260 = camera +9.6 deg, '
                         'collision-free over pans 140-860; default pose 178 = -10 deg)')
    a = ap.parse_args()
    pans = [int(p) for p in a.pans.split(',')]
    assert all(34 <= p <= 966 for p in pans), 'pan outside the mapped joint-1 range [34, 966]'
    targets = [t.strip() for t in a.targets.split(',') if t.strip()]
    text = targets + [d.strip() for d in a.decoys.split(',') if d.strip()]

    detect = make_detector(a.detector, a.model, text, decoys=[d.strip() for d in a.decoys.split(',') if d.strip()] )

    out_dir = os.path.join(HERE, 'scans', time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out_dir)
    rclpy.init()
    n = Scan()
    n.spin_until(lambda: all(k in n.last for k in ('rgb', 'depth', 'info', 'scan', 'state')), 15, 'topics')
    s = n.state()
    if s['enabled'] or s['estop']:
        raise SystemExit(f'refusing: wheels enabled or e-stop latched: {s}')
    r = n.call('arm/enable', SetBool.Request(data=True))
    print('arm enable:', r.message)
    if not r.success:
        raise SystemExit('arm enable refused')
    try:
        n.spin_until(lambda: (n.state() or {}).get('arm_enabled'), 5, 'driver state: arm enabled')
    except RuntimeError:
        raise SystemExit(f'arm not enabled per driver state: {n.state()}')
    s = n.state()
    print('start pan:', (s['arm_pose'] or {}).get('1'))
    wrist_rest = WRIST_DEFAULT
    assert a.wrist is None or 150 <= a.wrist <= 280, 'wrist outside the model-checked range 150..280'
    hits, views = [], []
    try:
        for i, p in enumerate(pans):
            dt = n.pan(p, {4: a.wrist} if a.wrist is not None and i == 0 else None)
            rgb, dep, info, scan = n.last['rgb'], n.last['depth'], n.last['info'], n.last['scan']
            img = np.frombuffer(rgb.data, np.uint8).reshape(rgb.height, rgb.width, -1)
            depth = np.frombuffer(dep.data, np.uint16).reshape(dep.height, dep.width)
            # arm is still (settled) -> latest TF is exact; stamp lookups can land a hair past the newest joint data
            tf_cam = n.tf.lookup_transform('base_link', rgb.header.frame_id, Time(), Duration(seconds=1))
            tf_las = n.tf.lookup_transform('base_link', scan.header.frame_id, Time(), Duration(seconds=1))
            Rc, tc = quat_to_R(tf_cam.transform.rotation), np.array([getattr(tf_cam.transform.translation, k) for k in 'xyz'])
            Rl, tl = quat_to_R(tf_las.transform.rotation), np.array([getattr(tf_las.transform.translation, k) for k in 'xyz'])
            K = np.array(info.k).reshape(3, 3)
            t = time.time()
            dets = detect(PIL.Image.fromarray(img[:, :, ::-1] if rgb.encoding == 'bgr8' else img), a.th)
            det_ms = (time.time() - t) * 1000
            vis = img.copy() if rgb.encoding == 'bgr8' else img[:, :, ::-1].copy()
            fwd = Rc @ np.array([0, 0, 1.0])
            views.append({'pan': p, 'cam_yaw_deg': round(math.degrees(math.atan2(fwd[1], fwd[0])), 1),
                          'move_s': round(dt, 1), 'detect_ms': round(det_ms)})
            for name, sc, box in dets:
                x0, y0, x1, y1 = [int(round(v)) for v in box]
                col = (0, 200, 255) if name in targets else (120, 120, 120)
                cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
                cv2.putText(vis, f'{name} {sc:.2f}', (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
                if name not in targets:
                    continue
                u, v = (x0 + x1) / 2, (y0 + y1) / 2
                ray_c = np.array([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], 1.0])
                ray_b = Rc @ ray_c                                   # base_link, not normalised (z_cam = 1)
                roi = depth[max(0, y0 + (y1 - y0) // 4):y1 - (y1 - y0) // 4, max(0, x0 + (x1 - x0) // 4):x1 - (x1 - x0) // 4]
                valid = roi[roi > 0]
                src, pt = None, None
                if valid.size >= 0.2 * max(1, roi.size):
                    pt, src = tc + ray_b * (np.median(valid) / 1000.0), 'depth'  # depth = z in the camera frame
                else:                                                # LiDAR along the same horizontal bearing
                    d = ray_b / np.linalg.norm(ray_b)
                    d_l = Rl.T @ d                                   # direction in the laser frame
                    ang = math.atan2(d_l[1], d_l[0])
                    i = int(round((ang - scan.angle_min) / scan.angle_increment)) % len(scan.ranges)
                    rr = [scan.ranges[(i + k) % len(scan.ranges)] for k in range(-2, 3)]
                    rr = [x for x in rr if scan.range_min < x < scan.range_max]
                    if rr:
                        hor = min(rr)
                        dxy = math.hypot(d[0], d[1])
                        pt = tl + np.array([d[0], d[1], 0]) / dxy * hor
                        pt[2] = tc[2] + d[2] / dxy * math.hypot(*(pt[:2] - tc[:2]))   # height along the ray
                        src = 'lidar'
                h = {'label': name, 'score': round(sc, 3), 'pan': p, 'box': [x0, y0, x1, y1],
                     'bearing_deg': round(math.degrees(math.atan2(ray_b[1], ray_b[0])), 1), 'range_src': src}
                if pt is not None:
                    h['xyz'] = [round(float(c), 3) for c in pt]
                    h['dist_m'] = round(float(math.hypot(pt[0], pt[1])), 2)
                hits.append(h)
                print(f"pan {p}: {name} {sc:.2f} bearing {h['bearing_deg']} deg"
                      + (f" at {h['dist_m']} m ({src})" if pt is not None else ' (no range)'))
            cv2.imwrite(os.path.join(out_dir, f'view_{p}.png'), vis)
            print(f'pan {p}: camera yaw {views[-1]["cam_yaw_deg"]} deg, {det_ms:.0f} ms')
    finally:
        centred = False
        try:                                          # always back to centre (all named poses use 1 = 500)
            n.pan(HOME_PAN, {4: wrist_rest} if a.wrist is not None and wrist_rest is not None else None)
            centred = abs(((n.state() or {}).get('arm_pose') or {}).get('1', -999) - HOME_PAN) <= 3
            print('back to centre:', centred)
        except Exception as e:
            print('return to centre FAILED:', e)
        print('arm disable:', n.call('arm/enable', SetBool.Request(data=False)).message)
    if not a.no_tuck and centred:                     # tuck holds the base where it is -> only when centred
        r = n.call('arm/tuck', Trigger.Request())
        print('tuck:', r.success)

    objs = []                                       # merge hits of the same label that are close together
    for h in sorted([h for h in hits if 'xyz' in h], key=lambda h: -h['score']):
        for o in objs:
            if o['label'] == h['label'] and math.dist(o['xyz'][:2], h['xyz'][:2]) < MERGE_M:
                o['views'] += 1; o['score'] = max(o['score'], h['score']); break
        else:
            objs.append({'label': h['label'], 'xyz': h['xyz'], 'dist_m': h['dist_m'], 'bearing_deg': h['bearing_deg'],
                         'score': h['score'], 'views': 1, 'range_src': h['range_src']})
    objs, dropped = filter_objects(objs)          # per-label score floor + min views (vision/scan_filter.py)
    json.dump({'views': views, 'hits': hits, 'objects': objs, 'dropped': dropped}, open(os.path.join(out_dir, 'scan.json'), 'w'), indent=1)

    S, px = 800, 100                                # top-down: 8 m x 8 m, robot in the middle, x (forward) up
    top = np.full((S, S, 3), 255, np.uint8)
    to_px = lambda x, y: (int(S / 2 - y * px), int(S / 2 - x * px))
    scan = n.last['scan']
    tf_las = n.tf.lookup_transform('base_link', scan.header.frame_id, Time(), Duration(seconds=1))
    Rl, tl = quat_to_R(tf_las.transform.rotation), np.array([getattr(tf_las.transform.translation, k) for k in 'xyz'])
    for i, r in enumerate(scan.ranges):
        if scan.range_min < r < scan.range_max:
            ang = scan.angle_min + i * scan.angle_increment
            p = Rl @ np.array([r * math.cos(ang), r * math.sin(ang), 0]) + tl
            cv2.circle(top, to_px(p[0], p[1]), 1, (150, 150, 150), -1)
    cv2.rectangle(top, to_px(0.15, 0.12), to_px(-0.15, -0.12), (0, 0, 0), 2)
    cv2.arrowedLine(top, to_px(0, 0), to_px(0.4, 0), (0, 0, 0), 2)
    colors = {t: c for t, c in zip(targets, [(0, 0, 255), (255, 120, 0), (0, 160, 0), (160, 0, 160), (0, 160, 160)])}
    for o in objs:
        c = colors.get(o['label'], (0, 0, 0))
        cv2.circle(top, to_px(*o['xyz'][:2]), 8, c, 2)
        cv2.putText(top, f"{o['label'].replace('a ', '')} {o['score']:.2f} x{o['views']}", to_px(o['xyz'][0] + 0.12, o['xyz'][1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 1)
    cv2.imwrite(os.path.join(out_dir, 'top.png'), top)
    print('objects:', json.dumps(objs))
    print('saved', out_dir)
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    from mind_pause import mind_paused
    with mind_paused('look-around scan'):              # the robot mind hands over the arm, gets it back after
        main()
