"""ROSOrin base: controller board (IMU, battery, gated motion) + COIN-D6 LiDAR + static TF.
If board_driver exits for any reason: stop_motors runs immediately (stop frame), then the whole
launch shuts down; systemd ExecStopPost writes the stop frame again as a backup (motion_safety.md).
Mount geometry from the factory URDF (reference data, docs/hardware.md)."""
import os

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, RegisterEventHandler, Shutdown
from launch.event_handlers import OnProcessExit
from launch_ros.actions import Node


def static_tf(name, xyz, rpy, parent, child):
    return Node(package='tf2_ros', executable='static_transform_publisher', name=name,
                arguments=['--x', xyz[0], '--y', xyz[1], '--z', xyz[2],
                           '--roll', rpy[0], '--pitch', rpy[1], '--yaw', rpy[2],
                           '--frame-id', parent, '--child-frame-id', child])


def generate_launch_description():
    share = get_package_share_directory('rosorin_base')
    calib = os.path.join(share, 'config', 'imu_calibration.yaml')
    arm_limits = os.path.join(share, 'config', 'arm_limits.yaml')
    board = Node(package='rosorin_base', executable='board_driver', name='rosorin_board_driver',
                 parameters=[{'device': '/dev/ttyACM0', 'imu_frame_id': 'imu_link'}, calib, arm_limits])
    urdf = open(os.path.join(share, 'urdf', 'rosorin.urdf')).read()   # generated: model/make_urdf.py
    stop_motors = os.path.join(get_package_prefix('rosorin_base'), 'lib', 'rosorin_base', 'stop_motors')
    on_board_exit = RegisterEventHandler(OnProcessExit(
        target_action=board,
        on_exit=[ExecuteProcess(cmd=[stop_motors], output='screen', name='stop_motors',
                                on_exit=Shutdown(reason='board_driver exited'))]))
    return LaunchDescription([
        board,
        on_board_exit,
        # arm + camera frames from /joint_states (board_driver); root base_link
        Node(package='robot_state_publisher', executable='robot_state_publisher', name='robot_state_publisher',
             parameters=[{'robot_description': urdf}]),
        Node(package='robot_localization', executable='ekf_node', name='ekf_filter_node',
             parameters=[os.path.join(share, 'config', 'ekf.yaml')]),
        Node(package='rosorin_base', executable='lidar_reader', name='rosorin_lidar_reader',
             respawn=True, respawn_delay=2.0,     # a crashed reader comes back by itself (the scan gate holds the wheels)
             parameters=[{'device': '/dev/lidar', 'frame_id': 'laser',
                          'clockwise': True}]),   # native angles run CW: floor test 2026-09-28, docs/hardware.md
        # factory pro/lidar.urdf.xacro lidar_joint; native angles published as-is, mount yaw pi
        static_tf('tf_base_laser', ['0.048156', '0.0000756', '0.106748'], ['0', '0', '3.14159265'],
                  'base_link', 'laser'),
        # LiDAR odometry (2026-09-30): rf2o -> rf2o_fix (sign-corrected) -> odom_lidar = the EKF's motion input
        Node(package='rf2o_laser_odometry', executable='rf2o_laser_odometry_node', name='rf2o_laser_odometry',
             parameters=[os.path.join(share, 'config', 'rf2o.yaml')], arguments=['--ros-args', '--log-level', 'warn']),
        Node(package='rosorin_base', executable='rf2o_fix', name='rf2o_fix'),
        # factory pro/imu.urdf.xacro imu_joint
        static_tf('tf_base_imu', ['-0.027258', '-0.0000564', '0.048046'], ['0', '-0.0087266', '1.5707'],
                  'base_link', 'imu_link'),
    ])
