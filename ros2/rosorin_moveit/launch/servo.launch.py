"""MoveIt Servo for the rosorin arm. Start move_group first (it owns the planning scene). After launch, call
/servo_node/start_servo (std_srvs/Trigger), then stream control_msgs/JointJog on /servo_node/delta_joint_cmds."""
import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    share = get_package_share_directory('rosorin_moveit')
    urdf = os.path.join(get_package_share_directory('rosorin_base'), 'urdf', 'rosorin.urdf')
    cfg = (MoveItConfigsBuilder('rosorin', package_name='rosorin_moveit')
           .robot_description(file_path=urdf)
           .robot_description_semantic(file_path='config/rosorin.srdf')
           .robot_description_kinematics(file_path='config/kinematics.yaml')
           .joint_limits(file_path='config/joint_limits.yaml')
           .to_moveit_configs())
    servo = {'moveit_servo': yaml.safe_load(open(os.path.join(share, 'config', 'servo.yaml')))}
    return LaunchDescription([
        Node(package='moveit_servo', executable='servo_node_main', name='servo_node', output='screen',
             parameters=[servo, cfg.robot_description, cfg.robot_description_semantic,
                         cfg.robot_description_kinematics, cfg.joint_limits]),
    ])
