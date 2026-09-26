from setuptools import find_packages, setup

package_name = "rescuebot_sim_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            "rescuebot_sim_command_bridge = rescuebot_sim_bridge.command_bridge:main",
            "rescuebot_sim_odom_tf = rescuebot_sim_bridge.odom_tf:main",
            "rescuebot_sim_autonomy_adapter = rescuebot_sim_bridge.autonomy_adapter:main",
        ],
    },
)
