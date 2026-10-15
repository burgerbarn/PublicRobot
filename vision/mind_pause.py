"""Hand the arm over from the robot mind (rosorin-mind.service) to a script, and back.
    with mind_paused():          # or mind_paused(reason='floor check')
        ... use the arm ...
Sets /rosorin_mind/enable false, waits until the mind reports mode 'paused' (it then has released the arm),
and on exit re-enables the mind only if it was enabled before. If the mind isn't running, it's a no-op.
Uses its own ROS context/node/executor, so it works before, after or alongside the script's own rclpy.init()."""
import contextlib, json, time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String
from std_srvs.srv import SetBool

MODE_TOPIC, ENABLE_SRV = '/rosorin_mind/mode', '/rosorin_mind/enable'


@contextlib.contextmanager
def mind_paused(reason='script', timeout=8.0):
    ctx = rclpy.Context(); rclpy.init(context=ctx)          # private context: never clashes with the script's init
    node = rclpy.create_node(f'mind_pause_{int(time.time() * 1000) % 100000}', context=ctx)
    ex = SingleThreadedExecutor(context=ctx); ex.add_node(node)
    state = {}
    node.create_subscription(String, MODE_TOPIC, lambda m: state.update(json.loads(m.data)),
                             QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
    cli = node.create_client(SetBool, ENABLE_SRV)

    def spin_until(cond, t):
        end = time.time() + t
        while time.time() < end and not cond():
            ex.spin_once(timeout_sec=0.05)
        return cond()

    running = cli.wait_for_service(timeout_sec=10.0)     # 2 s was too short on a loaded Jetson: the pause was skipped (2026-10-05)
    was_enabled = False
    try:
        if running:
            spin_until(lambda: 'mode' in state, 2.0)
            was_enabled = state.get('enabled', True)
            f = cli.call_async(SetBool.Request(data=False))
            spin_until(f.done, 5.0)
            if not spin_until(lambda: state.get('mode') == 'paused' and not state.get('have_arm'), timeout):
                raise RuntimeError(f'mind did not release the arm within {timeout} s: {state}')
            print(f'mind paused for {reason}', flush=True)
        yield
    finally:
        if running and was_enabled:
            f = cli.call_async(SetBool.Request(data=True))
            spin_until(f.done, 5.0)
            print('mind resumed', flush=True)
        ex.remove_node(node); node.destroy_node()
        rclpy.shutdown(context=ctx)
