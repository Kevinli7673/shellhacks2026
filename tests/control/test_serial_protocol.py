import json
from pathlib import Path
import unittest

from rescuebot.serial_link import MotorLink
from rescuebot.serial_protocol import (
    MAX_LINE_BYTES,
    ControlAck,
    ControlCommand,
    DriveAck,
    DriveCommand,
    FirmwareFault,
    ImuTelemetry,
    LineDecoder,
    ProtocolError,
    parse_command,
    parse_inbound,
)
from rescuebot.serial_sim import SimulatedFirmware


VECTORS_PATH = Path(__file__).parents[2] / "fixtures" / "serial_protocol_vectors.json"


def decode_lines(lines: list[bytes]) -> list[dict]:
    return [json.loads(line) for line in lines]


class ProtocolVectorTests(unittest.TestCase):
    """The simulated firmware must pass the same vectors as the real firmware."""

    def test_simulated_firmware_matches_shared_vectors(self) -> None:
        document = json.loads(VECTORS_PATH.read_text())
        self.assertGreaterEqual(len(document["cases"]), 20)
        for case in document["cases"]:
            with self.subTest(case=case["name"]):
                firmware = SimulatedFirmware(
                    watchdog_s=document["watchdog_ms"] / 1000,
                    hardware_ceiling=case.get("hardware_ceiling", document["hardware_ceiling"]),
                )
                self.assertEqual(decode_lines(firmware.reboot()), document["boot_emit"])
                for index, step in enumerate(case["steps"]):
                    now = step["t_ms"] / 1000
                    if step.get("tick"):
                        emitted = firmware.tick(now)
                    else:
                        emitted = firmware.receive(step["send"].encode() + b"\n", now)
                    context = f"step {index}"
                    self.assertEqual(decode_lines(emitted), step["emit"], context)
                    self.assertEqual(firmware.armed, step["armed"], context)
                    self.assertEqual(firmware.outputs, step["outputs"], context)

    def test_vector_lines_parse_the_same_way_on_the_pi(self) -> None:
        document = json.loads(VECTORS_PATH.read_text())
        for case in document["cases"]:
            for step in case["steps"]:
                if step.get("tick"):
                    continue
                expect_reject = any(message.get("type") == "fault" for message in step["emit"])
                with self.subTest(case=case["name"], line=step["send"][:60]):
                    if expect_reject:
                        with self.assertRaises(ProtocolError):
                            parse_command(step["send"])
                    else:
                        parse_command(step["send"])


class CodecTests(unittest.TestCase):
    def test_drive_packet_matches_the_frozen_shape(self) -> None:
        line = DriveCommand("pi01", 142, 1.0, 0.0, -0.5, 60).encode()
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(
            json.loads(line),
            {
                "type": "drive",
                "session": "pi01",
                "seq": 142,
                "forward": 1.0,
                "sideways": 0.0,
                "turn": -0.5,
                "speed_limit": 60,
            },
        )
        self.assertEqual(parse_command(line.rstrip(b"\n")), DriveCommand("pi01", 142, 1.0, 0.0, -0.5, 60))

    def test_ack_matches_the_frozen_shape(self) -> None:
        line = b'{"session":"pi01","ack":142,"fl":60,"fr":60,"rl":60,"rr":60}'
        self.assertEqual(parse_inbound(line), DriveAck("pi01", 142, 60, 60, 60, 60))

    def test_outbound_encoder_refuses_invalid_commands(self) -> None:
        for args in [
            ("pi01", 1, float("nan"), 0.0, 0.0, 60),
            ("pi01", 1, 1.01, 0.0, 0.0, 60),
            ("pi01", 1, 0.0, 0.0, 0.0, 256),
            ("pi01", 1, 0.0, 0.0, 0.0, 60.0),
            ("pi01", 0, 0.0, 0.0, 0.0, 60),
            ("bad session", 1, 0.0, 0.0, 0.0, 60),
            ("x" * 33, 1, 0.0, 0.0, 0.0, 60),
        ]:
            with self.subTest(args=args), self.assertRaises(ProtocolError):
                DriveCommand(*args)
        with self.assertRaises(ProtocolError):
            ControlCommand("boost", "pi01", 1)

    def test_firmware_replies_match_protocol_messages_h(self) -> None:
        self.assertEqual(
            parse_inbound(b'{"type":"arm_ack","session":"pi01","seq":2,"armed":true}'),
            ControlAck("arm_ack", "pi01", 2, True),
        )
        self.assertEqual(
            parse_inbound(b'{"type":"disarm_ack","session":"pi01","seq":3,"armed":false}'),
            ControlAck("disarm_ack", "pi01", 3, False),
        )
        self.assertEqual(
            parse_inbound(b'{"type":"fault","reason":"watchdog_expired","armed":false}'),
            FirmwareFault("watchdog_expired"),
        )
        self.assertEqual(
            parse_inbound(b'{"type":"imu","timestamp_ms":1200,"available":true,"heading":12.5,"calibration":3}'),
            ImuTelemetry(1200, True, 12.5, 3),
        )
        self.assertEqual(
            parse_inbound(b'{"type":"imu","timestamp_ms":1250,"available":false}'),
            ImuTelemetry(1250, False),
        )

    def test_inbound_messages_are_validated(self) -> None:
        for line in [
            b'{"session":"pi01","ack":3,"fl":256,"fr":0,"rl":0,"rr":0}',
            b'{"session":"pi01","ack":3,"fl":0.5,"fr":0,"rl":0,"rr":0}',
            b'{"session":"pi01","ack":3,"fl":0,"fr":0,"rl":0}',
            b'{"type":"arm_ack","session":"pi01","seq":2,"armed":"yes"}',
            b'{"type":"fault","armed":false}',
            b'{"type":"imu","timestamp_ms":-1,"available":true}',
            b'{"type":"imu","timestamp_ms":5,"available":true,"calibration":4}',
            b'{"type":"imu","timestamp_ms":5,"available":true,"extra":1}',
            b'{"type":"unknown"}',
            b"[1, 2]",
            b"\xff\xfe",
        ]:
            with self.subTest(line=line), self.assertRaises(ProtocolError):
                parse_inbound(line)


class LineDecoderTests(unittest.TestCase):
    def test_fragmented_bytes_reassemble_into_one_line(self) -> None:
        decoder = LineDecoder()
        line = DriveCommand("pi01", 7, 1.0, 0.0, 0.0, 60).encode()
        out: list[bytes] = []
        for byte in line:
            out.extend(decoder.feed(bytes([byte])))
        self.assertEqual(out, [line.rstrip(b"\n")])

    def test_combined_chunk_yields_each_line_and_keeps_the_remainder(self) -> None:
        decoder = LineDecoder()
        self.assertEqual(decoder.feed(b'{"a":1}\r\n\n{"b":2}\n{"c"'), [b'{"a":1}', b'{"b":2}'])
        self.assertEqual(decoder.feed(b":3}\n"), [b'{"c":3}'])

    def test_oversized_line_is_discarded_and_the_next_line_recovers(self) -> None:
        decoder = LineDecoder()
        self.assertEqual(decoder.feed(b"x" * (MAX_LINE_BYTES + 10)), [])
        self.assertEqual(decoder.feed(b"y" * 1000), [])
        self.assertLessEqual(len(decoder._buffer), MAX_LINE_BYTES + 1)
        self.assertEqual(decoder.feed(b'\n{"ok":1}\n'), [b'{"ok":1}'])
        self.assertEqual(decoder.oversized_count, 1)


class Harness:
    """Connects a MotorLink to a SimulatedFirmware with controllable faults."""

    def __init__(self) -> None:
        sessions = iter(["pi01", "pi02", "pi03"])
        self.link = MotorLink(session_factory=lambda: next(sessions))
        self.firmware = SimulatedFirmware()
        self.now = 0.0
        self.drop_to_firmware = False
        self.drop_to_pi = False
        self.sent: list[dict] = []

    def to_firmware(self, line: bytes | None) -> None:
        if line is None:
            return
        self.sent.append(json.loads(line))
        if self.drop_to_firmware:
            return
        self.to_pi(self.firmware.receive(line, self.now))

    def to_pi(self, lines: list[bytes]) -> None:
        if self.drop_to_pi:
            return
        for reply in self.link.receive(b"".join(lines), self.now):
            self.to_firmware(reply)

    def connect_and_arm(self) -> None:
        self.to_firmware(self.link.connect())
        self.to_firmware(self.link.request_arm())

    def drive(self, forward: float = 1.0, advance_s: float = 0.05, speed_limit: int = 100) -> None:
        self.now += advance_s
        self.to_pi(self.firmware.tick(self.now))
        self.to_firmware(self.link.drive(forward, 0.0, 0.0, speed_limit, self.now))


class MotorLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def test_connect_never_arms_and_drive_is_suppressed_until_armed(self) -> None:
        self.h.to_firmware(self.h.link.connect())
        self.assertFalse(self.h.link.armed)
        self.assertIsNone(self.h.link.drive(1.0, 0.0, 0.0, 100, now=0.1))
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.sent, [{"type": "disarm", "session": "pi01", "seq": 1}])

    def test_explicit_arm_and_drive_round_trip(self) -> None:
        self.h.connect_and_arm()
        self.assertTrue(self.h.link.armed)
        self.assertTrue(self.h.firmware.armed)
        self.h.drive()
        snapshot = self.h.link.snapshot(self.h.now)
        self.assertEqual(snapshot.wheels, {"fl": 100, "fr": 100, "rl": 100, "rr": 100})
        self.assertEqual(snapshot.last_ack_seq, snapshot.last_sent_seq)
        self.assertEqual(snapshot.ack_age_ms, 0)

    def test_arm_is_not_confirmed_without_a_firmware_reply(self) -> None:
        self.h.to_firmware(self.h.link.connect())
        self.h.drop_to_pi = True
        self.h.to_firmware(self.h.link.request_arm())
        self.assertFalse(self.h.link.armed)
        self.assertTrue(self.h.link.snapshot(0.0).arm_pending)

    def test_missing_acks_disarm_after_250ms_without_retransmitting(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.drop_to_pi = True
        for _ in range(4):
            self.h.drive(advance_s=0.05)
        self.assertTrue(self.h.link.armed)
        self.h.drive(advance_s=0.06)  # 0.26 s since the last advancing ACK
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "ack_timeout")
        self.assertEqual(self.h.sent[-1]["type"], "disarm")
        self.assertFalse(self.h.firmware.armed)
        drive_seqs = [m["seq"] for m in self.h.sent if m["type"] == "drive"]
        self.assertEqual(drive_seqs, sorted(set(drive_seqs)))

    def test_stale_duplicate_and_foreign_acks_do_not_refresh_the_deadline(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        seq = self.h.link.last_ack_seq
        for line in [
            DriveAck("pi01", seq, 1, 1, 1, 1).encode(),  # duplicate
            DriveAck("pi01", seq - 1, 1, 1, 1, 1).encode(),  # out of order
            DriveAck("pi01", seq + 5, 1, 1, 1, 1).encode(),  # never sent
            DriveAck("zz99", seq + 1, 1, 1, 1, 1).encode(),  # other session
        ]:
            self.h.link.receive(line, self.h.now + 0.2)
        self.assertEqual(self.h.link.stale_acks, 4)
        self.assertEqual(self.h.link.wheels, {"fl": 100, "fr": 100, "rl": 100, "rr": 100})
        self.assertIsNotNone(self.h.link.check(self.h.now + 0.26))
        self.assertFalse(self.h.link.armed)

    def test_imu_telemetry_never_counts_as_an_ack(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        for step in range(1, 7):
            self.h.link.receive(self.h.firmware.imu(self.h.now + step * 0.05), self.h.now + step * 0.05)
        self.assertEqual(self.h.link.imu_messages, 6)
        self.assertIsNotNone(self.h.link.check(self.h.now + 0.3))
        self.assertEqual(self.h.link.fault, "ack_timeout")

    def test_boot_message_disarms_immediately_and_requires_explicit_rearm(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.to_pi(self.h.firmware.reboot())
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "boot")
        self.h.drive()
        self.assertEqual(self.h.firmware.outputs, {"fl": 0, "fr": 0, "rl": 0, "rr": 0})
        self.h.to_firmware(self.h.link.request_arm())
        self.h.drive()
        self.assertTrue(self.h.link.armed)
        self.assertEqual(self.h.firmware.outputs["fl"], 100)

    def test_lost_boot_message_still_trips_the_ack_deadline(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.firmware.reboot()  # boot line lost on the wire
        for _ in range(6):
            self.h.drive()  # ignored by the rebooted firmware: no ACKs; 0.30 s > 0.25 s
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "ack_timeout")
        self.assertFalse(self.h.firmware.armed)
        self.h.to_firmware(self.h.link.request_arm())
        self.h.drive()
        self.assertTrue(self.h.link.armed)
        self.assertEqual(self.h.firmware.outputs["fl"], 100)

    def test_imu_clock_restart_detects_a_reboot_before_the_deadline(self) -> None:
        self.h.connect_and_arm()
        self.h.to_pi([self.h.firmware.imu(self.h.now + 10.0)])
        self.h.drive()
        self.h.firmware.reboot()  # boot line lost on the wire
        self.h.to_pi([self.h.firmware.imu(0.02)])
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "firmware_reset")

    def test_reconnect_uses_a_fresh_session_and_old_packets_are_ignored(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        old_drive = DriveCommand("pi01", self.h.link.last_sent_seq + 1, 1.0, 0.0, 0.0, 100).encode()
        self.h.link.disconnect()
        self.assertEqual(self.h.link.fault, "link_lost")
        self.h.to_firmware(self.h.link.connect())
        self.assertEqual(self.h.link.session, "pi02")
        self.assertFalse(self.h.link.armed)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.firmware.receive(old_drive, self.h.now), [])
        self.assertEqual(self.h.firmware.outputs, {"fl": 0, "fr": 0, "rl": 0, "rr": 0})
        self.h.link.receive(DriveAck("pi01", 1, 5, 5, 5, 5).encode(), self.h.now)
        self.assertEqual(self.h.link.stale_acks, 1)

    def test_unrequested_arm_ack_is_answered_with_disarm(self) -> None:
        self.h.to_firmware(self.h.link.connect())
        self.h.link.last_sent_seq = 5  # pretend seq 2 was sent but never as an arm
        self.h.drop_to_firmware = True
        self.h.to_pi([ControlAck("arm_ack", "pi01", 2, True).encode()])
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "unexpected_arm")
        self.assertEqual(self.h.sent[-1]["type"], "disarm")

    def test_arm_ack_for_another_session_is_answered_with_disarm(self) -> None:
        self.h.to_firmware(self.h.link.connect())
        self.h.to_pi([ControlAck("arm_ack", "zz99", 7, True).encode()])
        self.assertFalse(self.h.link.armed)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.sent[-1], {"type": "disarm", "session": "pi01", "seq": 2})

    def test_firmware_fault_report_disarms_the_pi_side(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.to_firmware(b'{"type":"drive","session":"pi01","seq":99,"forward":2}\n')
        self.assertFalse(self.h.firmware.armed)
        self.assertFalse(self.h.link.armed)
        self.assertEqual(self.h.link.fault, "malformed_packet")

    def test_firmware_watchdog_stops_when_the_pi_goes_silent(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.now += 0.5
        self.h.to_pi(self.h.firmware.tick(self.h.now))
        self.assertTrue(self.h.firmware.armed)
        self.h.now += 0.01
        self.h.to_pi(self.h.firmware.tick(self.h.now))
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.link.fault, "watchdog_expired")
        self.assertFalse(self.h.link.armed)

    def test_garbled_and_oversized_inbound_lines_are_counted_not_fatal(self) -> None:
        self.h.connect_and_arm()
        self.h.link.receive(b"\x00\xffgarbage\n" + b"z" * 300 + b"\n", self.h.now)
        self.assertEqual(self.h.link.rejected_lines, 2)
        self.assertTrue(self.h.link.armed)

    def test_stop_disarms_immediately_outside_the_drive_cadence(self) -> None:
        self.h.connect_and_arm()
        self.h.drive()
        self.h.to_firmware(self.h.link.disarm("stop"))
        self.assertFalse(self.h.link.armed)
        self.assertFalse(self.h.firmware.armed)
        self.assertEqual(self.h.firmware.outputs, {"fl": 0, "fr": 0, "rl": 0, "rr": 0})
        self.assertIsNone(self.h.link.drive(1.0, 0.0, 0.0, 100, self.h.now + 0.01))


if __name__ == "__main__":
    unittest.main()
