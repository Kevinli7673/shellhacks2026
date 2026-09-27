from glob import glob
from setuptools import find_packages, setup

package_name = "rescuebot_robot"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "dashboard_bridge = rescuebot_robot.dashboard_bridge:main",
            "map_viewer = rescuebot_robot.map_viewer:main",
        ]
    },
)
