"""The robot's own world model (owner 2026-10-02: "the robot has the capabilities to navigate its own existence").
Everything it knows, as positions on ITS room map (map frame of its live slam_toolbox map): Matt, objects, with
when they were last seen and how often. Every part of the mind reads/writes the same model; saved ~/vision/world.json.

Where: robot pose = TF map->base_link (only while its navigation/map is up - off the charger), camera direction =
TF base_link->camera_link0 + the pixel's angle, distance = LiDAR along that bearing (nearest return, 20th pct).
No pose -> nothing is added (what it knows is kept)."""
import json, math, os, time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD = os.path.join(HERE, 'world.json')
LASER_X = 0.048
MERGE_M = 0.7                                       # same label this close = the same thing (angle/range scatter)
FORGET_ONCE_S = 86400.0                             # seen once and never again for a day = noise                                     # laser in base_link (room_explorer / scan_overlay)


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def robot_pose(tf):
    """(x, y, yaw) of the robot on its own map, or None (map not up)."""
    import rclpy
    try:
        t = tf.lookup_transform('map', 'base_link', rclpy.time.Time()).transform
        return t.translation.x, t.translation.y, yaw_of(t.rotation)
    except Exception:
        return None


def camera_yaw(tf):
    import rclpy
    try:
        return yaw_of(tf.lookup_transform('base_link', 'camera_link0', rclpy.time.Time()).transform.rotation)
    except Exception:
        return None


def lidar_range(scan, bearing, half_deg=5.0, lo=0.25, hi=6.0):
    """Distance (m, from base_link) to what the LiDAR sees along bearing (base frame), or None."""
    if scan is None:
        return None
    r = np.asarray(scan.ranges, np.float32)
    ang = scan.angle_min + np.arange(len(r)) * scan.angle_increment + math.pi
    ok = np.isfinite(r) & (r > 0.12)
    lx = LASER_X + r[ok] * np.cos(ang[ok]); ly = r[ok] * np.sin(ang[ok])
    b = np.arctan2(ly, lx); d = np.hypot(lx, ly)
    sel = (np.abs(np.angle(np.exp(1j * (b - bearing)))) < math.radians(half_deg)) & (d > lo) & (d < hi)
    return float(np.percentile(d[sel], 20)) if sel.sum() >= 2 else None


def locate(tf, scan, u, K):
    """Map-frame (x, y) of what is at image column u, or None. Bearing: right of centre = negative yaw."""
    rp, cy = robot_pose(tf), camera_yaw(tf)
    if rp is None or cy is None:
        locate.why = 'no robot pose' if rp is None else 'no camera TF'; return None
    bearing = cy - math.atan((u - K[0, 2]) / K[0, 0])
    d = lidar_range(scan, bearing)
    if d is None:
        locate.why = f'no LiDAR return at bearing {math.degrees(bearing):.0f} deg'; return None
    locate.why = None
    x, y, th = rp
    return x + d * math.cos(th + bearing), y + d * math.sin(th + bearing)


def head_pan_toward(tf, xy):
    """Head pan (pulses) that looks at map point xy from where the robot is now, and the body-relative bearing (rad);
    None if the robot doesn't know where it is. pan pulse = 500 + yaw_deg / 0.24 (TF-verified sign)."""
    rp = robot_pose(tf)
    if rp is None:
        return None
    rel = math.atan2(xy[1] - rp[1], xy[0] - rp[0]) - rp[2]
    rel = math.atan2(math.sin(rel), math.cos(rel))
    return 500 + math.degrees(rel) / 0.24, rel


class World:
    def __init__(self):
        try:
            self.d = json.load(open(WORLD))
        except (OSError, ValueError):
            self.d = {'matt': None, 'objects': {}}
        self._dirty, self._saved = False, 0.0
        self.consolidate()

    def consolidate(self):
        """Merge same-label entries closer than MERGE_M (weighted by sightings); forget one-off old sightings."""
        now, before = time.time(), sum(len(v) for v in self.d['objects'].values())
        for label, lst in list(self.d['objects'].items()):
            lst = [o for o in lst if not (o['n'] <= 1 and now - o['t'] > FORGET_ONCE_S)]
            lst.sort(key=lambda o: -o['n'])
            merged = []
            for o in lst:
                for m in merged:
                    if math.dist((m['x'], m['y']), (o['x'], o['y'])) < MERGE_M:
                        tot = m['n'] + o['n']
                        m['x'] = round((m['x'] * m['n'] + o['x'] * o['n']) / tot, 2)
                        m['y'] = round((m['y'] * m['n'] + o['y'] * o['n']) / tot, 2)
                        m['n'], m['t'] = tot, max(m['t'], o['t']); m['first'] = min(m.get('first', m['t']), o.get('first', o['t']))
                        break
                else:
                    merged.append(dict(o))
            if merged:
                self.d['objects'][label] = merged
            else:
                del self.d['objects'][label]
        after = sum(len(v) for v in self.d['objects'].values())
        if after != before:
            self._dirty = True
        return before, after

    def see_matt(self, xy):
        self.d['matt'] = {'x': round(xy[0], 2), 'y': round(xy[1], 2), 't': time.time()}
        self._dirty = True

    def matt(self, max_age=None):
        m = self.d.get('matt')
        if not m or (max_age and time.time() - m['t'] > max_age):
            return None
        return m

    def see_object(self, label, xy):
        """Same label within 0.4 m = the same thing (moved a little, or seen again)."""
        lst = self.d['objects'].setdefault(label, [])
        for o in lst:
            if math.dist((o['x'], o['y']), xy) < MERGE_M:
                o['x'], o['y'] = round((o['x'] * o['n'] + xy[0]) / (o['n'] + 1), 2), round((o['y'] * o['n'] + xy[1]) / (o['n'] + 1), 2)
                o['n'], o['t'] = o['n'] + 1, time.time(); break
        else:
            lst.append({'x': round(xy[0], 2), 'y': round(xy[1], 2), 'n': 1, 't': time.time(), 'first': time.time()})
        self._dirty = True

    def known(self, label, xy, radius=0.4):
        return any(math.dist((o['x'], o['y']), xy) < radius for o in self.d['objects'].get(label, []))

    def save(self, every=10.0):
        if self._dirty and time.time() - self._saved > every:
            self.consolidate()
            json.dump(self.d, open(WORLD + '.tmp', 'w')); os.replace(WORLD + '.tmp', WORLD)
            self._dirty, self._saved = False, time.time()
