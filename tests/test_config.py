"""Tests for azure_vm_tui.config module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from azure_vm_tui.config import (
    AppConfig,
    AzureConfig,
    SSHConfig,
    UIConfig,
    load_config,
)


def write_toml(tmp_path: Path, content: str) -> Path:
    """Write a TOML config file to tmp_path and return its path."""
    config_file = tmp_path / ".azure-vm-tui"
    config_file.write_text(content)
    return config_file


class TestDefaultConfig:
    def test_default_config(self) -> None:
        """No config file present returns all defaults."""
        with (
            patch.object(Path, "cwd", return_value=Path("/nonexistent/cwd")),
            patch.object(Path, "home", return_value=Path("/nonexistent/home")),
        ):
            config = load_config()

        assert config == AppConfig()
        assert config.azure == AzureConfig()
        assert config.ui == UIConfig()
        assert config.ssh == SSHConfig()

    def test_default_values(self) -> None:
        """Default values match the documented specification."""
        config = AppConfig()

        assert config.azure.subscription_id == ""
        assert config.azure.resource_group == ""
        assert config.ui.show_stats is False
        assert config.ui.refresh_interval == 5
        assert config.ui.fuzzy_threshold == 5
        assert config.ssh.user == "azureuser"
        assert config.ssh.key == "~/.ssh/id_rsa"
        assert config.ssh.timeout == 8


class TestPartialConfig:
    def test_partial_config_azure_only(self, tmp_path: Path) -> None:
        """Only [azure] section present — ui/ssh receive defaults."""
        config_file = write_toml(
            tmp_path,
            '[azure]\nsubscription_id = "abc-123"\nresource_group = "my-rg"\n',
        )
        config = load_config(str(config_file))

        assert config.azure.subscription_id == "abc-123"
        assert config.azure.resource_group == "my-rg"
        assert config.ui == UIConfig()
        assert config.ssh == SSHConfig()

    def test_partial_config_ui_only(self, tmp_path: Path) -> None:
        """Only [ui] section present — azure/ssh receive defaults."""
        config_file = write_toml(tmp_path, "[ui]\nshow_stats = true\n")
        config = load_config(str(config_file))

        assert config.ui.show_stats is True
        assert config.azure == AzureConfig()
        assert config.ssh == SSHConfig()

    def test_partial_config_missing_keys_within_section(self, tmp_path: Path) -> None:
        """Partial [ssh] section — missing keys get defaults."""
        config_file = write_toml(tmp_path, '[ssh]\nuser = "ubuntu"\n')
        config = load_config(str(config_file))

        assert config.ssh.user == "ubuntu"
        assert config.ssh.key == "~/.ssh/id_rsa"
        assert config.ssh.timeout == 8


class TestRefreshIntervalClamping:
    def test_refresh_interval_clamped_below_minimum(self, tmp_path: Path) -> None:
        """refresh_interval below 5 is clamped to 5."""
        config_file = write_toml(tmp_path, "[ui]\nrefresh_interval = 2\n")
        config = load_config(str(config_file))

        assert config.ui.refresh_interval == 5

    def test_refresh_interval_at_minimum_unchanged(self, tmp_path: Path) -> None:
        """refresh_interval exactly 5 is not changed."""
        config_file = write_toml(tmp_path, "[ui]\nrefresh_interval = 5\n")
        config = load_config(str(config_file))

        assert config.ui.refresh_interval == 5

    def test_refresh_interval_above_minimum_unchanged(self, tmp_path: Path) -> None:
        """refresh_interval above 5 is kept as-is."""
        config_file = write_toml(tmp_path, "[ui]\nrefresh_interval = 60\n")
        config = load_config(str(config_file))

        assert config.ui.refresh_interval == 60

    def test_refresh_interval_zero_clamped(self, tmp_path: Path) -> None:
        """refresh_interval = 0 is clamped to 5."""
        config_file = write_toml(tmp_path, "[ui]\nrefresh_interval = 0\n")
        config = load_config(str(config_file))

        assert config.ui.refresh_interval == 5


class TestFullConfig:
    def test_full_config_all_values_loaded(self, tmp_path: Path) -> None:
        """All config values present — all are loaded correctly."""
        config_file = write_toml(
            tmp_path,
            '\n'.join([
                '[azure]',
                'subscription_id = "sub-999"',
                'resource_group = "prod-rg"',
                '',
                '[ui]',
                'show_stats = true',
                'refresh_interval = 45',
                'fuzzy_threshold = 10',
                '',
                '[ssh]',
                'user = "ubuntu"',
                'key = "~/.ssh/azure_rsa"',
                'timeout = 15',
            ]),
        )
        config = load_config(str(config_file))

        assert config.azure.subscription_id == "sub-999"
        assert config.azure.resource_group == "prod-rg"
        assert config.ui.show_stats is True
        assert config.ui.refresh_interval == 45
        assert config.ui.fuzzy_threshold == 10
        assert config.ssh.user == "ubuntu"
        assert config.ssh.key == "~/.ssh/azure_rsa"
        assert config.ssh.timeout == 15


class TestConfigPathResolution:
    def test_config_path_override(self, tmp_path: Path) -> None:
        """Explicit path parameter is used directly."""
        config_file = write_toml(
            tmp_path,
            '[azure]\nsubscription_id = "explicit-sub"\n',
        )
        config = load_config(str(config_file))

        assert config.azure.subscription_id == "explicit-sub"

    def test_cwd_config_found_first(self, tmp_path: Path) -> None:
        """$PWD config is preferred over home config."""
        cwd_dir = tmp_path / "cwd"
        home_dir = tmp_path / "home"
        cwd_dir.mkdir()
        home_dir.mkdir()

        (cwd_dir / ".azure-vm-tui").write_text('[azure]\nsubscription_id = "from-cwd"\n')
        (home_dir / ".azure-vm-tui").write_text('[azure]\nsubscription_id = "from-home"\n')

        with (
            patch.object(Path, "cwd", return_value=cwd_dir),
            patch.object(Path, "home", return_value=home_dir),
        ):
            config = load_config()

        assert config.azure.subscription_id == "from-cwd"

    def test_home_config_used_when_no_cwd_config(self, tmp_path: Path) -> None:
        """Home config is used when no $PWD config exists."""
        cwd_dir = tmp_path / "cwd"
        home_dir = tmp_path / "home"
        cwd_dir.mkdir()
        home_dir.mkdir()

        (home_dir / ".azure-vm-tui").write_text('[azure]\nsubscription_id = "from-home"\n')

        with (
            patch.object(Path, "cwd", return_value=cwd_dir),
            patch.object(Path, "home", return_value=home_dir),
        ):
            config = load_config()

        assert config.azure.subscription_id == "from-home"


class TestMissingExplicitPath:
    def test_missing_explicit_path_raises(self, tmp_path: Path) -> None:
        """Explicit path to nonexistent file raises FileNotFoundError."""
        missing = str(tmp_path / "does-not-exist.toml")

        with pytest.raises(FileNotFoundError, match=r"does-not-exist\.toml"):
            load_config(missing)


class TestImmutability:
    def test_app_config_is_frozen(self) -> None:
        """AppConfig instances are immutable."""
        config = AppConfig()

        with pytest.raises(Exception):  # noqa: B017
            config.azure = AzureConfig(subscription_id="mutated")  # type: ignore[misc]

    def test_azure_config_is_frozen(self) -> None:
        """AzureConfig instances are immutable."""
        azure = AzureConfig()

        with pytest.raises(Exception):  # noqa: B017
            azure.subscription_id = "mutated"  # type: ignore[misc]

    def test_ui_config_is_frozen(self) -> None:
        """UIConfig instances are immutable."""
        ui = UIConfig()

        with pytest.raises(Exception):  # noqa: B017
            ui.show_stats = True  # type: ignore[misc]

    def test_ssh_config_is_frozen(self) -> None:
        """SSHConfig instances are immutable."""
        ssh = SSHConfig()

        with pytest.raises(Exception):  # noqa: B017
            ssh.user = "mutated"  # type: ignore[misc]
