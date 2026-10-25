import ipaddress
import json
import socket
import struct
import sys
import time

HOSTNAME = "green-printer.local"


def read_name(packet, offset):
    labels = []
    end = None
    seen = set()
    while packet[offset]:
        if offset in seen:
            raise ValueError("DNS compression loop")
        seen.add(offset)
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if end is None:
                end = offset + 2
            offset = struct.unpack_from("!H", packet, offset)[0] & 0x3FFF
        else:
            offset += 1
            labels.append(packet[offset : offset + length].decode("ascii"))
            offset += length
    return ".".join(labels).lower(), end if end is not None else offset + 1


def parse_addresses(packet, record_type):
    _, flags, questions, answers, authority, additional = struct.unpack_from(
        "!6H", packet
    )
    if not flags & 0x8000:
        return set()
    offset = 12
    for _ in range(questions):
        _, offset = read_name(packet, offset)
        offset += 4
    addresses = set()
    for _ in range(answers + authority + additional):
        name, offset = read_name(packet, offset)
        kind, cls, ttl, length = struct.unpack_from("!HHIH", packet, offset)
        offset += 10
        if name == HOSTNAME and kind == record_type and cls & 0x7FFF == 1 and ttl:
            addresses.add(str(ipaddress.ip_address(packet[offset : offset + length])))
        offset += length
    return addresses


def query(record_type):
    name = b"\x0dgreen-printer\x05local\x00"
    packet = (
        struct.pack("!6H", 0, 0, 1, 0, 0, 0) + name + struct.pack("!2H", record_type, 1)
    )
    addresses = set()
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as client:
        index = socket.if_nametoindex("end0")
        client.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, index)
        client.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
        membership = socket.inet_pton(socket.AF_INET6, "ff02::fb") + struct.pack(
            "@I", index
        )
        client.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, membership)
        client.bind(("::", 5353))
        client.sendto(packet, ("ff02::fb", 5353, 0, index))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            client.settimeout(deadline - time.monotonic())
            try:
                response = client.recv(65535)
            except TimeoutError:
                break
            addresses.update(parse_addresses(response, record_type))
    return sorted(addresses)


if __name__ == "__main__":
    print(json.dumps(query(int(sys.argv[1]))))
