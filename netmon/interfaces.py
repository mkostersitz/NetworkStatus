"""Network interface detection, classification, and preference."""
import socket
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Optional

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# Lower number = higher priority
_PRIORITY = {"wired": 0, "wifi": 1, "vpn": 2, "other": 3}

_VPN_PATTERNS  = ("vpn", "tun", "tap", "utun", "ppp", "ipsec", "wg", "wireguard",
                  "nordlynx", "tailscale", "zerotier", "proton")
_WIFI_PATTERNS = ("wi-fi", "wifi", "wlan", "wireless", "airport", "802.11")
_WIRE_PATTERNS = ("eth", "ethernet", "lan", "em0", "em1", "eno", "enp", "igc",
                  "igb", "e1000", "vmxnet", "virtio")


@dataclass
class NetInterface:
    name: str
    kind: str   # wired | wifi | vpn | other
    ip: str
    label: str = field(default="")   # human-readable, e.g. "Wi-Fi" on macOS

    def display(self) -> str:
        base = self.label or self.name
        return f"{base} ({self.kind})"


def _classify_by_name(name: str) -> str:
    n = name.lower()
    if any(p in n for p in _VPN_PATTERNS):
        return "vpn"
    if any(p in n for p in _WIFI_PATTERNS):
        return "wifi"
    if any(p in n for p in _WIRE_PATTERNS):
        return "wired"
    return "other"


def _mac_hw_ports() -> dict[str, str]:
    """Return {ifname: human_label} from networksetup on macOS."""
    try:
        out = subprocess.check_output(
            ["networksetup", "-listallhardwareports"],
            text=True, timeout=5, stderr=subprocess.DEVNULL
        )
        result = {}
        label = None
        for line in out.splitlines():
            if line.startswith("Hardware Port:"):
                label = line.split(":", 1)[1].strip()
            elif line.startswith("Device:") and label:
                result[line.split(":", 1)[1].strip()] = label
                label = None
        return result
    except Exception:
        return {}


def _mac_classify(name: str, label: str) -> str:
    l = label.lower()
    if any(p in l for p in _VPN_PATTERNS):
        return "vpn"
    if "wi-fi" in l or "airport" in l or "wireless" in l:
        return "wifi"
    if "ethernet" in l or "thunderbolt" in l or "usb 10/100" in l:
        return "wired"
    return _classify_by_name(name)


def list_interfaces() -> list[NetInterface]:
    """Return all up IPv4 interfaces, sorted by preference (wired first)."""
    if not _HAS_PSUTIL:
        return []

    hw_ports = _mac_hw_ports() if platform.system() == "Darwin" else {}
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    ifaces: list[NetInterface] = []

    for name, addr_list in addrs.items():
        st = stats.get(name)
        if not st or not st.isup:
            continue
        for addr in addr_list:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            label = hw_ports.get(name, "")
            kind = _mac_classify(name, label) if label else _classify_by_name(name)
            ifaces.append(NetInterface(name=name, kind=kind, ip=ip, label=label))

    return sorted(ifaces, key=lambda i: _PRIORITY.get(i.kind, 99))


def source_ip_for(target: str = "8.8.8.8") -> Optional[str]:
    """Return the local IP the OS would use to reach target."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 53))
            return s.getsockname()[0]
    except Exception:
        return None


def active_interface(target: str = "8.8.8.8") -> Optional[NetInterface]:
    """Return the interface currently used to reach target."""
    src = source_ip_for(target)
    if not src:
        return None
    for iface in list_interfaces():
        if iface.ip == src:
            return iface
    return None


def preferred_interface(target: str = "8.8.8.8") -> Optional[NetInterface]:
    """
    Return the best available interface (wired > wifi > vpn > other).
    Falls back to the active interface if nothing better exists.
    """
    ifaces = list_interfaces()
    if not ifaces:
        return None
    best = ifaces[0]
    current = active_interface(target)
    # Only prefer a different interface if it's strictly better
    if current and _PRIORITY.get(current.kind, 99) <= _PRIORITY.get(best.kind, 99):
        return current
    return best
