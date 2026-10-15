# Robot vision (Jetson Orin NX 16 GB, on the robot)
Installed by `scripts/install_vision.sh` (rollback `scripts/rollback_vision.sh`) into `~/vision` on the robot.
- `grab.py` (system python + ROS 2): saves one Aurora frame -> frame_rgb.png, frame_depth.npy.
  Camera: `ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py` (not in the base service yet).
- `detect.py` (venv): NanoOWL open-vocabulary detection; `--model 16` (default) or `32`, `--prompts`, `--th`.

First results (2026-09-29, one 640x400 frame from the tuck pose, dim room):
| model | time/frame | TV | right speaker | left speaker (in shadow) | lampshade |
|---|---|---|---|---|---|
| patch32 | 28 ms | 0.54 | 0.15 | missed | mislabelled speaker 0.14 |
| patch16 + "a lamp" decoy | 115 ms | 0.53 | 0.21 | missed | "a lamp" 0.10 |
Aurora depth is missing on the TV and speakers (dark/shiny surfaces), so 3D placement needs another source
(LiDAR bearing, a second viewpoint, or depth around the box).

## Look-around scan (`scan.py`, 2026-09-29)
Robot stays put (wheels disabled); the arm pans joint 1 through 500,380,260,140,620,740,860 (camera yaw
+1 .. -85 .. +87 deg, from TF), frame + NanoOWL at each stop, then pan (and wrist) back to default, tuck.
Hits -> base_link points: direction from TF, distance from Aurora depth in the box, else the LiDAR range on
that bearing. `--wrist 260` tilts the camera to +9.6 deg (model-checked: no new contacts over pans 140-860,
wrist 150-280; default pose 178 = -10 deg, which cut off the shelves). ~1.5 min incl. recover + tuck.
Needs: `~/arm/recover` after power-up, Aurora driver running, owner with the gamepad.
Fixes found on the way: driver state is 2 Hz -> wait for fresh state after enable/goals; TF lookup at the
image stamp can land past the newest joint data -> latest TF (arm is still); a one-shot `ros2 topic pub` to
arm/goal gets lost before discovery (use a node publisher).
Second scan (robot on a ~9 cm stand, wrist 260): TV 0.64 x3 views at 1.91 m / -19 deg (LiDAR range),
speaker (right of the fireplace lamp) 0.19 x2 at 2.43 m / +1 deg (LiDAR range; hits the hearth below it),
"microphone" 0.17 x1 at 3.42 m / +97 deg (depth; small dark object high on the door-side wall, unconfirmed).
Missed: left speaker (in shadow), floor wedge monitor under the flag.

## OWLv2 (2026-09-29) - scan default
`scan.py --detector owlv2` (default): google/owlv2-base-patch16-ensemble via HF transformers, fp16, ~1.0 s/frame
incl. post-processing, load ~14 s; per-label NMS (IoU 0.3, top 3). Same tuck frame vs NanoOWL patch16: TV 0.62
(0.53), fireplace speakers 0.60 + 0.57 (0.21 + missed), mic 0.17 (missed). Enlarging a crop did NOT help
NanoOWL (blurry upscale: all scores fell). `--detector nanoowl` stays for fast passes.
Bug found with the slower detector: driver state (2 Hz) queues while detection runs; counting "fresh" messages
let old "not moving" states through mid-swing (goals sent 2.8 s into 4.5 s moves, camera angles wrong, return
cut short -> no tuck). Fixed: a move is done only when the reported commanded pose equals the goal and not moving.
Scan 20260929T090320Z (robot on a ~9 cm stand, wrist 260, owner in front of the fireplace in the centre view):
mic 0.18 at +18 deg, 3.2 m (depth), 1.45 m up = between the fireplace speakers (+28 deg 2.8 m, +1.5 deg 2.4 m;
owner confirmed that's where it is); TV 0.63 x3 at -19 deg 1.9 m; floor wedge monitor 0.61 at +91 deg (0.04 m up).
Extra "speaker"/"TV" hits (headphones, a screen right of the robot, the owner's legs at 0.46 m) - needs
per-label thresholds / view agreement. LiDAR range for raised objects hits what is below them (hearth, stool).
