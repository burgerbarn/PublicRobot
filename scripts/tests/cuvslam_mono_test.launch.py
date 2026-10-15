from launch import LaunchDescription
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode
def generate_launch_description():
    n = ComposableNode(name="vslam_test", package="isaac_ros_visual_slam",
        plugin="nvidia::isaac_ros::visual_slam::VisualSlamNode",
        parameters=[{"num_cameras": 1, "base_frame": "base_link", "camera_optical_frames": ["depth_camera_link"],
                     "enable_imu_fusion": False, "publish_odom_to_base_tf": False, "publish_map_to_odom_tf": False,
                     "rectified_images": False, "enable_image_denoising": False, "image_jitter_threshold_ms": 200.0,
                     "verbosity": 1}],
        remappings=[("visual_slam/image_0", "/aurora/ir/image_raw"), ("visual_slam/camera_info_0", "/aurora/ir/camera_info")])
    return LaunchDescription([ComposableNodeContainer(name="vslam_container", namespace="", package="rclcpp_components",
        executable="component_container_mt", composable_node_descriptions=[n], output="screen")])
