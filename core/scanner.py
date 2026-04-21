"""
scanner.py - Network Scanner

Responsible for:
- Scanning the local subnet to discover active devices
- Resolving MAC addresses and hostnames for discovered devices
- Returning structured scan results to the caller (API-triggered, not background)

Scan strategy:
    1. Attempt ARP sweep (fast, accurate, requires root/sudo)
    2. Fall back to ICMP ping sweep per-host if ARP is unavailable or fails

Dependencies:
    scapy  - ARP scanning  (pip install scapy)
    ping3  - ICMP ping     (pip install ping3)
"""

import logging
import socket
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# TODO: replace with get_config() once config resolution is sorted
DEFAULT_SUBNET          = "192.168.1.0/24"
DEFAULT_TIMEOUT         = 2       # seconds per host
DEFAULT_WORKERS         = 50      # parallel threads for ping sweep
ARP_UNAVAILABLE_REASON  = ""      # populated at import time if scapy is missing


# ── Optional dependency guards ────────────────────────────────────────────────
# Scapy and ping3 are imported lazily so the module doesn't hard-crash if
# they aren't installed yet. Each scan method checks availability before use.

try:
    from scapy.all import ARP, Ether, srp
    ARP_AVAILABLE = True
except ImportError:
    ARP_AVAILABLE = False
    ARP_UNAVAILABLE_REASON = "scapy not installed — run: pip install scapy"
    logger.warning(ARP_UNAVAILABLE_REASON)

try:
    import ping3
    PING_AVAILABLE = True
except ImportError:
    PING_AVAILABLE = False
    logger.warning("ping3 not installed — run: pip install ping3")


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class DiscoveredDevice:
    """Represents a single device found during a scan."""
    ip:         str
    mac:        Optional[str]       = None    # Not available from ping-only scans
    hostname:   Optional[str]       = None
    method:     str                 = ""      # "arp" or "ping"
    latency_ms: Optional[float]     = None    # Round-trip time (ping only)


@dataclass
class ScanResult:
    """Returned by run_scan() — full picture of a single scan run."""
    subnet:         str
    devices:        list[DiscoveredDevice]  = field(default_factory=list)
    method_used:    str                     = ""      # "arp" or "ping"
    scan_duration_seconds: float            = 0.0
    timestamp:      str                     = field(
                        default_factory=lambda: datetime.utcnow().isoformat()
                    )
    error:          Optional[str]           = None


# ── Public entry point ────────────────────────────────────────────────────────

def run_scan(
    subnet: str = DEFAULT_SUBNET,
    timeout: int = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
) -> ScanResult:
    """
    Scan the subnet and return all discovered devices.

    Tries ARP first (requires root). If ARP is unavailable or raises an
    exception, falls back to a concurrent ICMP ping sweep.

    Args:
        subnet:  CIDR subnet to scan, e.g. "192.168.1.0/24".
        timeout: Per-host timeout in seconds.
        workers: Thread pool size for ping sweep fallback.

    Returns:
        ScanResult with a list of DiscoveredDevice entries.
    """
    start = datetime.utcnow()

    if ARP_AVAILABLE:
        logger.info("Starting ARP scan on %s", subnet)
        result = _arp_scan(subnet, timeout)
    else:
        logger.info("ARP unavailable, falling back to ping sweep on %s", subnet)
        result = _ping_sweep(subnet, timeout, workers)

    result.scan_duration_seconds = (
        datetime.utcnow() - start
    ).total_seconds()

    logger.info(
        "Scan complete — %d device(s) found in %.2fs via %s",
        len(result.devices),
        result.scan_duration_seconds,
        result.method_used,
    )
    return result


# ── ARP scan ──────────────────────────────────────────────────────────────────

def _arp_scan(subnet: str, timeout: int) -> ScanResult:
    """
    Broadcast ARP requests across the subnet and collect replies.

    Each reply gives us an IP + MAC pair. Hostnames are resolved separately.
    Requires root/sudo — scapy needs raw socket access.

    Args:
        subnet:  CIDR subnet string.
        timeout: Seconds to wait for ARP replies.

    Returns:
        ScanResult with method_used = "arp".
    """
    try:
        packet   = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet)
        answered, _ = srp(packet, timeout=timeout, verbose=False)

        devices = []
        for _, reply in answered:
            ip  = reply[ARP].psrc
            mac = reply[ARP].hwsrc
            devices.append(DiscoveredDevice(
                ip       = ip,
                mac      = mac,
                hostname = _resolve_hostname(ip),
                method   = "arp",
            ))

        return ScanResult(subnet=subnet, devices=devices, method_used="arp")

    except PermissionError:
        logger.warning("ARP scan requires root. Falling back to ping sweep.")
        return _ping_sweep(subnet, timeout)

    except Exception as exc:
        logger.error("ARP scan failed: %s. Falling back to ping sweep.", exc)
        return _ping_sweep(subnet, timeout)


# ── Ping sweep ────────────────────────────────────────────────────────────────

def _ping_sweep(
    subnet: str,
    timeout: int = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
) -> ScanResult:
    """
    Ping every host in the subnet concurrently and collect responses.

    No root required. Slower than ARP and does not yield MAC addresses,
    but works on any system with ping3 installed.

    Args:
        subnet:  CIDR subnet string.
        timeout: Per-host ping timeout in seconds.
        workers: Number of parallel threads.

    Returns:
        ScanResult with method_used = "ping".
    """
    if not PING_AVAILABLE:
        return ScanResult(
            subnet=subnet,
            method_used="ping",
            error="ping3 not installed — run: pip install ping3",
        )

    hosts   = _expand_subnet(subnet)
    devices = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_ping_host, ip, timeout): ip for ip in hosts}

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                devices.append(result)

    return ScanResult(subnet=subnet, devices=devices, method_used="ping")


def _ping_host(ip: str, timeout: int) -> Optional[DiscoveredDevice]:
    """
    Ping a single host. Returns a DiscoveredDevice if it responds, else None.

    Args:
        ip:      Target IP address.
        timeout: Seconds before giving up.

    Returns:
        DiscoveredDevice on success, None if host is unreachable.
    """
    try:
        latency = ping3.ping(ip, timeout=timeout, unit="ms")
        if latency is not None:
            return DiscoveredDevice(
                ip         = ip,
                hostname   = _resolve_hostname(ip),
                method     = "ping",
                latency_ms = latency,
            )
    except Exception as exc:
        logger.debug("Ping failed for %s: %s", ip, exc)

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _expand_subnet(subnet: str) -> list[str]:
    """
    Return a list of all host IP addresses in the given CIDR subnet.
    Excludes the network address and broadcast address.

    Example:
        "192.168.1.0/24" → ["192.168.1.1", ..., "192.168.1.254"]
    """
    import ipaddress
    network = ipaddress.IPv4Network(subnet, strict=False)
    return [str(ip) for ip in network.hosts()]


def _resolve_hostname(ip: str) -> Optional[str]:
    """
    Attempt a reverse DNS lookup for the given IP.
    Returns the hostname string, or None if lookup fails.
    """
    try:
        return socket.gethostbyaddr(ip)[0]
    except socket.herror:
        return None