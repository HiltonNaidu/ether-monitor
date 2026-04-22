"""
registry.py - Device Registry

Responsible for:
- Persisting devices to a local SQLite database
- CRUD operations on devices (add, remove, update, list)
- Resolving a MAC address or alias to a device record
- Updating live data (IP, last seen, last ping) after scans

Every CLI command that references a device passes through resolve() first,
which accepts either a MAC address or an alias interchangeably.
"""

import sqlite3
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# TODO: replace with get_config() once config resolution is sorted
DEFAULT_DB_PATH = "data/devices.db"


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Device:
    """Represents a single registered device."""
    mac:            str
    alias:          Optional[str]   = None
    ip:             Optional[str]   = None
    hostname:       Optional[str]   = None
    last_seen:      Optional[str]   = None   # UTC ISO string
    last_ping_ms:   Optional[float] = None   # Round-trip latency in ms
    is_online:      bool            = False
    added_at:       Optional[str]   = None   # UTC ISO string


class DeviceNotFoundError(Exception):
    """Raised when a MAC or alias does not match any registered device."""
    pass


class DuplicateAliasError(Exception):
    """Raised when assigning an alias that is already in use."""
    pass


class DuplicateMacError(Exception):
    """Raised when adding a MAC address that is already registered."""
    pass


# ── Registry ──────────────────────────────────────────────────────────────────

class Registry:
    """
    Manages the persistent device store backed by SQLite.

    Usage:
        registry = Registry()
        registry.add("AA:BB:CC:DD:EE:FF")
        device = registry.resolve("AA:BB:CC:DD:EE:FF")
        registry.set_alias("AA:BB:CC:DD:EE:FF", "gaming-pc")
        device = registry.resolve("gaming-pc")
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_db()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """
        Create the devices table if it doesn't already exist.
        Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
        """
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    mac           TEXT PRIMARY KEY,
                    alias         TEXT UNIQUE,
                    ip            TEXT,
                    hostname      TEXT,
                    last_seen     TEXT,
                    last_ping_ms  REAL,
                    is_online     INTEGER DEFAULT 0,
                    added_at      TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        """Open and return a connection with row_factory set for dict-like access."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Core CRUD ─────────────────────────────────────────────────────────────

    def add(self, mac: str, alias: Optional[str] = None) -> Device:
        """
        Register a new device by MAC address.

        Args:
            mac:   MAC address in any common format.
            alias: Optional human-readable name (e.g. "gaming-pc").

        Returns:
            The newly created Device.

        Raises:
            ValueError:        if the MAC address format is invalid.
            DuplicateMacError: if the MAC is already registered.
            DuplicateAliasError: if the alias is already taken.
        """
        mac = _normalise_mac(mac)
        now = _utcnow()

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO devices (mac, alias, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (mac, alias, now),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: devices.mac" in str(exc):
                raise DuplicateMacError(f"Device already registered: {mac}")
            if "UNIQUE constraint failed: devices.alias" in str(exc):
                raise DuplicateAliasError(f"Alias already in use: {alias}")
            raise

        logger.info("Device added: %s (alias=%s)", mac, alias)
        return Device(mac=mac, alias=alias, added_at=now)

    def remove(self, identifier: str) -> None:
        """
        Remove a device by MAC address or alias.

        Args:
            identifier: MAC address or alias of the device to remove.

        Raises:
            DeviceNotFoundError: if no device matches the identifier.
        """
        device = self.resolve(identifier)
        with self._connect() as conn:
            conn.execute("DELETE FROM devices WHERE mac = ?", (device.mac,))
        logger.info("Device removed: %s", device.mac)

    def get(self, identifier: str) -> Optional[Device]:
        """
        Look up a device by MAC or alias without raising on miss.

        Returns:
            Device if found, None otherwise.
        """
        mac = _normalise_mac(identifier) if _is_mac(identifier) else None

        with self._connect() as conn:
            if mac:
                row = conn.execute(
                    "SELECT * FROM devices WHERE mac = ?", (mac,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM devices WHERE alias = ?", (identifier,)
                ).fetchone()

        return _row_to_device(row) if row else None

    def resolve(self, identifier: str) -> Device:
        """
        Like get(), but raises DeviceNotFoundError on miss.
        All CLI commands call this so they get a consistent error.

        Args:
            identifier: MAC address or alias.

        Returns:
            Matching Device.

        Raises:
            DeviceNotFoundError: if no device matches.
        """
        device = self.get(identifier)
        if device is None:
            raise DeviceNotFoundError(
                f"No device found for identifier: '{identifier}'"
            )
        return device

    def list_all(self) -> list[Device]:
        """
        Return all registered devices, ordered by alias then MAC.

        Returns:
            List of Device objects (empty list if none registered).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY alias NULLS LAST, mac"
            ).fetchall()
        return [_row_to_device(row) for row in rows]

    # ── Updates ───────────────────────────────────────────────────────────────

    def set_alias(self, identifier: str, alias: str) -> Device:
        """
        Assign or update the alias for a device.

        Args:
            identifier: MAC address or current alias of the device.
            alias:      New alias to assign.

        Returns:
            Updated Device.

        Raises:
            DeviceNotFoundError: if the identifier doesn't match any device.
            DuplicateAliasError: if the alias is already taken by another device.
        """
        device = self.resolve(identifier)

        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE devices SET alias = ? WHERE mac = ?",
                    (alias, device.mac),
                )
        except sqlite3.IntegrityError:
            raise DuplicateAliasError(f"Alias already in use: '{alias}'")

        logger.info("Alias set: %s → %s", device.mac, alias)
        device.alias = alias
        return device

    def update_network_info(
        self,
        mac: str,
        ip: Optional[str]       = None,
        hostname: Optional[str] = None,
        is_online: bool         = False,
        ping_ms: Optional[float]= None,
    ) -> None:
        """
        Update live network data for a device after a scan or ping.
        Called by the scanner and ping commands — not by the user directly.

        Args:
            mac:       Normalised MAC address of the device to update.
            ip:        Current IP address if known.
            hostname:  Resolved hostname if known.
            is_online: Whether the device responded.
            ping_ms:   Round-trip latency in milliseconds.
        """
        mac = _normalise_mac(mac)
        now = _utcnow() if is_online else None

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE devices
                SET ip           = COALESCE(?, ip),
                    hostname     = COALESCE(?, hostname),
                    is_online    = ?,
                    last_ping_ms = COALESCE(?, last_ping_ms),
                    last_seen    = COALESCE(?, last_seen)
                WHERE mac = ?
                """,
                (ip, hostname, int(is_online), ping_ms, now, mac),
            )

        logger.debug("Network info updated for %s (online=%s)", mac, is_online)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_mac(mac: str) -> str:
    """
    Strip separators and return uppercase MAC string.
    Raises ValueError if the format is invalid.
    """
    cleaned = re.sub(r"[:\-\.]", "", mac).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", cleaned):
        raise ValueError(f"Invalid MAC address: '{mac}'")
    return cleaned


def _is_mac(identifier: str) -> bool:
    """Return True if the identifier looks like a MAC address."""
    cleaned = re.sub(r"[:\-\.]", "", identifier)
    return bool(re.fullmatch(r"[0-9A-F]{12}", cleaned.upper()))


def _utcnow() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.utcnow().isoformat()


def _row_to_device(row: sqlite3.Row) -> Device:
    """Convert a sqlite3.Row to a Device dataclass."""
    return Device(
        mac          = row["mac"],
        alias        = row["alias"],
        ip           = row["ip"],
        hostname     = row["hostname"],
        last_seen    = row["last_seen"],
        last_ping_ms = row["last_ping_ms"],
        is_online    = bool(row["is_online"]),
        added_at     = row["added_at"],
    )