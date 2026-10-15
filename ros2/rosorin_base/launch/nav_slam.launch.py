"""Mapping run: slam_toolbox builds a new map while the robot drives (Nav2 + nvblox as in nav.launch.py,
collision_monitor = the only cmd_vel publisher). scripts/explore_run.sh saves the result as the kept map
(~/maps/room_live.yaml + .png, nav2 map_saver) - NVIDIA's Nova Carter procedure (demo_lidar_mapping). Runs on a kept
map use nav.launch.py (AMCL + Isaac ROS Occupancy Grid Localizer)."""
import json, os, tempfile

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node

MAPS = '/home/burgerbarn/maps'
LIVE = os.path.join(MAPS, 'room_live')
MASK = os.path.join(MAPS, 'room_live_keepout.yaml')


def generate_launch_description():
    share = get_package_share_directory('rosorin_base')
    params = os.path.join(share, 'config', 'nav2.yaml')
    cm_params = os.path.join(share, 'config', 'collision_monitor.yaml')
    # MAPPING run (NVIDIA Nova Carter: slam_toolbox online async while the robot drives the room; the run script saves
    # the map with nav2 map_saver). Later runs use nav.launch.py: AMCL on that saved map.
    slam_node = Node(package='slam_toolbox', executable='async_slam_toolbox_node', name='slam_toolbox', output='screen',
                     parameters=[os.path.join(share, 'config', 'slam.yaml'), {'mode': 'mapping'}])
    keepout = False                                     # a new map has no room boundary drawn yet
    practice = os.environ.get('ROSORIN_PRACTICE') == '1'
    cfg = yaml.safe_load(open(params))
    gc = cfg['global_costmap']['global_costmap']['ros__parameters']
    gc.pop('filters', None); gc.pop('keepout_filter', None)
    if practice:        # body practice (behavior/practice.py): the smoother must not clip the moves it is learning
        vs = cfg['velocity_smoother']['ros__parameters']      # about; AssistedTeleop + the collision monitor still guard
        vs['max_velocity'], vs['min_velocity'] = [0.35, 0.35, 1.0], [-0.35, -0.35, -1.0]
    else:
        from rosorin_base import learned_limits               # driving limits the robot learned from its own practice
        print('learned limits:', learned_limits.apply(cfg))
    fd, params = tempfile.mkstemp(prefix='nav2_slam_', suffix='.yaml'); os.close(fd)
    yaml.safe_dump(cfg, open(params, 'w'))
    nav = ['controller_server', 'smoother_server', 'planner_server', 'behavior_server', 'bt_navigator',
           'velocity_smoother', 'collision_monitor']
    to_nav = [('cmd_vel', 'cmd_vel_nav')]
    nodes = [
        # nvblox gets depth only while the head is still (rosorin_base/depth_gate.py: measured 2026-10-02, frames taken
        # during an arm move put ~900 false obstacle cells on the floor ahead)
        Node(package='rosorin_base', executable='depth_gate', name='depth_gate', output='screen',
             remappings=[('depth_in', '/aurora/depth/image_raw')]),
        Node(package='nvblox_ros', executable='nvblox_node', name='nvblox_node', output='screen',
             parameters=['/opt/ros/humble/share/nvblox_examples_bringup/config/nvblox/nvblox_base.yaml',
                         os.path.join(share, 'config', 'nvblox.yaml')],
             remappings=[('camera_0/depth/image', '/depth_gate/depth'),
                         ('camera_0/depth/camera_info', '/aurora/rgb/camera_info')]),
        slam_node,
        Node(package='nav2_controller', executable='controller_server', output='screen',
             parameters=[params], remappings=to_nav),
        Node(package='nav2_smoother', executable='smoother_server', name='smoother_server', output='screen',
             parameters=[params]),
        Node(package='nav2_planner', executable='planner_server', name='planner_server', output='screen',
             parameters=[params]),
        Node(package='nav2_behaviors', executable='behavior_server', name='behavior_server', output='screen',
             parameters=[params], remappings=to_nav),
        Node(package='nav2_bt_navigator', executable='bt_navigator', name='bt_navigator', output='screen',
             parameters=[params]),
        Node(package='nav2_velocity_smoother', executable='velocity_smoother', name='velocity_smoother',
             output='screen', parameters=[params], remappings=[('cmd_vel', 'cmd_vel_nav')]),
        Node(package='nav2_collision_monitor', executable='collision_monitor', name='collision_monitor',
             output='screen', parameters=[cm_params]),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_navigation',
             output='screen', parameters=[{'autostart': True, 'node_names': nav}]),
    ]
    if keepout:
        nodes[:0] = [
            Node(package='nav2_map_server', executable='map_server', name='filter_mask_server', output='screen',
                 parameters=[params, {'yaml_filename': MASK}]),
            Node(package='nav2_map_server', executable='costmap_filter_info_server', name='costmap_filter_info_server',
                 output='screen', parameters=[params]),
            Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_filters',
                 output='screen', parameters=[{'autostart': True, 'node_names': ['filter_mask_server',
                                                                                 'costmap_filter_info_server']}]),
        ]
    return LaunchDescription(nodes)
