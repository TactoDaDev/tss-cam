"""TSS Cam receiver layer: protocol, game detection, and the hub + BeamNG hook end to end over real local UDP
(a fake phone and a fake game, no hardware).  Run:  python -m unittest discover -s receiver/tests -t receiver"""
from __future__ import annotations

import socket
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tsscam import protocol as tsc  # noqa: E402
from tsscam.games.beamng import BeamNGHook  # noqa: E402
from tsscam.games.detect import running_game  # noqa: E402
from tsscam.hub import TscHub  # noqa: E402


def udp(timeout=1.0) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(timeout)
    return s


def recv_until(sock: socket.socket, magic: bytes, timeout=2.0) -> bytes:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        if data[:4] == magic:
            return data
    raise AssertionError(f"no {magic!r} within {timeout} s")


class ProtocolTest(unittest.TestCase):
    def test_json_round_trip(self):
        pkt = tsc.decode(tsc.encode_json(tsc.COMMAND, {"seq": 7, "cmd": "door", "args": {"id": "door_FL"}}))
        self.assertEqual(pkt.magic, tsc.COMMAND)
        self.assertEqual(pkt.json()["args"]["id"], "door_FL")

    def test_big_caps_are_compressed_into_one_datagram(self):
        caps = {"controls": [{"id": f"door_{i}", "label": f"Door {i}", "group": "doors", "kind": "toggle"}
                             for i in range(60)]}
        data = tsc.encode_json(tsc.CAPS, caps)
        self.assertLessEqual(len(data), tsc.MAX_DATAGRAM)
        self.assertEqual(len(tsc.decode(data).json()["controls"]), 60)

    def test_damage_is_rejected(self):
        data = bytearray(tsc.encode_json(tsc.HELLO, {"ver": "1"}))
        data[10] ^= 0xFF
        with self.assertRaises(tsc.TscError):
            tsc.decode(bytes(data))
        with self.assertRaises(tsc.TscError):
            tsc.decode(b"ACHC" + bytes(96))

    def test_sticks(self):
        self.assertEqual(tsc.decode(tsc.encode_sticks(2, -0.5, 0, 0.25)).sticks(), (1.0, -0.5, 0.0, 0.25))


class DetectTest(unittest.TestCase):
    def test_tasklist(self):
        out = '"System","4","Services","0","1 K"\n"BeamNG.drive.x64.exe","123","Console","1","9,000 K"\n'
        self.assertEqual(running_game(out), "beamng")
        self.assertEqual(running_game('"acs.exe","9","Console","1","1 K"\n'), "assetto")
        self.assertIsNone(running_game('"explorer.exe","9","Console","1","1 K"\n'))


class HubBeamNGTest(unittest.TestCase):
    def setUp(self):
        self.game = udp()      # the BeamNG mod's socket
        self.phone = udp()     # the phone app's socket
        self.service = udp()   # the receiver's socket (the hub answers the phone through it)
        self.maps = (bytearray(108), bytearray(48), bytearray(48))
        game_addr = self.game.getsockname()
        self.hooks: list[BeamNGHook] = []

        def make(cb):
            h = BeamNGHook(cb, game_addr=game_addr, hook_addr=("127.0.0.1", 0), mappings=self.maps)
            self.hooks.append(h)
            return h

        self.hub = TscHub(mode="auto", hooks={"beamng": make}, detect=lambda: "beamng")
        self.hub.tick(time.monotonic())
        self.hook_addr = self.hooks[0].sock.getsockname()

    def tearDown(self):
        self.hub.stop()
        for s in (self.game, self.phone, self.service):
            s.close()

    def phone_send(self, data: bytes):
        self.hub.handle_phone(data, self.phone.getsockname(), self.service)

    def test_auto_mode_starts_the_beamng_hook(self):
        self.assertEqual(self.hub.game_key(), "beamng")

    def test_hello_gets_hello_and_caps(self):
        self.phone_send(tsc.encode_json(tsc.HELLO, {"app": "tsscam", "ver": "0.1"}))
        hello = tsc.decode(recv_until(self.phone, tsc.HELLO)).json()
        self.assertEqual(hello["game"], "beamng")
        caps = tsc.decode(recv_until(self.phone, tsc.CAPS)).json()
        self.assertEqual(caps["game"], "beamng")
        self.assertFalse(caps["connected"])

    def test_command_before_the_game_is_seen_is_refused(self):
        self.phone_send(tsc.encode_json(tsc.COMMAND, {"seq": 1, "cmd": "horn"}))
        ans = tsc.decode(recv_until(self.phone, tsc.ANSWER)).json()
        self.assertEqual((ans["seq"], ans["ok"]), (1, False))

    def test_caps_command_answer_and_sticks_round_trip(self):
        self.phone_send(tsc.encode_json(tsc.HELLO, {"ver": "0.1"}))
        caps = {"rev": 1, "car": "BMW M5", "controls": [{"id": "door_FL", "group": "doors", "kind": "toggle"}]}
        self.game.sendto(tsc.encode_json(tsc.CAPS, caps), self.hook_addr)
        got = None
        end = time.monotonic() + 2
        while time.monotonic() < end and not (got and got.get("car")):
            got = tsc.decode(recv_until(self.phone, tsc.CAPS)).json()
        self.assertEqual(got["car"], "BMW M5")
        self.assertTrue(got["connected"])
        cmd = tsc.encode_json(tsc.COMMAND, {"seq": 5, "cmd": "door", "args": {"id": "door_FL"}})
        self.phone_send(cmd)
        self.assertEqual(recv_until(self.game, tsc.COMMAND), cmd)
        self.game.sendto(tsc.encode_json(tsc.ANSWER, {"seq": 5, "ok": True, "err": ""}), self.hook_addr)
        self.assertTrue(tsc.decode(recv_until(self.phone, tsc.ANSWER)).json()["ok"])
        sticks = tsc.encode_sticks(0.5, 0, 0, 1)
        self.phone_send(sticks)
        self.assertEqual(recv_until(self.game, tsc.STICKS), sticks)

    def test_the_bridge_part_still_works(self):
        pose = bytearray(108)
        pose[0:4] = b"POSE"
        struct.pack_into("<I", pose, 8, 42)
        struct.pack_into("<I", pose, 104, 42)  # seqBegin == seqEnd: a whole sample
        self.maps[0][:] = pose
        self.hooks[0].step(time.monotonic())
        got, end = b"", time.monotonic() + 2  # the hook's thread may have sent the earlier (empty) pose first
        while got != bytes(pose) and time.monotonic() < end:
            got, _ = self.game.recvfrom(256)
        self.assertEqual(got, bytes(pose))
        status = b"HMS1" + bytes(44)
        self.game.sendto(status, self.hook_addr)
        time.sleep(0.05)
        self.hooks[0].step(time.monotonic())
        self.assertEqual(bytes(self.maps[1]), status)

    def test_game_select_command(self):
        self.phone_send(tsc.encode_json(tsc.COMMAND, {"seq": 9, "cmd": "game.select", "args": {"game": "assetto"}}))
        self.assertTrue(tsc.decode(recv_until(self.phone, tsc.ANSWER)).json()["ok"])
        self.assertEqual(self.hub.mode, "assetto")


class ServiceIntegrationTest(unittest.TestCase):
    """The patched AcHandCam service hands TSC* datagrams to the hub and keeps rejecting junk."""

    def test_hello_through_the_real_service(self):
        from handheld_receiver.config import Settings
        from handheld_receiver.service import ReceiverService
        probe = udp()
        port = probe.getsockname()[1]
        probe.close()
        settings = Settings()
        settings.network.port = port
        settings.network.bind_address = "127.0.0.1"
        svc = ReceiverService(settings, use_shm=False)
        hub = TscHub(mode="assetto", hooks={"assetto": __import__("tsscam.games.assetto", fromlist=["x"]).AssettoHook},
                     detect=lambda: None)
        svc.extra_handler = hub.handle_phone
        svc.start()
        phone = udp()
        try:
            phone.sendto(tsc.encode_json(tsc.HELLO, {"ver": "0.1"}), ("127.0.0.1", port))
            self.assertEqual(tsc.decode(recv_until(phone, tsc.HELLO)).json()["mode"], "assetto")
            phone.sendto(tsc.encode_json(tsc.COMMAND, {"seq": 3, "cmd": "lights"}), ("127.0.0.1", port))
            ans = tsc.decode(recv_until(phone, tsc.ANSWER)).json()
            self.assertFalse(ans["ok"])  # AC car controls arrive in phase 6
        finally:
            phone.close()
            hub.stop()
            svc.stop()


if __name__ == "__main__":
    unittest.main()
