"""Online async SLAM (slam_toolbox, apt) on top of base.launch.py (scan + EKF odom->base_link).
Publishes map->odom and /map. Not started by rosorin-base.service: run on demand."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('rosorin_base')
    return LaunchDescription([
        Node(package='slam_toolbox', executable='async_slam_toolbox_node', name='slam_toolbox',
             output='screen', parameters=[os.path.join(share, 'config', 'slam.yaml')]),
    ])
