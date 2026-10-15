# lightBot test robot: the software state of the robot after guide chapters 6-9 (user burgerbarn, ROS 2 Humble and
# chapter 9's package list), without the Jetson hardware. One Dockerfile, two architectures:
#   amd64 (native):        docker build -t lightbot-sim:humble ~/lightbot-sim
#   arm64 (the Jetson's,   docker build --platform linux/arm64 -t lightbot-sim:humble-arm64 ~/lightbot-sim
#          qemu-emulated)  (ros:humble is multi-arch; every package below exists for both)
FROM ros:humble
ENV DEBIAN_FRONTEND=noninteractive
# base tools + chapter 9 ("The packages later chapters need") + packages later chapters' own install steps name
# (ch13 MoveIt). Apt lists are KEPT: chapters run `apt-get install` of these packages offline (container --network none).
RUN apt-get update && apt-get install -y --no-install-recommends \
      sudo git nano rsync xxd usbutils psmisc diffutils less zstd curl gnupg ca-certificates file \
      python3-pytest python3-colcon-common-extensions ros-dev-tools python3-numpy python3-venv python3-pip \
      ros-humble-control-msgs ros-humble-robot-state-publisher ros-humble-tf2-tools ros-humble-tf2-ros \
      ffmpeg v4l-utils \
    && apt-get install -y --no-install-recommends ros-humble-robot-localization ros-humble-slam-toolbox \
      ros-humble-navigation2 ros-humble-nav2-bringup \
    && apt-get install -y ros-humble-web-video-server \
    && apt-get install -y ros-humble-moveit ros-humble-moveit-servo ros-humble-moveit-ros-perception
# offline git: the repositories chapters clone, mirrored at image build; https://github.com/ -> file:///opt/mirror/github/
RUN mkdir -p /opt/mirror/github/MAPIRlab /opt/mirror/github/ros-navigation /opt/mirror/github/robo-friends \
    && git clone -q --mirror https://github.com/MAPIRlab/rf2o_laser_odometry.git /opt/mirror/github/MAPIRlab/rf2o_laser_odometry.git \
    && git clone -q --mirror https://github.com/robo-friends/m-explore-ros2.git /opt/mirror/github/robo-friends/m-explore-ros2.git \
    && git clone -q --bare --depth 1 --branch 1.1.20 https://github.com/ros-navigation/navigation2.git /opt/mirror/github/ros-navigation/navigation2.git \
    && git config --system url."file:///opt/mirror/github/".insteadOf https://github.com/ \
    && git config --system --add safe.directory '*'
# robot user (chapter 6: burgerbarn, uid 1000, groups dialout/sudo/video) and an emulated laptop user "mac"
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && useradd -m -u 1000 -s /bin/bash -G dialout,sudo,video burgerbarn \
    && useradd -m -u 1001 -s /bin/bash mac \
    && echo 'burgerbarn ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/burgerbarn \
    && echo 'mac ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/mac && chmod 440 /etc/sudoers.d/*
# laptop emulation (ssh/scp/rsync to the robot names are local operations) and the offline apt-get shim
COPY fakebin/ /opt/fakebin/
COPY simbin/ /usr/local/sbin/
RUN chmod 755 /opt/fakebin/* /usr/local/sbin/*
# fake controller board for `run_chapter.py --fake-board` (a pty linked as /dev/ttyACM0, started as root per chapter)
COPY fakeboard/ /opt/fakeboard/
RUN chmod 755 /opt/fakeboard/fake_board.py
# fake COIN-D6 LiDAR for `run_chapter.py --fake-lidar` (a pty: /dev/ttyUSB0 -> /dev/pts/N, /dev/lidar -> ttyUSB0)
COPY fakelidar/ /opt/fakelidar/
RUN chmod 755 /opt/fakelidar/fake_lidar.py
# fake systemd for `run_chapter.py --fake-systemd` (a manager + systemctl/journalctl clients; the runner links them into
# /usr/local/sbin at container start, so without the flag the image behaves as before)
COPY fakesystemd/ /opt/fakesystemd/
RUN chmod 755 /opt/fakesystemd/fakesystemd.py
