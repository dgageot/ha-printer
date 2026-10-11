import os
import subprocess
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("USB_PRINTER_TEST_IMAGE", "ha-printer:local")
QUEUE = "HP_LaserJet_M109-M112"
URI = "usb://HP/LaserJet%20M109-M112?serial=TEST-PRINTER"


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
                "--health-interval=1s",
                "--health-start-period=0s",
                "--health-retries=2",
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
            result = cls.execute("lpstat", "-v", QUEUE, check=False)
            default = cls.execute("lpstat", "-d", check=False)
            if (
                result.returncode == 0
                and URI in result.stdout
                and QUEUE in default.stdout
            ):
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
            self.execute("/usr/share/usb-printer/healthcheck.sh")
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

    def server_ip(self):
        return subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                self.server,
            ],
            text=True,
        ).strip()

    @staticmethod
    def attribute(tag, name, value):
        name, value = name.encode(), value.encode()
        return (
            bytes([tag]) + len(name).to_bytes(2) + name + len(value).to_bytes(2) + value
        )

    def post_ipp(self, ip, body):
        return subprocess.run(
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

    def test_oversized_requests_are_rejected(self):
        result = self.post_ipp(self.server_ip(), b"\0" * (25 * 1024 * 1024 + 1))
        self.assertEqual(result.stdout, b"413")

    def test_queue_rejects_jobs_when_full(self):
        self.addCleanup(self.execute, "cancel", "-a", QUEUE)
        self.execute(
            "bash",
            "-euc",
            'for i in {1..100}; do lp -d "$1" -H hold '
            "/usr/share/cups/data/testprint >/dev/null; done",
            "--",
            QUEUE,
        )
        rejected = self.execute(
            "lp",
            "-d",
            QUEUE,
            "-H",
            "hold",
            "/usr/share/cups/data/testprint",
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Too many active jobs", rejected.stderr)
        self.assertEqual(
            len(self.execute("lpstat", "-o", QUEUE).stdout.splitlines()), 100
        )

    def test_remote_administration_is_denied(self):
        ip = self.server_ip()
        uri = f"ipp://{ip}:631/printers/{QUEUE}"

        body = bytes.fromhex("020040090000000101")
        body += self.attribute(0x47, "attributes-charset", "utf-8")
        body += self.attribute(0x48, "attributes-natural-language", "en")
        body += self.attribute(0x45, "printer-uri", uri) + b"\x03"
        result = self.post_ipp(ip, body)
        self.assertEqual(result.stdout, b"403")

    def test_remote_document_access_is_denied(self):
        ip = self.server_ip()
        held = self.execute(
            "lp", "-d", QUEUE, "-H", "hold", "/usr/share/cups/data/testprint"
        ).stdout
        job_id = int(held.split(f"{QUEUE}-", 1)[1].split()[0])
        self.addCleanup(self.execute, "cancel", f"{QUEUE}-{job_id}")
        uri = f"ipp://{ip}:631/printers/{QUEUE}"

        body = bytes.fromhex("020040270000000101")
        body += self.attribute(0x47, "attributes-charset", "utf-8")
        body += self.attribute(0x48, "attributes-natural-language", "en")
        body += self.attribute(0x45, "printer-uri", uri)
        body += self.attribute(0x42, "requesting-user-name", "root")
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
        result = self.post_ipp(ip, body)
        self.assertEqual(result.stdout, b"403")

    def test_print_filter_produces_output_without_printing(self):
        result = self.execute(
            "bash",
            "-c",
            f"cupsfilter -p /etc/cups/ppd/{QUEUE}.ppd -e "
            f"-m printer/{QUEUE} /usr/share/cups/data/testprint >/tmp/output.pcl && "
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

    def wait_for_health(self, expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    self.server,
                ],
                text=True,
                timeout=10,
            ).strip()
            if state == expected:
                return
            time.sleep(0.5)
        self.fail(f"Expected {expected} health, got {state}")

    def test_health_detects_dependency_failure_and_recovers(self):
        self.wait_for_health("healthy")
        try:
            self.execute("s6-svc", "-d", "/run/service/printer-avahi")
            self.execute("s6-svc", "-r", "/run/service/printer-cups")
            self.wait_for_health("unhealthy")
        finally:
            self.execute("s6-svc", "-u", "/run/service/printer-avahi")
        self.wait_for_health("healthy")
        self.wait_for_queue()
        self.assert_advertised()

    def test_health_detects_unresponsive_scheduler(self):
        self.wait_for_health("healthy")
        try:
            self.execute("pkill", "-STOP", "-x", "cupsd")
            self.wait_for_health("unhealthy")
        finally:
            self.execute("pkill", "-CONT", "-x", "cupsd")
        self.wait_for_health("healthy")

    def test_partial_initialization_is_repaired(self):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                self.network,
                "--entrypoint",
                "bash",
                IMAGE,
                "-euc",
                (
                    "mkdir -p /data/cups/ppd; "
                    "printf saved >/data/cups/ppd/saved.ppd; "
                    "/usr/share/usb-printer/init.sh; "
                    "test -s /data/cups/cups-files.conf; "
                    'test "$(cat /data/cups/ppd/saved.ppd)" = saved; '
                    "/usr/share/usb-printer/init.sh; "
                    'test "$(cat /data/cups/ppd/saved.ppd)" = saved; '
                    "test \"$(grep -c '^SyncOnClose ' /data/cups/cups-files.conf)\" = 1"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("cupsd.conf", result.stdout + result.stderr)

    def test_legacy_initialization_symlinks_are_repaired(self):
        for target in ["/missing", "/data/cups", "/etc/cups"]:
            with self.subTest(target=target):
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        self.network,
                        "--entrypoint",
                        "bash",
                        IMAGE,
                        "-euc",
                        (
                            'mkdir -p /data; ln -s "$1" /data/cups; '
                            "/usr/share/usb-printer/init.sh; "
                            "test ! -L /data/cups; "
                            "test -s /data/cups/cups-files.conf"
                        ),
                        "--",
                        target,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )

    def test_dangling_configuration_symlink_is_repaired(self):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                self.network,
                "--entrypoint",
                "bash",
                IMAGE,
                "-euc",
                (
                    "mkdir -p /data/cups; "
                    "ln -s /missing /data/cups/cups-files.conf; "
                    "/usr/share/usb-printer/init.sh; "
                    "test ! -L /data/cups/cups-files.conf; "
                    "test -s /data/cups/cups-files.conf"
                ),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

    def test_interrupted_initialization_is_retried(self):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                self.network,
                "--entrypoint",
                "bash",
                IMAGE,
                "-euc",
                (
                    "mkdir -p /data/cups.init; "
                    "touch /data/cups.init/incomplete; "
                    "/usr/share/usb-printer/init.sh; "
                    "test ! -e /data/cups.init; "
                    "test ! -e /data/cups/incomplete; "
                    "test -s /data/cups/cups-files.conf"
                ),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

    def test_job_survives_container_replacement(self):
        held = self.execute(
            "lp", "-d", QUEUE, "-H", "hold", "/usr/share/cups/data/testprint"
        ).stdout
        job_id = int(held.split(f"{QUEUE}-", 1)[1].split()[0])
        self.addCleanup(self.execute, "cancel", f"{QUEUE}-{job_id}")
        subprocess.run(
            ["docker", "stop", self.server], check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["docker", "rm", self.server], check=True, capture_output=True, timeout=15
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--network",
                self.network,
                "--name",
                self.server,
                "--volume",
                f"{self.volume}:/data",
                "--volume",
                f"{ROOT / 'tests/fixtures/lpinfo'}:/usr/local/bin/lpinfo:ro",
                IMAGE,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.wait_for_queue()
        self.assertIn(f"{QUEUE}-{job_id}", self.execute("lpstat", "-o", QUEUE).stdout)
        self.execute(
            "cmp", "/usr/share/cups/data/testprint", f"/data/spool/d{job_id:05d}-001"
        )
        self.assert_advertised()

    def test_job_survives_abrupt_shutdown(self):
        held = self.execute(
            "lp", "-d", QUEUE, "-H", "hold", "/usr/share/cups/data/testprint"
        ).stdout
        job_id = int(held.split(f"{QUEUE}-", 1)[1].split()[0])
        self.addCleanup(self.execute, "cancel", f"{QUEUE}-{job_id}")
        subprocess.run(
            ["docker", "kill", self.server], check=True, capture_output=True, timeout=15
        )
        subprocess.run(
            ["docker", "start", self.server],
            check=True,
            capture_output=True,
            timeout=15,
        )
        self.wait_for_queue()
        self.assertIn(f"{QUEUE}-{job_id}", self.execute("lpstat", "-o", QUEUE).stdout)
        self.execute(
            "cmp", "/usr/share/cups/data/testprint", f"/data/spool/d{job_id:05d}-001"
        )

    def test_stale_uri_is_corrected_after_restart(self):
        self.execute("lpadmin", "-p", QUEUE, "-v", URI.replace("TEST-PRINTER", "OLD"))
        subprocess.run(
            ["docker", "restart", self.server],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.wait_for_queue()
        self.assertIn(URI, self.execute("lpstat", "-v", QUEUE).stdout)

    def test_queue_survives_container_restart(self):
        before = self.execute("lpstat", "-v", QUEUE).stdout
        held = self.execute(
            "lp", "-d", QUEUE, "-H", "hold", "/usr/share/cups/data/testprint"
        ).stdout
        job_id = held.split(f"{QUEUE}-", 1)[1].split()[0]
        self.addCleanup(self.execute, "cancel", f"{QUEUE}-{job_id}")
        subprocess.run(
            ["docker", "restart", self.server],
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.wait_for_queue()
        self.assertEqual(before, self.execute("lpstat", "-v", QUEUE).stdout)
        self.assertIn(f"{QUEUE}-{job_id}", self.execute("lpstat", "-o", QUEUE).stdout)
        self.assertEqual(
            self.execute("readlink", "/var/spool/cups").stdout.strip(), "/data/spool"
        )
        self.assert_advertised()


if __name__ == "__main__":
    unittest.main()
