"""
wol.py - Wake-on-LAN engine

Responsible for:
- Validating and normalising MAC addresses
- Constructing the magic packet
- Broadcasting it over UDP
- (Optionally) verifying the target came online after waking
"""

import socket
import struct
import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
#TODO: get basic defaults from config instead of hardcoding here
WOL_PORT       = 9          # Standard WoL UDP port (also 7 is common)
BROADCAST_IP   = "255.255.255.255"
MAGIC_PREFIX   = b"\xff" * 6
MAC_REPEAT     = 16         # MAC is repeated 16 times in the magic packet


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class WakeResult:
    """Returned by send_magic_packet() to give the caller structured feedback."""
    success: bool
    mac: str
    broadcast_ip: str
    port: int
    message: str = ""


# ── MAC utilities ────────────────────────────────────────────────────────────

def normalise_mac(mac: str) -> str:
    """
    Accept any common MAC format and return a clean uppercase hex string.

    Accepted inputs:
        "AA:BB:CC:DD:EE:FF"
        "aa-bb-cc-dd-ee-ff"
        "AABBCCDDEEFF"

    Returns:
        "AABBCCDDEEFF"

    Raises:
        ValueError: if the MAC address is not valid.
    """
    # Strip separators and uppercase
    cleaned = re.sub(r"[:\-\.]", "", mac).upper()

    if not re.fullmatch(r"[0-9A-F]{12}", cleaned):
        raise ValueError(f"Invalid MAC address: '{mac}'")

    return cleaned


def mac_to_bytes(mac: str) -> bytes:
    """
    Convert a normalised MAC string to a 6-byte bytes object.

    Example:
        "AABBCCDDEEFF" → b"\xaa\xbb\xcc\xdd\xee\xff"
    """
    normalised = normalise_mac(mac)
    return bytes.fromhex(normalised)


# ── Magic packet ─────────────────────────────────────────────────────────────

def build_magic_packet(mac: str) -> bytes:
    """
    Construct the WoL magic packet for the given MAC address.

    Structure:
        6 bytes of 0xFF  +  (MAC × 16)  =  102 bytes total

    Args:
        mac: MAC address in any supported format.

    Returns:
        102-byte magic packet ready to be broadcast.
    """
    mac_bytes = mac_to_bytes(mac)
    return MAGIC_PREFIX + mac_bytes * MAC_REPEAT


# ── Sending ──────────────────────────────────────────────────────────────────

def send_magic_packet(
    mac: str,
    broadcast_ip: str = BROADCAST_IP,
    port: int = WOL_PORT,
) -> WakeResult:
    """
    Broadcast a magic packet to wake the device with the given MAC address.

    Args:
        mac:          Target device MAC address.
        broadcast_ip: Broadcast address (default: 255.255.255.255).
                      Can be a subnet broadcast e.g. "192.168.1.255" for
                      directed broadcast across routers.
        port:         UDP port to send on (default: 9).

    Returns:
        WakeResult dataclass with success flag and metadata.
    """
    try:
        packet = build_magic_packet(mac)
        _broadcast_packet(packet, broadcast_ip, port)

        logger.info("Magic packet sent → %s via %s:%d", mac, broadcast_ip, port)
        return WakeResult(
            success=True,
            mac=normalise_mac(mac),
            broadcast_ip=broadcast_ip,
            port=port,
            message="Magic packet sent successfully.",
        )

    except ValueError as exc:
        logger.warning("WoL failed (invalid MAC): %s", exc)
        return WakeResult(
            success=False,
            mac=mac,
            broadcast_ip=broadcast_ip,
            port=port,
            message=str(exc),
        )
    except OSError as exc:
        logger.error("WoL failed (socket error): %s", exc)
        return WakeResult(
            success=False,
            mac=mac,
            broadcast_ip=broadcast_ip,
            port=port,
            message=f"Socket error: {exc}",
        )


def _broadcast_packet(packet: bytes, broadcast_ip: str, port: int) -> None:
    """
    Internal helper — opens a UDP broadcast socket and sends the packet.

    Separated from send_magic_packet() so it can be patched in unit tests.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.connect((broadcast_ip, port))
        sock.send(packet)


# ── Optional: verify device came online ──────────────────────────────────────

def wait_for_device(
    host: str,
    timeout: int = 60,
    interval: int = 5,
) -> bool:
    """
    Poll the target host via ICMP/TCP until it responds or timeout is reached.
    Useful to confirm the wake actually worked.

    Args:
        host:     IP address or hostname to poll.
        timeout:  Total seconds to wait before giving up.
        interval: Seconds between each poll attempt.

    Returns:
        True if the device responded within timeout, False otherwise.

    Note:
        Stub — implement with `ping3`, a raw socket ping, or a TCP connect
        to a known-open port on the target device.
    """
    raise NotImplementedError


def _ping(host: str) -> bool:
    """
    Internal helper — returns True if host responds to a single ping.

    Note:
        Stub — implement with `ping3.ping()` or subprocess `ping`.
    """
    raise NotImplementedError