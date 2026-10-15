"""Localization + the map that updates itself as it drives (slam_toolbox continuing the kept pose graph; slam:=false = the
old map_server + AMCL) + Nav2 navigation on top of base.launch.py. ALWAYS ON: rosorin-nav.service runs this
from boot; skills are clients (ros2 run rosorin_base nav_wait, then goals). rosorin_base/nav_supervisor.py places the
robot on the map, starts the navigation servers once it has a pose, heals a half-alive stack and publishes /nav/status.
NVIDIA's Nova Carter reference setup (nova_carter_navigation/lidar_localization.launch.py, release-3.2): Nav2 AMCL on a
saved occupancy map; Isaac ROS Occupancy Grid Localizer (GPU search of the whole map with one LiDAR scan) when
/trigger_grid_search_localization is called. Its answer goes to the supervisor (/localizer/result), which checks it
against the scan before AMCL gets it (docs/research/relocalization_reference.md: the result carries no confidence).
Differs from nav2_bringup: behavior_server also publishes into cmd_vel_nav; chain is
controller/behaviors -> cmd_vel_nav -> velocity_smoother -> cmd_vel_smoothed -> collision_monitor -> cmd_vel,
so collision_monitor is the ONLY cmd_vel publisher (board_driver latches e-stop on >1).
Stop gamepad_teleop and slam first. Stop with scripts/nav_stop.sh (launch SIGINT left nodes running)."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode
import tempfile

import yaml


def generate_launch_description():
    share = get_package_share_directory('rosorin_base')
    params = os.path.join(share, 'config', 'nav2.yaml')
    cm_params = os.path.join(share, 'config', 'collision_monitor.yaml')
    default_map = '/home/burgerbarn/maps/room_live.yaml'
    map_file = next((a.split(':=', 1)[1] for a in __import__('sys').argv if a.startswith('map:=')), default_map)
    slam = next((a.split(':=', 1)[1] for a in __import__('sys').argv if a.startswith('slam:=')), 'true').lower() == 'true'
    maps = os.path.dirname(default_map)
    # the kept pose graph (~/maps/room.posegraph/.data, scripts/install_live_map.sh) is continued from where the robot
    # last knew it stood (nav_supervisor writes last_pose.json); the supervisor re-places it anyway before mapping starts
    start = [0.0, 0.0, 0.0]
    for f in ('last_pose.json', 'home_pose.json'):
        try:
            d = yaml.safe_load(open(os.path.join(maps, f))); start = [float(d['x']), float(d['y']), float(d['yaw'])]; break
        except Exception:
            continue
    mask_file = map_file[:-5] + '_keepout.yaml'
    keepout = os.path.exists(mask_file)
    cfg = yaml.safe_load(open(params))
    from rosorin_base import learned_limits               # driving limits the robot learned from its own practice
    print('learned limits:', learned_limits.apply(cfg))
    if not keepout:                                     # no room boundary drawn for this map: same params, no filter
        gc = cfg['global_costmap']['global_costmap']['ros__parameters']
        gc.pop('filters', None); gc.pop('keepout_filter', None)
    fd, params = tempfile.mkstemp(prefix='nav2_', suffix='.yaml'); os.close(fd)
    yaml.safe_dump(cfg, open(params, 'w'))
    map_yaml = LaunchConfiguration('map')
    localizer = ComposableNodeContainer(
        package='rclcpp_components', name='occupancy_grid_localizer_container', namespace='',
        executable='component_container_mt', output='screen', composable_node_descriptions=[
            ComposableNode(package='isaac_ros_occupancy_grid_localizer', name='occupancy_grid_localizer',
                           plugin='nvidia::isaac_ros::occupancy_grid_localizer::OccupancyGridLocalizerNode',
                           # min_scan_fov_degrees: NVIDIA's value is 270 (a 360 deg LiDAR). This LiDAR gives valid returns over
                           # 227 deg only (rear gap 133 deg: blind sector + arm tower, measured 2026-10-05), so with 270 the
                           # localizer silently returned nothing. Lowered to fit what the sensor really delivers.
                           parameters=[map_file, {'loc_result_frame': 'map', 'map_yaml_path': map_file,
                                                  'robot_radius': 0.18, 'min_scan_fov_degrees': 200.0}],
                           remappings=[('localization_result', '/localizer/result')]),
            ComposableNode(package='isaac_ros_pointcloud_utils', name='laserscan_to_flatscan',
                           plugin='nvidia::isaac_ros::pointcloud_utils::LaserScantoFlatScanNode')])
    loc_nodes = ([] if slam else ['map_server', 'amcl']) + (['filter_mask_server', 'costmap_filter_info_server'] if keepout else [])
    nav = ['controller_server', 'smoother_server', 'planner_server', 'behavior_server', 'bt_navigator',
           'velocity_smoother', 'collision_monitor']
    to_nav = [('cmd_vel', 'cmd_vel_nav')]
    filt = [
        Node(package='nav2_map_server', executable='map_server', name='filter_mask_server', output='screen',
             parameters=[params, {'yaml_filename': mask_file}]),
        Node(package='nav2_map_server', executable='costmap_filter_info_server', name='costmap_filter_info_server',
             output='screen', parameters=[params]),
    ] if keepout else []
    supervisor = Node(package='rosorin_base', executable='nav_supervisor', name='nav_supervisor', output='screen',
                      parameters=[{'slam': slam, 'loc_manager': bool(loc_nodes), 'map_dir': maps}])
    localization = [
        # one map, continued on every drive: slam_toolbox's localize-and-map node starts in localization mode on the
        # kept graph (pose tracked, map untouched); nav_supervisor switches it to mapping once the scan confirms the
        # pose and back when the pose is lost; it saves copies (pose graph + image) after drives. Its `pose` is
        # published as amcl_pose so every client stays as it was. docs/research/whole_project_review.md section 4.
        Node(package='slam_toolbox', executable='map_and_localization_slam_toolbox_node', name='slam_toolbox',
             output='screen', parameters=[os.path.join(share, 'config', 'slam.yaml'),
                                          {'mode': 'localization', 'map_file_name': os.path.join(maps, 'room'),
                                           'map_start_pose': start}],
             remappings=[('pose', 'amcl_pose')]),
    ] if slam else [
        Node(package='nav2_map_server', executable='map_server', name='map_server', output='screen',
             parameters=[params, {'yaml_filename': map_yaml}]),
        Node(package='nav2_amcl', executable='amcl', name='amcl', output='screen', parameters=[params]),
    ]
    loc_manager = [Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_localization',
                        output='screen', parameters=[{'autostart': True, 'node_names': loc_nodes, 'bond_timeout': 10.0}])] if loc_nodes else []
    return LaunchDescription(filt + [
        DeclareLaunchArgument('map', default_value=default_map),
        localizer,
        # 3D obstacle map from the depth camera (NVIDIA nvblox, GPU). The depth image is registered to the RGB camera
        # by the driver (its own point cloud uses the RGB intrinsics, checked 2026-10-02) -> RGB camera_info.
        # nvblox gets depth only while the head is still (rosorin_base/depth_gate.py: measured 2026-10-02, frames taken
        # during an arm move put ~900 false obstacle cells on the floor ahead)
        Node(package='rosorin_base', executable='depth_gate', name='depth_gate', output='screen',
             remappings=[('depth_in', '/aurora/depth/image_raw')]),
        Node(package='nvblox_ros', executable='nvblox_node', name='nvblox_node', output='screen',
             parameters=['/opt/ros/humble/share/nvblox_examples_bringup/config/nvblox/nvblox_base.yaml',
                         os.path.join(share, 'config', 'nvblox.yaml')],
             remappings=[('camera_0/depth/image', '/depth_gate/depth'),
                         ('camera_0/depth/camera_info', '/aurora/rgb/camera_info')]),
        *localization, *loc_manager,
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
             output='screen', parameters=[params],
             remappings=[('cmd_vel', 'cmd_vel_nav')]),                 # publishes cmd_vel_smoothed
        Node(package='nav2_collision_monitor', executable='collision_monitor', name='collision_monitor',
             output='screen', parameters=[cm_params]),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_navigation',
             # autostart off: the global costmap waits without limit for map -> base_link, so the supervisor sends
             # STARTUP once the robot has a pose. bond_timeout 10: a server must answer within half of it, and the
             # stock 4 s (2 s) is short for a loaded Jetson (docs/research/always_on_navigation_reference.md)
             output='screen', parameters=[{'autostart': False, 'node_names': nav, 'bond_timeout': 10.0}]),
        supervisor,
        RegisterEventHandler(OnProcessExit(target_action=supervisor, on_exit=[EmitEvent(event=Shutdown(
            reason='nav_supervisor exited'))])),
    ])
