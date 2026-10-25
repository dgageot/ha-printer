import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOSTNAME = "green-printer.local"
READY_FILE = Path("/run/printer-addresses.ready")
AVAHI = "org.freedesktop.Avahi"
ESTABLISHED = 2
COLLISION = 3
FAILURE = 4
NO_REVERSE = 16
RETRY_DELAY = 30


def interface_addresses(data):
    interface = data[0]
    addresses = []
    if "UP" in interface["flags"] and "LOWER_UP" in interface["flags"]:
        for family in ("inet", "inet6"):
            candidates = [
                address
                for address in interface["addr_info"]
                if address["family"] == family
                and address["scope"] in ("global", "link")
                and not address.get("tentative")
                and not address.get("dadfailed")
            ]
            preferred = [
                address
                for address in candidates
                if address["scope"] == "global" and not address.get("deprecated")
            ]
            addresses.extend(address["local"] for address in preferred or candidates)
    return interface["ifindex"], tuple(sorted(addresses))


def read_addresses():
    result = subprocess.run(
        ["ip", "-j", "address", "show", "dev", "end0"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )
    return interface_addresses(json.loads(result.stdout))


def clear_ready():
    READY_FILE.unlink(missing_ok=True)


class Publisher:
    def __init__(self, bus):
        owner = bus.get_name_owner(AVAHI)
        self.server = bus.get_object(owner, "/")
        path = self.server.EntryGroupNew(dbus_interface=f"{AVAHI}.Server", timeout=2)
        self.group = bus.get_object(owner, path)
        self.snapshot = None
        self.retry_at = 0

    def group_call(self, method, *args):
        return self.group.get_dbus_method(method, f"{AVAHI}.EntryGroup")(
            *args, timeout=2
        )

    def failed(self, error):
        clear_ready()
        print(
            f"Address publication failed for {HOSTNAME} ({error}); "
            f"retrying in {RETRY_DELAY}s without renaming.",
            flush=True,
        )
        self.group_call("Reset")
        self.retry_at = time.monotonic() + RETRY_DELAY

    def update(self, snapshot):
        server_state = self.server.GetState(dbus_interface=f"{AVAHI}.Server", timeout=2)
        if server_state != ESTABLISHED:
            clear_ready()
            self.group_call("Reset")
            self.snapshot = None
            return
        if snapshot != self.snapshot:
            clear_ready()
            self.group_call("Reset")
            self.snapshot = snapshot
            self.retry_at = 0
        index, addresses = snapshot
        if not addresses or time.monotonic() < self.retry_at:
            clear_ready()
            return
        state = self.group_call("GetState")
        if state in (COLLISION, FAILURE):
            self.failed(f"state {state}")
        elif state == ESTABLISHED:
            READY_FILE.write_text(str(os.getpid()))
        elif state == 0:
            clear_ready()
            for address in addresses:
                self.group_call("AddAddress", index, -1, NO_REVERSE, HOSTNAME, address)
            self.group_call("Commit")
        else:
            clear_ready()


def check_ready():
    try:
        pid = int(READY_FILE.read_text())
        os.kill(pid, 0)
        age = time.time() - READY_FILE.stat().st_mtime
        return 0 <= age < 5
    except (OSError, ValueError):
        return False


def main():
    import dbus

    def stop(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bus = publisher = None
    error = None
    clear_ready()
    try:
        while True:
            try:
                snapshot = read_addresses()
                if publisher is None:
                    bus = dbus.SystemBus(private=True)
                    bus.set_exit_on_disconnect(False)
                    publisher = Publisher(bus)
                try:
                    publisher.update(snapshot)
                except dbus.DBusException as exc:
                    if exc.get_dbus_name() != f"{AVAHI}.CollisionError":
                        raise
                    publisher.failed(str(exc))
                error = None
            except (
                dbus.DBusException,
                OSError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                clear_ready()
                if str(exc) != error:
                    print(f"Cannot publish {HOSTNAME}: {exc}", flush=True)
                    error = str(exc)
                if bus is not None:
                    bus.close()
                bus = publisher = None
            time.sleep(1)
    finally:
        clear_ready()
        if bus is not None:
            bus.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if check_ready() else 1)
    main()
