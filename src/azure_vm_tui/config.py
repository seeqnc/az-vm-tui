"""Configuration loading for azure-vm-tui.

Searches for .azure-vm-tui config file in $PWD then ~/.azure-vm-tui.
All settings have sensible defaults and missing keys are filled in automatically.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = ".azure-vm-tui"
REFRESH_INTERVAL_MIN = 5


@dataclass(frozen=True)
class AzureConfig:
    """Azure subscription and resource group settings."""

    subscription_id: str = ""
    resource_group: str = ""


@dataclass(frozen=True)
class UIConfig:
    """UI display and behaviour settings."""

    show_stats: bool = False
    refresh_interval: int = 5
    fuzzy_threshold: int = 5


@dataclass(frozen=True)
class SSHConfig:
    """SSH connection settings for stats collection."""

    user: str = "azureuser"
    key: str = "~/.ssh/id_rsa"
    timeout: int = 8


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    azure: AzureConfig = field(default_factory=AzureConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    ssh: SSHConfig = field(default_factory=SSHConfig)


def _resolve_config_path() -> Path | None:
    """Search standard locations for a config file.

    Returns:
        Path to the first config file found, or None if none exist.
    """
    candidates = [
        Path.cwd() / CONFIG_FILENAME,
        Path.home() / CONFIG_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_azure(raw: dict) -> AzureConfig:
    """Build AzureConfig from a raw TOML section dict.

    Args:
        raw: Dict from the [azure] TOML section (may be empty).

    Returns:
        AzureConfig with values from raw, defaults for missing keys.
    """
    defaults = AzureConfig()
    return AzureConfig(
        subscription_id=raw.get("subscription_id", defaults.subscription_id),
        resource_group=raw.get("resource_group", defaults.resource_group),
    )


def _parse_ui(raw: dict) -> UIConfig:
    """Build UIConfig from a raw TOML section dict.

    Args:
        raw: Dict from the [ui] TOML section (may be empty).

    Returns:
        UIConfig with values from raw, defaults for missing keys, and validated ranges.
    """
    defaults = UIConfig()
    refresh_interval = raw.get("refresh_interval", defaults.refresh_interval)
    return UIConfig(
        show_stats=raw.get("show_stats", defaults.show_stats),
        refresh_interval=max(REFRESH_INTERVAL_MIN, refresh_interval),
        fuzzy_threshold=raw.get("fuzzy_threshold", defaults.fuzzy_threshold),
    )


def _parse_ssh(raw: dict) -> SSHConfig:
    """Build SSHConfig from a raw TOML section dict.

    Args:
        raw: Dict from the [ssh] TOML section (may be empty).

    Returns:
        SSHConfig with values from raw, defaults for missing keys.
    """
    defaults = SSHConfig()
    return SSHConfig(
        user=raw.get("user", defaults.user),
        key=raw.get("key", defaults.key),
        timeout=raw.get("timeout", defaults.timeout),
    )


def _parse_toml(data: dict) -> AppConfig:
    """Build AppConfig from parsed TOML data.

    Args:
        data: Top-level dict from a parsed TOML file.

    Returns:
        AppConfig with all sections merged with defaults.
    """
    return AppConfig(
        azure=_parse_azure(data.get("azure", {})),
        ui=_parse_ui(data.get("ui", {})),
        ssh=_parse_ssh(data.get("ssh", {})),
    )


def load_config(path: str | None = None) -> AppConfig:
    """Load application configuration from a TOML file.

    Searches in order: explicit path (if given), $PWD/.azure-vm-tui,
    ~/.azure-vm-tui. The first file found is used. Missing keys receive
    defaults. If no config file is found, returns all defaults.

    Args:
        path: Optional explicit path to a config file. If provided, no
            fallback search is performed and FileNotFoundError is raised
            if the file does not exist.

    Returns:
        AppConfig populated from the config file and defaults.

    Raises:
        FileNotFoundError: If an explicit path was given but does not exist.
        tomllib.TOMLDecodeError: If the config file is not valid TOML.
    """
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"Config file not found: {config_path}")
    else:
        config_path = _resolve_config_path()
        if config_path is None:
            return AppConfig()

    with config_path.open("rb") as fh:
        data = tomllib.load(fh)

    return _parse_toml(data)
