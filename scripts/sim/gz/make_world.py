#!/usr/bin/env python3
"""Gazebo (Fortress) world for the simulated rebuild: the room as the robot mapped it (maps/room_20260928.pgm/.yaml),
walls 0.6 m high where the map is occupied, plus the robot's body at its saved home pose (maps/home_pose.json).

Robot body (sizes approximate; motion is what matters): 0.30 x 0.22 x 0.12 m box, planar velocity control on
/model/rosorin/cmd_vel (fed by the emulated board's wheel commands), a 360-degree LiDAR at the factory lidar_joint
position (0.048, 0.0, 0.107) - no yaw here, the LiDAR emulator converts to the COIN-D6's clockwise native angles - and
an RGB-D camera at the camera's mount. Usage: make_world.py <repo>/maps > room.sdf
"""
import json, math, os, sys

maps = sys.argv[1] if len(sys.argv) > 1 else 'maps'
meta = {}
for line in open(os.path.join(maps, 'room_20260928.yaml')):
    if ':' in line:
        k, v = line.split(':', 1)
        meta[k.strip()] = v.strip()
res = float(meta['resolution'])
ox, oy = [float(v) for v in meta['origin'].strip('[]').split(',')[:2]]
occ_t = float(meta['occupied_thresh'])

raw = open(os.path.join(maps, meta['image']), 'rb').read()
parts, pos = [], 0
while len(parts) < 4:                      # P5 header: magic, width, height, maxval (comments not used here)
    while raw[pos:pos + 1].isspace():
        pos += 1
    end = pos
    while not raw[end:end + 1].isspace():
        end += 1
    parts.append(raw[pos:end]); pos = end
pos += 1
assert parts[0] == b'P5'
W, H, maxv = int(parts[1]), int(parts[2]), int(parts[3])
px = raw[pos:pos + W * H]
occupied = lambda c, r: (maxv - px[r * W + c]) / maxv > occ_t      # negate 0: dark = occupied

# merge occupied cells into boxes: runs along each row, then stack identical runs on consecutive rows
runs = {}
for r in range(H):
    c = 0
    while c < W:
        if occupied(c, r):
            s = c
            while c < W and occupied(c, r):
                c += 1
            runs.setdefault((s, c), []).append(r)
        else:
            c += 1
boxes = []
for (s, e), rows in runs.items():
    start = prev = rows[0]
    for r in rows[1:] + [None]:
        if r is not None and r == prev + 1:
            prev = r
            continue
        boxes.append((s, e, start, prev))
        if r is not None:
            start = prev = r

WALL_H = 0.6
walls = []
for i, (s, e, r0, r1) in enumerate(boxes):
    sx, sy = (e - s) * res, (r1 - r0 + 1) * res
    cx = ox + (s + e) / 2 * res
    cy = oy + (H - (r0 + r1 + 1) / 2) * res
    walls.append(f'<collision name="c{i}"><pose>{cx:.3f} {cy:.3f} {WALL_H / 2} 0 0 0</pose><geometry><box><size>'
                 f'{sx:.3f} {sy:.3f} {WALL_H}</size></box></geometry></collision>'
                 f'<visual name="v{i}"><pose>{cx:.3f} {cy:.3f} {WALL_H / 2} 0 0 0</pose><geometry><box><size>'
                 f'{sx:.3f} {sy:.3f} {WALL_H}</size></box></geometry></visual>')

home = json.load(open(os.path.join(maps, 'home_pose.json')))
print(f'''<?xml version="1.0"?>
<sdf version="1.8">
 <world name="room">
  <physics name="1ms" type="ignored"><max_step_size>0.004</max_step_size><real_time_factor>1.0</real_time_factor></physics>
  <plugin filename="ignition-gazebo-physics-system" name="ignition::gazebo::systems::Physics"/>
  <plugin filename="ignition-gazebo-scene-broadcaster-system" name="ignition::gazebo::systems::SceneBroadcaster"/>
  <plugin filename="ignition-gazebo-user-commands-system" name="ignition::gazebo::systems::UserCommands"/>
  <plugin filename="ignition-gazebo-sensors-system" name="ignition::gazebo::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
  <light type="directional" name="sun"><cast_shadows>false</cast_shadows><pose>0 0 10 0 0 0</pose>
   <diffuse>0.9 0.9 0.9 1</diffuse><direction>-0.3 0.2 -0.9</direction></light>
  <model name="ground"><static>true</static><link name="l">
   <collision name="c"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry></collision>
   <visual name="v"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry></visual></link></model>
  <model name="walls"><static>true</static><link name="l">
   {"".join(walls)}
  </link></model>
  <model name="rosorin">
   <pose>{home["x"]:.3f} {home["y"]:.3f} 0.03 0 0 {home["yaw"]:.4f}</pose>
   <link name="base_link">
    <inertial><mass>4.0</mass><inertia><ixx>0.03</ixx><iyy>0.04</iyy><izz>0.05</izz></inertia></inertial>
    <collision name="c"><pose>0 0 0.06 0 0 0</pose><geometry><box><size>0.30 0.22 0.12</size></box></geometry>
     <surface><friction><ode><mu>0.0</mu><mu2>0.0</mu2></ode></friction></surface></collision>
    <visual name="v"><pose>0 0 0.06 0 0 0</pose><geometry><box><size>0.30 0.22 0.12</size></box></geometry></visual>
    <sensor name="lidar" type="gpu_lidar">
     <pose>0.048 0.0 0.107 0 0 0</pose><topic>/sim/lidar</topic><update_rate>10</update_rate>
     <lidar><scan><horizontal><samples>401</samples><resolution>1</resolution>
      <min_angle>-3.14159</min_angle><max_angle>3.14159</max_angle></horizontal></scan>
      <range><min>0.05</min><max>7.94</max><resolution>0.001</resolution></range>
      <noise><type>gaussian</type><mean>0</mean><stddev>0.006</stddev></noise></lidar>
     <always_on>1</always_on><visualize>false</visualize>
    </sensor>
    <!-- the Aurora 930 as the robot drives (arm pose "drive": camera_link 0.099 0.001 0.316 above base_footprint,
         pitched 29 deg down, model/arm_model.py); 640x400, fx 419.9 -> horizontal FOV 1.3025 rad (chapter 15) -->
    <sensor name="camera" type="rgbd_camera">
     <pose>0.099 0.001 0.291 0 0.506 0</pose><topic>/sim/camera</topic><update_rate>15</update_rate>
     <camera><horizontal_fov>1.3025</horizontal_fov><image><width>640</width><height>400</height></image>
      <clip><near>0.15</near><far>8</far></clip></camera>
     <always_on>1</always_on>
    </sensor>
   </link>
   <plugin filename="ignition-gazebo-velocity-control-system" name="ignition::gazebo::systems::VelocityControl">
    <topic>/model/rosorin/cmd_vel</topic></plugin>
   <plugin filename="ignition-gazebo-pose-publisher-system" name="ignition::gazebo::systems::PosePublisher">
    <publish_link_pose>false</publish_link_pose><publish_model_pose>true</publish_model_pose>
    <use_pose_vector_msg>false</use_pose_vector_msg><update_frequency>20</update_frequency></plugin>
  </model>
 </world>
</sdf>''')
print(f'{len(boxes)} wall boxes from {W}x{H} cells at {res} m', file=sys.stderr)
