import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fixtures.mdns_query import parse_addresses, read_name

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "publish_addresses", ROOT / "rootfs/usr/share/usb-printer/publish_addresses.py"
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class DNSParserTests(unittest.TestCase):
    def test_ignores_unrelated_names_and_zero_ttl(self):
        packet = struct.pack("!6H", 0, 0x8400, 1, 4, 0, 0)
        packet += b"\x0dgreen-printer\x05local\x00" + struct.pack("!HH", 1, 1)
        packet += b"\x0dhomeassistant\x05local\x00" + struct.pack(
            "!HHIH4B", 1, 1, 120, 4, 192, 0, 2, 99
        )
        packet += b"\xc0\x0c" + struct.pack("!HHIH4B", 1, 0x8001, 0, 4, 192, 0, 2, 3)
        packet += b"\xc0\x0c" + struct.pack("!HHIH4B", 1, 3, 120, 4, 192, 0, 2, 4)
        packet += b"\xc0\x0c" + struct.pack("!HHIH4B", 1, 0x8001, 120, 4, 192, 0, 2, 2)
        self.assertEqual(parse_addresses(packet, 1), {"192.0.2.2"})

    def test_rejects_compression_loops(self):
        with self.assertRaises(ValueError):
            read_name(b"\xc0\x00", 0)


class AddressSelectionTests(unittest.TestCase):
    def select(self, addresses, flags=("UP", "LOWER_UP")):
        return publisher.interface_addresses(
            [{"ifindex": 7, "flags": flags, "addr_info": addresses}]
        )

    def test_prefers_non_deprecated_global_addresses_per_family(self):
        index, addresses = self.select(
            [
                {"family": "inet", "scope": "global", "local": "192.0.2.2"},
                {"family": "inet6", "scope": "link", "local": "fe80::1"},
                {"family": "inet6", "scope": "global", "local": "fd00::2"},
                {
                    "family": "inet6",
                    "scope": "global",
                    "local": "fd00::1",
                    "deprecated": True,
                },
                {
                    "family": "inet6",
                    "scope": "global",
                    "local": "fd00::3",
                    "tentative": True,
                },
                {
                    "family": "inet6",
                    "scope": "global",
                    "local": "fd00::4",
                    "dadfailed": True,
                },
            ]
        )
        self.assertEqual(index, 7)
        self.assertEqual(addresses, ("192.0.2.2", "fd00::2"))

    def test_keeps_link_local_when_no_usable_global_address(self):
        _, addresses = self.select(
            [
                {"family": "inet6", "scope": "link", "local": "fe80::1"},
                {
                    "family": "inet6",
                    "scope": "global",
                    "local": "fd00::1",
                    "tentative": True,
                },
            ]
        )
        self.assertEqual(addresses, ("fe80::1",))

    def test_withdraws_addresses_when_link_is_down(self):
        self.assertEqual(
            self.select(
                [
                    {"family": "inet", "scope": "global", "local": "192.0.2.2"},
                ],
                flags=("UP",),
            ),
            (7, ()),
        )


class PublisherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ready = Path(directory.name) / "ready"
        patcher = patch.object(publisher, "READY_FILE", self.ready)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bus = Mock()
        self.server = Mock()
        self.group = Mock()
        self.call = Mock()
        self.bus.get_object.side_effect = [self.server, self.group]
        self.group.get_dbus_method.side_effect = lambda method, _: getattr(
            self.call, method
        )
        self.server.GetState.return_value = 2
        self.call.GetState.return_value = 0
        self.subject = publisher.Publisher(self.bus)
        self.snapshot = (7, ("192.0.2.2", "fd00::1"))

    def test_publishes_one_forward_only_group_before_reporting_ready(self):
        self.subject.update(self.snapshot)
        self.assertEqual(self.call.AddAddress.call_count, 2)
        self.call.AddAddress.assert_any_call(
            7, -1, 16, "green-printer.local", "192.0.2.2", timeout=2
        )
        self.call.Commit.assert_called_once()
        self.assertFalse(self.ready.exists())
        self.call.GetState.return_value = 2
        self.subject.update(self.snapshot)
        self.assertTrue(publisher.check_ready())
        self.subject.update(self.snapshot)
        self.call.Commit.assert_called_once()

    def test_collision_withdraws_readiness_and_backs_off_without_renaming(self):
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 2
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 3
        with patch.object(publisher.time, "monotonic", return_value=100):
            self.subject.update(self.snapshot)
            self.assertFalse(self.ready.exists())
            self.assertEqual(self.subject.retry_at, 130)
            self.call.GetState.return_value = 0
            self.subject.update(self.snapshot)
            self.call.Commit.assert_called_once()
        with patch.object(publisher.time, "monotonic", return_value=130):
            self.subject.update(self.snapshot)
        self.assertEqual(self.call.Commit.call_count, 2)
        for call in self.call.AddAddress.call_args_list:
            self.assertEqual(call.args[3], "green-printer.local")

    def test_synchronous_collision_reuses_connection_and_backs_off(self):
        class CollisionError(Exception):
            def get_dbus_name(self):
                return f"{publisher.AVAHI}.CollisionError"

        dbus = Mock()
        dbus.DBusException = CollisionError
        dbus.SystemBus.return_value = self.bus
        self.bus.get_object.side_effect = [self.server, self.group]
        self.call.AddAddress.side_effect = CollisionError("conflict")
        with (
            patch.dict("sys.modules", {"dbus": dbus}),
            patch.object(publisher, "read_addresses", return_value=self.snapshot),
            patch.object(publisher.time, "monotonic", return_value=100),
            patch.object(publisher.time, "sleep", side_effect=[None, SystemExit]),
            patch.object(publisher.signal, "signal"),
            self.assertRaises(SystemExit),
        ):
            publisher.main()
        dbus.SystemBus.assert_called_once()
        self.call.AddAddress.assert_called_once()
        self.call.Commit.assert_not_called()
        self.assertFalse(self.ready.exists())

    def test_failure_withdraws_readiness_and_backs_off(self):
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 2
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 4
        with patch.object(publisher.time, "monotonic", return_value=100):
            self.subject.update(self.snapshot)
        self.assertFalse(self.ready.exists())
        self.assertEqual(self.subject.retry_at, 130)

    def test_empty_address_set_withdraws_publication(self):
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 2
        self.subject.update(self.snapshot)
        self.call.Reset.reset_mock()
        self.subject.update((7, ()))
        self.call.Reset.assert_called_once()
        self.assertFalse(self.ready.exists())

    def test_new_interface_index_recreates_group_even_with_same_addresses(self):
        self.subject.update(self.snapshot)
        self.call.Reset.reset_mock()
        self.subject.update((8, self.snapshot[1]))
        self.call.Reset.assert_called_once()
        self.call.AddAddress.assert_any_call(
            8, -1, 16, "green-printer.local", "192.0.2.2", timeout=2
        )

    def test_unavailable_server_clears_readiness(self):
        self.subject.update(self.snapshot)
        self.call.GetState.return_value = 2
        self.subject.update(self.snapshot)
        self.server.GetState.return_value = 1
        self.subject.update(self.snapshot)
        self.assertFalse(self.ready.exists())
        self.assertIsNone(self.subject.snapshot)

    def test_readiness_expires_if_publisher_stalls(self):
        self.ready.write_text(str(os.getpid()))
        self.assertTrue(publisher.check_ready())
        os.utime(self.ready, (0, 0))
        self.assertFalse(publisher.check_ready())

    def test_invalid_readiness_is_unhealthy(self):
        self.ready.write_text("")
        self.assertFalse(publisher.check_ready())


if __name__ == "__main__":
    unittest.main()
