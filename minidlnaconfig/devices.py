# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Which devices are talking to minidlna, and what they are streaming.

Three sources are combined, because none of them is complete on its own:

* minidlna's own status page lists the clients it has classified.
* ``ss`` shows the open connections to its port right now, with bytes sent,
  which is what tells "browsing" from "streaming".
* The log records devices that announced themselves over SSDP.

Standard library only, so it stays testable without a display server.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Tuple

LOOPBACK = {"127.0.0.1", "::1"}


@dataclass
class Device:
    ip: str
    kind: str = ""            # minidlna's classification, e.g. "Samsung TV"
    mac: str = ""
    connections: int = 0
    bytes_sent: int = 0
    announced: int = 0        # SSDP packets seen in the log
    last_seen: float = 0.0    # last SSDP packet, epoch seconds

    @property
    def streaming(self) -> bool:
        return self.connections > 0


@dataclass
class Connection:
    peer: str
    port: int
    bytes_sent: int = 0


@dataclass
class StatusPage:
    reachable: bool = False
    counts: Dict[str, int] = None
    clients: List[Tuple[str, str, str, int]] = None   # (kind, ip, mac, connections)
    error: str = ""

    def __post_init__(self):
        self.counts = self.counts or {}
        self.clients = self.clients or []


# --------------------------------------------------------------------- parsing


def _cells(row_html: str) -> List[str]:
    return [html.unescape(re.sub(r"<[^>]*>", "", c)).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.I | re.S)]


def parse_status_page(page: str) -> StatusPage:
    """Parse minidlna's HTML status page."""
    result = StatusPage(reachable=True)
    sections = re.split(r"<h3[^>]*>", page, flags=re.I)
    for section in sections[1:]:
        title = re.sub(r"<[^>]*>.*", "", section, flags=re.S).strip().lower()
        rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", section, re.I | re.S)]
        if title.startswith("media library"):
            for cells in rows:
                if len(cells) == 2 and cells[1].isdigit():
                    result.counts[cells[0].lower()] = int(cells[1])
        elif title.startswith("connected clients"):
            for cells in rows[1:]:                      # first row is the header
                if len(cells) >= 5:
                    try:
                        connections = int(cells[4])
                    except ValueError:
                        connections = 0
                    result.clients.append((cells[1], cells[2], cells[3], connections))
    return result


def fetch_status_page(port: int, timeout: float = 2.0) -> StatusPage:
    """Read the status page. Addressed by IP: minidlna refuses hostnames."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return StatusPage(reachable=False, error=str(exc))
    return parse_status_page(body)


def parse_ss(output: str) -> List[Connection]:
    """Parse ``ss -tin`` output into one entry per connection."""
    connections: List[Connection] = []
    current = None
    for line in output.splitlines():
        if not line.strip() or line.startswith("Recv-Q"):
            continue
        if line[:1] in (" ", "\t"):
            if current is not None:
                match = re.search(r"\bbytes_sent:(\d+)", line)
                if match:
                    current.bytes_sent = int(match.group(1))
            continue
        fields = line.split()
        if len(fields) < 4:
            current = None
            continue
        peer = fields[-1]
        host, _, port = peer.rpartition(":")
        host = host.strip("[]")
        try:
            current = Connection(host, int(port))
        except ValueError:
            current = None
            continue
        connections.append(current)
    return connections


def open_connections(port: int) -> List[Connection]:
    """Connections to minidlna's port right now. Empty when ``ss`` is missing."""
    ss = shutil.which("ss")
    if not ss:
        return []
    try:
        result = subprocess.run(
            [ss, "-tin", "state", "established", f"( sport = :{port} )"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ss(result.stdout) if result.returncode == 0 else []


def neighbours() -> Dict[str, str]:
    """ip -> MAC address from the kernel's neighbour table."""
    ip = shutil.which("ip")
    if not ip:
        return {}
    try:
        result = subprocess.run([ip, "neigh", "show"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if "lladdr" in fields:
            table[fields[0]] = fields[fields.index("lladdr") + 1]
    return table


# ----------------------------------------------------------------------- merge


def merge(
    status: StatusPage,
    connections: List[Connection],
    ssdp: Dict[str, Tuple[int, float]],
    macs: Dict[str, str],
) -> List[Device]:
    """Combine the three sources into one list, busiest devices first."""
    devices: Dict[str, Device] = {}

    def get(ip: str) -> Device:
        if ip not in devices:
            devices[ip] = Device(ip=ip, mac=macs.get(ip, ""))
        return devices[ip]

    for kind, ip, mac, count in status.clients:
        if ip in LOOPBACK:
            continue
        device = get(ip)
        device.kind = kind if kind.lower() != "unknown" else device.kind
        if mac and mac.upper() != "FF:FF:FF:FF:FF:FF":
            device.mac = mac
        device.connections = max(device.connections, count)

    for connection in connections:
        if connection.peer in LOOPBACK:
            continue
        device = get(connection.peer)
        device.bytes_sent += connection.bytes_sent
        device.connections = max(device.connections, 1)

    for ip, (count, last) in ssdp.items():
        if ip in LOOPBACK:
            continue
        device = get(ip)
        device.announced = count
        device.last_seen = last

    return sorted(
        devices.values(),
        key=lambda d: (-d.connections, -d.bytes_sent, -d.last_seen, d.ip),
    )


def format_rate(bytes_per_second: float) -> str:
    """``3.2 MB/s`` in decimal units, the way network speeds are quoted."""
    if bytes_per_second < 1000:
        return f"{bytes_per_second:.0f} B/s"
    for unit in ("kB/s", "MB/s", "GB/s"):
        bytes_per_second /= 1000
        if bytes_per_second < 1000:
            return f"{bytes_per_second:.1f} {unit}"
    return f"{bytes_per_second:.1f} TB/s"
