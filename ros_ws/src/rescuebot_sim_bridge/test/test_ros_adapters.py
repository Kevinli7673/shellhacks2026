"""Exercise the real ROS adapter entrypoints on isolated topics and sockets."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from geometry_msgs.msg import Twist
import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from rescuebot.bridge_ipc import DatagramSender
from rescuebot.simulation_ipc import SimulationCommand


def test_command_entrypoint_accepts_ros_remapping_and_stops_on_expiry(tmp_path):
    context = Context()
    rclpy.init(context=context)
    node = Node("adapter_probe", context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(node)
    received = []
    topic = f"/adapter_test_{os.getpid()}/cmd_vel"
    node.create_subscription(Twist, topic, received.append, 10)
    socket = tmp_path / "command.sock"
    sender = DatagramSender(socket)
    process = subprocess.Popen([
        sys.executable, "-m", "rescuebot_sim_bridge.command_bridge",
        "--socket", str(socket), "--ros-args",
        "-r", "__node:=command_entrypoint_test", "-r", f"/cmd_vel:={topic}",
    ])

    def wait_for(predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert process.poll() is None, "adapter exited before handling commands"
            executor.spin_once(timeout_sec=0.02)
            if predicate():
                return
        raise AssertionError("timed out waiting for adapter output")

    try:
        wait_for(lambda: received and Path(socket).exists())
        assert received[-1].linear.x == 0.0
        received.clear()
        assert sender.send(SimulationCommand(
            1, time.monotonic() + 0.25, True, 1.0, 1.0, 1.0,
        ).encode())
        wait_for(lambda: any(msg.linear.x == 0.4 for msg in received))
        moving = next(msg for msg in received if msg.linear.x == 0.4)
        assert moving.linear.y == -0.4
        assert moving.angular.z == -1.2
        received.clear()
        wait_for(lambda: any(msg.linear.x == msg.linear.y == msg.angular.z == 0.0 for msg in received))
    finally:
        sender.close()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        executor.shutdown()
        node.destroy_node()
        context.shutdown()


def test_autonomy_repetition_does_not_renew_stale_ros_input(tmp_path):
    from rescuebot.autonomy import AutonomyStatus
    from rescuebot.autonomy_ipc import decode_autonomy_intent
    from rescuebot.bridge_ipc import DatagramReceiver
    from rescuebot_sim_bridge.autonomy_adapter import SimulationAutonomyAdapter

    rclpy.init()
    receiver = DatagramReceiver(tmp_path / "intent.sock")
    node = SimulationAutonomyAdapter(str(tmp_path / "intent.sock"),
                                     str(tmp_path / "status.sock"), .08, .08, .24)
    try:
        node._status = AutonomyStatus(True, "test", None)
        message = Twist()
        message.linear.x = .04
        node._receive_twist(message)
        received_at = node._received_at
        node._forward()
        first = decode_autonomy_intent(receiver.drain()[0])
        node._forward()
        second = decode_autonomy_intent(receiver.drain()[0])
        assert first.forward == .5
        assert first.expires_at == second.expires_at == received_at + .25
        node._received_at -= .3
        node._forward()
        assert not receiver.drain()
    finally:
        node.destroy_node()
        receiver.close()
        rclpy.shutdown()
