"""Find this PC's LAN IPv4 addresses (to type into the phone app).
Nothing is sent on the network: connecting a UDP socket only picks a route."""

from __future__ import annotations

import socket


def lan_ipv4_addresses() -> list[str]:
    found: list[str] = []
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 9))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found
