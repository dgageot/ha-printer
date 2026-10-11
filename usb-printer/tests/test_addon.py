import os
import subprocess
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("USB_PRINTER_TEST_IMAGE", "usb-printer:local")
QUEUE = "HP_LaserJet_M109-M112"


class UsbPrinterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = uuid.uuid4().hex[:8]
        cls.server = f"usb-printer-test-{suffix}"
        cls.network = f"{cls.server}-net"
        cls.volume = f"{cls.server}-data"
        subprocess.run(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "-o",
                "com.docker.network.container_iface_prefix=end",
                cls.network,
            ],
            check=True,
            capture_output=True,
        )
        cls.addClassCleanup(
            subprocess.run,
            ["docker", "network", "rm", cls.network],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "volume", "create", cls.volume], check=True, capture_output=True
        )
        cls.addClassCleanup(
            subprocess.run,
            ["docker", "volume", "rm", cls.volume],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--network",
                cls.network,
                "--name",
                cls.server,
                "--volume",
                f"{cls.volume}:/data",
                "--volume",
                f"{ROOT / 'tests/fixtures/lpinfo'}:/usr/local/bin/lpinfo:ro",
                IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        cls.addClassCleanup(
            subprocess.run,
            ["docker", "rm", "--force", cls.server],
            check=True,
            capture_output=True,
        )
        cls.wait_for_queue()

    @classmethod
    def execute(cls, *args, check=True):
        return subprocess.run(
            ["docker", "exec", cls.server, *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=20,
        )

    @classmethod
    def wait_for_queue(cls):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            result = cls.execute("lpstat", "-p", QUEUE, check=False)
            if result.returncode == 0:
                return
            time.sleep(0.5)
        logs = subprocess.check_output(["docker", "logs", cls.server], text=True)
        raise AssertionError(f"Printer did not start: {logs}")

    def assert_advertised(self):
        result = self.execute("ippfind", "-T", "3", "_ipp._tcp", "--print")
        self.assertIn(f"green-printer.local:631/printers/{QUEUE}", result.stdout)

    def test_automatic_queue_and_ipp_attributes(self):
        result = self.execute("lpstat", "-v", QUEUE)
        self.assertIn(
            "usb://HP/LaserJet%20M109-M112?serial=TEST-PRINTER", result.stdout
        )
        result = self.execute(
            "ipptool",
            "-t",
            f"ipp://localhost:631/printers/{QUEUE}",
            "/usr/share/cups/ipptool/get-printer-attributes.test",
        )
        self.assertIn("[PASS]", result.stdout)
        self.assert_advertised()

    def test_disconnected_printer_waits_for_usb_connection(self):
        try:
            self.execute("touch", "/data/no-printer")
            self.execute("lpadmin", "-x", QUEUE)
            self.execute("s6-svc", "-r", "/run/service/printer-setup")
            time.sleep(2)
            self.assertNotEqual(
                self.execute("lpstat", "-p", QUEUE, check=False).returncode, 0
            )
            self.execute("rm", "/data/no-printer")
            self.wait_for_queue()
            self.assertIn(
                "serial=TEST-PRINTER", self.execute("lpstat", "-v", QUEUE).stdout
            )
        finally:
            self.execute("rm", "-f", "/data/no-printer")

    def test_no_scanner_packages(self):
        result = self.execute(
            "dpkg-query", "-W", "-f=${Package} ${db:Status-Abbrev}\\n"
        )
        for package in [
            "hplip",
            "libsane1",
            "sane-utils",
            "scanservjs",
            "nodejs",
            "ipp-usb",
        ]:
            self.assertNotIn(f"{package} ii ", result.stdout)

    def test_remote_administration_is_denied(self):
        ip = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                self.server,
            ],
            text=True,
        ).strip()
        uri = f"ipp://{ip}:631/printers/{QUEUE}"

        def attribute(tag, name, value):
            name, value = name.encode(), value.encode()
            return (
                bytes([tag])
                + len(name).to_bytes(2)
                + name
                + len(value).to_bytes(2)
                + value
            )

        body = bytes.fromhex("020040090000000101")
        body += attribute(0x47, "attributes-charset", "utf-8")
        body += attribute(0x48, "attributes-natural-language", "en")
        body += attribute(0x45, "printer-uri", uri) + b"\x03"
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--network",
                self.network,
                "--entrypoint",
                "curl",
                IMAGE,
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--header",
                "Content-Type: application/ipp",
                "--data-binary",
                "@-",
                f"http://{ip}:631/printers/{QUEUE}",
            ],
            input=body,
            capture_output=True,
            check=True,
            timeout=15,
        )
        self.assertEqual(result.stdout, b"403")

    def test_remote_document_access_is_denied(self):
        ip = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                self.server,
            ],
            text=True,
        ).strip()
        held = self.execute(
            "lp", "-d", QUEUE, "-H", "hold", "/usr/share/cups/data/testprint"
        ).stdout
        job_id = int(held.split(f"{QUEUE}-", 1)[1].split()[0])
        self.addCleanup(self.execute, "cancel", f"{QUEUE}-{job_id}")
        uri = f"ipp://{ip}:631/printers/{QUEUE}"

        def attribute(tag, name, value):
            name, value = name.encode(), value.encode()
            return (
                bytes([tag])
                + len(name).to_bytes(2)
                + name
                + len(value).to_bytes(2)
                + value
            )

        body = bytes.fromhex("020040270000000101")
        body += attribute(0x47, "attributes-charset", "utf-8")
        body += attribute(0x48, "attributes-natural-language", "en")
        body += attribute(0x45, "printer-uri", uri)
        body += attribute(0x42, "requesting-user-name", "root")
        for name, value in [("job-id", job_id), ("document-number", 1)]:
            encoded = name.encode()
            body += (
                b"\x21"
                + len(encoded).to_bytes(2)
                + encoded
                + b"\x00\x04"
                + value.to_bytes(4)
            )
        body += b"\x03"
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--interactive",
                "--network",
                self.network,
                "--entrypoint",
                "curl",
                IMAGE,
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--header",
                "Content-Type: application/ipp",
                "--data-binary",
                "@-",
                f"http://{ip}:631/printers/{QUEUE}",
            ],
            input=body,
            capture_output=True,
            check=True,
            timeout=15,
        )
        self.assertEqual(result.stdout, b"403")

    def test_print_filter_produces_output_without_printing(self):
        result = self.execute(
            "bash",
            "-c",
            f"cupsfilter -p /etc/cups/ppd/{QUEUE}.ppd -e "
            f"-m printer/{QUEUE} /usr/share/cups/data/testprint >/tmp/output.pcl; "
            "test -s /tmp/output.pcl",
        )
        self.assertIn("hpcups", result.stderr)

    def test_discovery_recovers_after_service_restarts(self):
        for service in ["printer-dbus", "printer-avahi"]:
            with self.subTest(service=service):
                before = self.execute("pgrep", "-x", "cupsd").stdout.strip()
                self.execute("s6-svc", "-r", f"/run/service/{service}")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = self.execute(
                        "pgrep", "-x", "cupsd", check=False
                    ).stdout.strip()
                    if current and (service == "printer-avahi" or current != before):
                        probe = self.execute(
                            "ippfind", "-T", "2", "_ipp._tcp", "--print", check=False
                        )
                        if (
                            probe.returncode == 0
                            and f"green-printer.local:631/printers/{QUEUE}"
                            in probe.stdout
                        ):
                            break
                    time.sleep(0.5)
                else:
                    self.fail(f"CUPS did not recover after restarting {service}")
                self.assert_advertised()

    def test_queue_survives_container_restart(self):
        before = self.execute("cat", "/etc/cups/printers.conf").stdout
        subprocess.run(
            ["docker", "restart", self.server],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.wait_for_queue()
        after = self.execute("cat", "/etc/cups/printers.conf").stdout
        self.assertEqual(before, after)
        self.assertEqual(
            self.execute("readlink", "/var/spool/cups").stdout.strip(), "/data/spool"
        )
        self.assert_advertised()


if __name__ == "__main__":
    unittest.main()
