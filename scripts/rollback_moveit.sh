#!/bin/bash
# Undo install_moveit.sh: remove exactly the packages it added.
set -e
[ -f ~/moveit_added_packages.txt ] || { echo "no ~/moveit_added_packages.txt"; exit 1; }
sudo apt-get remove -y $(cat ~/moveit_added_packages.txt)
