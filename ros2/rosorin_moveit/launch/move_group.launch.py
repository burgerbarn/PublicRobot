"""MoveIt move_group for the rosorin arm (planning, named poses, collision checking). Executes through board_driver's
FollowJointTrajectory action (arm_controller/follow_joint_trajectory) - every safety gate of the driver still applies.
Uses rosorin_base's URDF (box collision geometry generated from the arm model, model/make_urdf.py)."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    urdf = os.path.join(get_package_share_directory('rosorin_base'), 'urdf', 'rosorin.urdf')
    cfg = (MoveItConfigsBuilder('rosorin', package_name='rosorin_moveit')
           .robot_description(file_path=urdf)
           .robot_description_semantic(file_path='config/rosorin.srdf')
           .robot_description_kinematics(file_path='config/kinematics.yaml')
           .joint_limits(file_path='config/joint_limits.yaml')
           .trajectory_execution(file_path='config/moveit_controllers.yaml')
           .planning_pipelines(pipelines=['ompl'])
           .sensors_3d(file_path='config/sensors_3d.yaml')
           .to_moveit_configs())
    return LaunchDescription([
        Node(package='moveit_ros_move_group', executable='move_group', output='screen',
             parameters=[cfg.to_dict(), {'publish_robot_description_semantic': True,
                                                 'octomap_frame': 'base_link', 'octomap_resolution': 0.02}]),
    ])
