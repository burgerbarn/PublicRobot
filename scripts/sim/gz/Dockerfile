# Simulated rebuild: the physical world (room from the kept map, the robot's body, LiDAR and depth camera).
# x86_64 on bigbuddy, GPU rendering for sensors. Stock Humble pairing: Gazebo Fortress via ros-humble-ros-gz.
FROM osrf/ros:humble-desktop
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ros-humble-ros-gz python3-numpy \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /sim
