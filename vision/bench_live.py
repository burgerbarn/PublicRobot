"""Compute benchmark on the live camera (no motion): NanoOWL on every new RGB frame for --seconds, with tegrastats
logged alongside. Answers "how much of the Orin does perception X use" before hardware upgrades are discussed
(owner 2026-09-29: demonstrate the actual limits first). Usage (robot, ~/vision/venv, ROS sourced):
  python bench_live.py [--model 32] [--seconds 20] [--prompts "a person,a face,a hand"]"""
import argparse, os, re, subprocess, time

import numpy as np
import PIL.Image
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

from scan import make_detector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='32', choices=['16', '32'])
    ap.add_argument('--seconds', type=float, default=20)
    ap.add_argument('--prompts', default='a person,a face,a hand,a cat,a dog')
    ap.add_argument('--th', type=float, default=0.12)
    a = ap.parse_args()
    detect = make_detector('nanoowl', a.model, a.prompts.split(','))
    rclpy.init(); n = Node('bench_live'); last = {}
    n.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: last.__setitem__('rgb', m), 2)
    while 'rgb' not in last:
        rclpy.spin_once(n, timeout_sec=0.1)
    ts = subprocess.Popen(['tegrastats', '--interval', '500'], stdout=subprocess.PIPE, text=True)
    lat, frames, seen, stamp = [], 0, {}, None
    t_end = time.time() + a.seconds
    while time.time() < t_end:
        rclpy.spin_once(n, timeout_sec=0.01)
        m = last['rgb']
        if m.header.stamp == stamp:
            continue
        stamp = m.header.stamp
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)
        pil = PIL.Image.fromarray(img[:, :, ::-1] if m.encoding == 'bgr8' else img)
        t = time.time(); dets = detect(pil, a.th); lat.append((time.time() - t) * 1000); frames += 1
        for nm, sc, _ in dets:
            seen[nm] = max(seen.get(nm, 0), sc)
    ts.terminate()
    out = ts.stdout.read()
    gpu = [int(x) for x in re.findall(r'GR3D_FREQ (\d+)%', out)]
    ram = [int(x) for x in re.findall(r'RAM (\d+)/', out)]
    cpu = [np.mean([int(c) for c in re.findall(r'(\d+)%@', blk)]) for blk in re.findall(r'CPU \[([^\]]*)\]', out)]
    pw = [int(x) for x in re.findall(r'VDD_IN (\d+)mW', out)]
    lat = np.array(lat)
    print(f'NanoOWL patch{a.model}, {len(a.prompts.split(","))} prompts: {frames} frames in {a.seconds:.0f} s = '
          f'{frames / a.seconds:.1f} fps (camera-limited if ~= camera rate) | latency ms p50 {np.median(lat):.0f} '
          f'p95 {np.percentile(lat, 95):.0f}')
    print(f'GPU busy {np.mean(gpu):.0f}% (max {max(gpu)}%) | CPU avg {np.mean(cpu):.0f}% | RAM {max(ram)} MB | '
          f'board power {np.mean(pw) / 1000:.1f} W')
    print('seen:', {k: round(v, 2) for k, v in sorted(seen.items(), key=lambda kv: -kv[1])})
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
