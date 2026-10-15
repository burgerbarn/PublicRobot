"""Attention loop v2 (arm only, wheels untouched): the head always looks at something for a reason, and GLIDES.
Motion = joint velocities on arm_controller/joint_jog (board_driver integrates at 25 Hz with an acceleration limit,
limits + arm-model veto per step, glides to a stop when commands stop) - no goal-by-goal stepping (v1 was jerky).
Perception = NanoOWL patch32 on every camera frame (~14 fps, ~50 % GPU; vision/bench_live.py).
Target = the owner as ONE entity: a face inside/near a person box is the same "you" (v1 flipped face<->person every
frame). Aim point: the face centre if seen, else the top 15 % of the person box, else a hand. Kept LOST_S after it
disappears (hysteresis), then curiosity: slow deliberate looks around with pauses.
Tracking = proportional: joint velocity = GAIN x angle error (camera intrinsics), dead zone, capped.
Signs (measured): target right -> joint1 +rad (pan pulse down); target below -> joint4 +rad (wrist pulse down).
Self-training log ~/vision/attention/<UTC>/events.jsonl + a frame per new acquisition.
Usage (robot, ~/vision/venv, ROS sourced, wheels disabled): python attention.py [--seconds 120]"""
import argparse, json, math, os, random, time

import cv2
import numpy as np
import PIL.Image
import rclpy
from control_msgs.msg import JointJog
from std_srvs.srv import SetBool, Trigger

from scan import Scan, make_detector

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = ['a face', 'a person', 'a hand']
TH = {'a face': 0.2, 'a person': 0.25, 'a hand': 0.22}
# everyday objects: give the robot's thinking something to be curious about (2026-09-30: it only ever saw 'nothing specific')
OBJECTS = ['a cup', 'a phone', 'a bag', 'a box', 'a cable', 'a shoe', 'a guitar', 'a laptop']
TH.update({o: 0.2 for o in OBJECTS})
GAIN, DEAD_DEG, VMAX = 1.8, 1.5, 1.25       # 1/s, deg, rad/s (owner 2026-10-01: head faster; was 1.6, 0.7; 2.5 overshot +-18 deg)
LOST_S, LOOK_S, SWEEP_V = 1.5, 2.5, 0.25    # s, s, rad/s


def aim_point(dets):
    """(u, v, what) for the owner-as-one-entity, or None."""
    faces = [d for d in dets if d[0] == 'a face']
    people = [d for d in dets if d[0] == 'a person']
    hands = [d for d in dets if d[0] == 'a hand']
    if faces:
        _, _, (x0, y0, x1, y1) = max(faces, key=lambda d: d[1])
        return (x0 + x1) / 2, (y0 + y1) / 2, 'face'
    if people:
        _, _, (x0, y0, x1, y1) = max(people, key=lambda d: d[1])
        return (x0 + x1) / 2, y0 + 0.15 * (y1 - y0), 'person'
    if hands:
        _, _, (x0, y0, x1, y1) = max(hands, key=lambda d: d[1])
        return (x0 + x1) / 2, (y0 + y1) / 2, 'hand'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=120)
    a = ap.parse_args()
    out = os.path.join(HERE, 'attention', time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out)
    log = open(os.path.join(out, 'events.jsonl'), 'w')
    detect = make_detector('nanoowl', '32', PROMPTS)
    rclpy.init()
    n = Scan()
    jog = n.create_publisher(JointJog, 'arm_controller/joint_jog', 10)
    n.spin_until(lambda: all(k in n.last for k in ('rgb', 'info', 'state')), 15, 'topics')
    s = n.state()
    if s['enabled'] or s['estop']:
        raise SystemExit(f'refusing: wheels enabled or e-stop: {s}')
    if not s.get('arm_holding'):
        n.cli['arm/recover'] = n.create_client(Trigger, '/rosorin_board_driver/arm/recover')
        print('recover:', n.call('arm/recover', Trigger.Request()).success, flush=True)
    r = n.call('arm/enable', SetBool.Request(data=True))
    if not r.success:
        raise SystemExit('arm enable refused: ' + r.message)
    n.spin_until(lambda: (n.state() or {}).get('arm_enabled'), 5, 'arm enabled in driver state')
    K = np.array(n.last['info'].k).reshape(3, 3)

    def send(v1, v4):
        m = JointJog(joint_names=['joint1', 'joint4'], velocities=[float(v1), float(v4)])
        m.header.stamp = n.get_clock().now().to_msg()
        jog.publish(m)

    mode, last_seen, what, stamp, frames = 'curious', 0.0, None, None, 0
    sweep_dir, sweep_until, pause_until = random.choice([-1, 1]), 0.0, 0.0
    t_end = time.time() + a.seconds
    try:
        while time.time() < t_end:
            rclpy.spin_once(n, timeout_sec=0.005)
            st = n.state() or {}
            if st.get('estop') or st.get('enabled') or not st.get('arm_enabled'):
                print('stop:', st.get('estop') or ('wheels enabled' if st.get('enabled') else 'arm disabled'), flush=True)
                break
            m = n.last['rgb']
            if m.header.stamp == stamp:
                continue
            stamp = m.header.stamp; frames += 1
            img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)
            rgb = img[:, :, ::-1] if m.encoding == 'bgr8' else img
            dets = [d for d in detect(PIL.Image.fromarray(rgb), 0.12) if d[1] >= TH[d[0]]]
            now = time.time()
            ap_ = aim_point(dets)
            if ap_:
                u, v, w = ap_
                ex = math.atan((u - K[0, 2]) / K[0, 0]); ey = math.atan((v - K[1, 2]) / K[1, 1])
                if mode != 'track':
                    fn = f'{frames:05d}_{w}.jpg'
                    cv2.imwrite(os.path.join(out, fn), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                    print(f'{time.strftime("%H:%M:%S")} found you ({w})', flush=True)
                    log.write(json.dumps({'t': now, 'event': 'acquire', 'what': w, 'frame': fn,
                                          'dets': [(d[0], round(d[1], 3)) for d in dets]}) + '\n')
                mode, last_seen, what = 'track', now, w
                v1 = 0.0 if abs(math.degrees(ex)) < DEAD_DEG else max(-VMAX, min(VMAX, GAIN * ex))
                v4 = 0.0 if abs(math.degrees(ey)) < DEAD_DEG else max(-VMAX, min(VMAX, GAIN * ey))
                send(v1, v4)
                log.write(json.dumps({'t': now, 'event': 'track', 'what': w, 'err_deg': [round(math.degrees(ex), 1),
                                      round(math.degrees(ey), 1)], 'vel': [round(v1, 3), round(v4, 3)],
                                      'pose': st.get('arm_pose')}) + '\n')
            elif mode == 'track' and now - last_seen < LOST_S:
                send(0.0, 0.0)                                 # brief loss: hold still, wait for it
            else:
                if mode == 'track':
                    print(f'{time.strftime("%H:%M:%S")} lost you -> curious', flush=True)
                    log.write(json.dumps({'t': now, 'event': 'lost', 'what': what}) + '\n')
                    mode, pause_until, sweep_until = 'curious', now + 1.0, 0.0
                # curiosity: a slow deliberate turn, then a pause to look; reverse near the pan ends
                pan = (st.get('arm_pose') or {}).get('1', 500)
                if pan < 200: sweep_dir = -1                   # low pan pulse = far right -> turn back left
                if pan > 800: sweep_dir = 1
                if now < pause_until:
                    send(0.0, 0.0)
                elif now < sweep_until:
                    send(sweep_dir * SWEEP_V, 0.0)
                else:
                    sweep_until = now + random.uniform(1.0, 2.5)
                    pause_until = sweep_until + LOOK_S
                    if random.random() < 0.3:
                        sweep_dir = -sweep_dir
                    log.write(json.dumps({'t': now, 'event': 'look_around', 'dir': sweep_dir, 'pan': pan}) + '\n')
            log.flush()
    finally:
        send(0.0, 0.0)
        time.sleep(0.5)
        n.call('arm/enable', SetBool.Request(data=False))
        print(f'saved {out}: {frames} frames', flush=True)
        n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    from mind_pause import mind_paused
    with mind_paused('attention test'):              # the robot mind hands over the arm, gets it back after
        main()
