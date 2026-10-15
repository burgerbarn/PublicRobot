from glob import glob
from setuptools import setup
setup(
    name='rosorin_moveit',
    version='0.1.0',
    packages=[],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/rosorin_moveit']),
        ('share/rosorin_moveit', ['package.xml']),
        ('share/rosorin_moveit/launch', glob('launch/*.py')),
        ('share/rosorin_moveit/config', glob('config/*')),
    ],
    install_requires=['setuptools'],
)
