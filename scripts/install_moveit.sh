#!/bin/bash
# MoveIt 2 + Servo from the official ROS apt repo (2026-09-29): arm planning/named poses/smooth servoing.
# Records the exact package set added so rollback_moveit.sh removes only what this added.
set -e
before=$(mktemp); dpkg-query -W -f='${Package}\n' | sort > "$before"
sudo apt-get install -y ros-humble-moveit ros-humble-moveit-servo ros-humble-moveit-ros-perception
dpkg-query -W -f='${Package}\n' | sort | comm -13 "$before" - > ~/moveit_added_packages.txt
echo "added $(wc -l < ~/moveit_added_packages.txt) packages (list: ~/moveit_added_packages.txt)"
