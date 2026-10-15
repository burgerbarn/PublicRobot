#!/usr/bin/env python3
"""Sanity check of config/nav2.yaml before it is deployed (2026-10-02: an edit deleted max_vel_x; DWB then sampled
vx = 0 only and the robot could only strafe and turn for five runs). Exit 1 with the reason if a limit is missing or
out of range. Usage: check_nav2_config.py [path]"""
import sys

import yaml

p = sys.argv[1] if len(sys.argv) > 1 else 'ros2/rosorin_base/config/nav2.yaml'
d = yaml.safe_load(open(p))
f = d['controller_server']['ros__parameters']['FollowPath']
vs = d['velocity_smoother']['ros__parameters']
bad = []
for k, lo, hi in (('max_vel_x', 0.1, 0.5), ('max_vel_theta', 0.3, 2.0), ('max_speed_xy', 0.1, 0.5), ('acc_lim_x', 0.1, 2.5),
                  ('acc_lim_theta', 0.5, 5.0), ('vx_samples', 5, 50), ('vtheta_samples', 5, 50), ('sim_time', 0.5, 3.0)):
    if k not in f or not lo <= f[k] <= hi:
        bad.append(f'FollowPath.{k} = {f.get(k, "MISSING")} (want {lo}..{hi})')
for k in ('min_vel_x', 'min_vel_y', 'max_vel_y', 'decel_lim_x', 'critics'):
    if k not in f:
        bad.append(f'FollowPath.{k} MISSING')
if f.get('max_vel_x', 0) > vs['max_velocity'][0] + 1e-6:
    bad.append('controller max_vel_x above the velocity smoother limit')
if vs['max_velocity'][0] < 0.1 or vs['max_velocity'][2] < 0.3:
    bad.append(f'velocity_smoother max_velocity {vs["max_velocity"]}')
for cm in ('local_costmap', 'global_costmap'):
    pl = d[cm][cm]['ros__parameters']
    for layer in pl['plugins']:
        if layer not in pl:
            bad.append(f'{cm}: plugin {layer} has no parameters')
# 2026-10-02 (research fixes): the recovery tree has neither Spin nor BackUp, and any direct use of the drive
# behaviours ramps (stock 0.0 limits = velocity step)
import os, re
bt = os.path.join(os.path.dirname(os.path.abspath(p)), '..', 'bt', 'navigate_no_backup.xml')
if os.path.exists(bt):
    for tag in re.findall(r'<(Spin|BackUp|DriveOnHeading)\b', open(bt).read()):
        bad.append(f'behaviour tree contains <{tag}> (blind recovery motion; see the header of the tree file)')
b = d['behavior_server']['ros__parameters']
for name in ('backup', 'drive_on_heading'):
    if not (b.get(name, {}).get('acceleration_limit', 0) > 0 and b.get(name, {}).get('deceleration_limit', 0) < 0):
        bad.append(f'behavior_server.{name}: acceleration_limit / deceleration_limit not set (velocity step)')
if bad:
    print('nav2.yaml NOT OK:'); [print('  -', b) for b in bad]; sys.exit(1)
print('nav2.yaml ok: vx 0..%.2f, vy %.2f..%.2f, wz %.1f' % (f['max_vel_x'], f['min_vel_y'], f['max_vel_y'], f['max_vel_theta']))
