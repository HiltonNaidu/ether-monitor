"""
loader.py - Configuration loader

Loads config.yaml (or a custom path via env var) and exposes typed,
validated config sections as dataclasses. All other modules should
import from here rather than reading the file themselves.

Usage:
    from core.config import get_config

    cfg = get_config()
    print(cfg.wol.port)         # 9
    print(cfg.network.subnet)   # "192.168.1.0/24"
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml

logger = logging.getLogger(__name__)

# ── Env var that lets users point at a custom config file ─────────────────────

CONFIG_ENV_VAR  = "LAN_MANAGER_CONFIG"
CONFIG_DEFAULT  = Path(__file__).parent.parent / "config.yaml"


# ── Section dataclasses ───────────────────────────────────────────────────────

@dataclass
class NetworkConfig:
    interface:    str = "0.0.0.0"
    broadcast_ip: str = "255.255.255.255"
    subnet:       str = "192.168.1.0/24"


@dataclass
class WolConfig:
    port:                    int   = 9
    repeat:                  int   = 1
    repeat_interval_seconds: float = 0.5
    verify_wake:             bool  = False
    verify_timeout_seconds:  int   = 60
    verify_interval_seconds: int   = 5


@dataclass
class ScannerConfig:
    enabled:          bool  = True
    interval_seconds: int   = 30
    method:           str   = "arp"
    timeout_seconds:  float = 2.0
    workers:          int   = 50


@dataclass
class RegistryConfig:
    backend:     str = "sqlite"
    sqlite_path: str = "data/devices.db"
    json_path:   str = "data/devices.json"


@dataclass
class ApiConfig:
    host:         str        = "0.0.0.0"
    port:         int        = 8000
    docs_enabled: bool       = True
    cors_origins: list[str]  = field(default_factory=lambda: ["http://localhost:3000"])


@dataclass
class LoggingConfig:
    level:     str = "INFO"
    format:    str = "text"
    file_path: str = ""


@dataclass
class AppConfig:
    """Root config object. Access all settings through this."""
    network:  NetworkConfig  = field(default_factory=NetworkConfig)
    wol:      WolConfig      = field(default_factory=WolConfig)
    scanner:  ScannerConfig  = field(default_factory=ScannerConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    api:      ApiConfig      = field(default_factory=ApiConfig)
    logging:  LoggingConfig  = field(default_factory=LoggingConfig)


# ── Loader ────────────────────────────────────────────────────────────────────

def load_config(path: Optional[Path] = None) -> AppConfig:
    """
    Load and validate config from a YAML file.

    Resolution order for the config file path:
        1. `path` argument (if provided)
        2. LAN_MANAGER_CONFIG environment variable
        3. config.yaml in the project root (default)

    Unknown keys in the YAML are ignored — this allows newer config files
    to work with older versions of the app without crashing.

    Args:
        path: Explicit path to a config.yaml file.

    Returns:
        Fully populated AppConfig dataclass.

    Raises:
        FileNotFoundError: if the resolved config path does not exist.
        yaml.YAMLError:    if the file is not valid YAML.
    """
    resolved = _resolve_path(path)
    logger.info("Loading config from: %s", resolved)

    raw = _read_yaml(resolved)
    return _parse_config(raw)


def _resolve_path(explicit: Optional[Path]) -> Path:
    if explicit:
        return Path(explicit)

    env_path = os.getenv(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path)

    return CONFIG_DEFAULT


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _parse_config(raw: dict) -> AppConfig:
    """
    Map raw YAML dict → typed dataclasses.
    Missing keys fall back to dataclass defaults.
    """
    return AppConfig(
        network  = _parse_section(NetworkConfig,  raw.get("network",  {})),
        wol      = _parse_section(WolConfig,       raw.get("wol",      {})),
        scanner  = _parse_section(ScannerConfig,   raw.get("scanner",  {})),
        registry = _parse_section(RegistryConfig,  raw.get("registry", {})),
        api      = _parse_section(ApiConfig,        raw.get("api",      {})),
        logging  = _parse_section(LoggingConfig,   raw.get("logging",  {})),
    )


def _parse_section(cls, data: dict):
    """
    Instantiate a dataclass from a dict, ignoring any keys
    that don't exist as fields on the class.
    """
    valid_fields = {f for f in cls.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


# ── Singleton ─────────────────────────────────────────────────────────────────
# Modules call get_config() rather than load_config() directly so the file
# is only read once per process. Call reload_config() in tests or if you
# support runtime config reloads.

_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Return the cached AppConfig, loading it on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(path: Optional[Path] = None) -> AppConfig:
    """Force a fresh load from disk and update the cache."""
    global _config
    _config = load_config(path)
    return _config