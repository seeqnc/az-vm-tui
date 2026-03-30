"""Tests for the az CLI wrapper module."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

import azure_vm_tui.az
from azure_vm_tui.az import (
    AutoShutdownInfo,
    AzError,
    VMInfo,
    _get_subscription_id,
    check_az_cli,
    check_login,
    disable_auto_shutdown,
    enable_auto_shutdown,
    get_auto_shutdown,
    get_subscription,
    get_vm_ip,
    list_vms,
    start_vm,
    stop_vm,
)
from azure_vm_tui.validators import validate_shutdown_time


@pytest.fixture(autouse=True)
def _reset_subscription_cache() -> None:
    """Reset the lru_cache on _get_subscription_id between tests."""
    azure_vm_tui.az._get_subscription_id.cache_clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIST_VMS_JSON = json.dumps(
    [
        {
            "name": "prod-web-01",
            "resourceGroup": "prod-rg",
            "powerState": "VM running",
            "location": "eastus",
            "hardwareProfile": {"vmSize": "Standard_D4s_v3"},
            "storageProfile": {"osDisk": {"osType": "Linux"}},
            "tags": {"env": "prod"},
        },
        {
            "name": "dev-gpu-01",
            "resourceGroup": "dev-rg",
            "powerState": "VM deallocated",
            "location": "westeurope",
            "hardwareProfile": {"vmSize": "Standard_NC6"},
            "storageProfile": {"osDisk": {"osType": "Linux"}},
            "tags": {},
        },
        {
            "name": "staging-api",
            "resourceGroup": "staging-rg",
            "powerState": "VM stopped",
            "location": "eastus",
            "hardwareProfile": {"vmSize": "Standard_B2s"},
            "storageProfile": {"osDisk": {"osType": "Windows"}},
            "tags": {"env": "staging", "team": "platform"},
        },
    ]
)

ACCOUNT_JSON = json.dumps({"name": "My Subscription", "id": "abc-123", "tenantId": "tenant-xyz"})

IP_ADDRESSES_JSON = json.dumps(
    [
        {
            "virtualMachine": {
                "network": {
                    "publicIpAddresses": [
                        {"ipAddress": "52.1.2.3", "name": "prod-web-01-ip"},
                    ]
                }
            }
        }
    ]
)

IP_ADDRESSES_EMPTY_JSON = json.dumps(
    [
        {
            "virtualMachine": {
                "network": {
                    "publicIpAddresses": [],
                }
            }
        }
    ]
)


def _ok(stdout: str) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "Something went wrong", code: int = 1) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=code, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# check_az_cli
# ---------------------------------------------------------------------------


def test_check_az_cli_found() -> None:
    """No error is raised when az is on PATH."""
    with patch("azure_vm_tui.az.shutil.which", return_value="/usr/bin/az"):
        check_az_cli()  # must not raise


def test_check_az_cli_not_found() -> None:
    """AzError is raised when az is not on PATH."""
    with patch("azure_vm_tui.az.shutil.which", return_value=None), pytest.raises(AzError) as exc_info:
        check_az_cli()
    assert "https://aka.ms/installazurecli" in exc_info.value.stderr


# ---------------------------------------------------------------------------
# check_login
# ---------------------------------------------------------------------------


def test_check_login_success() -> None:
    """No error is raised when az account show succeeds."""
    with patch("subprocess.run", return_value=_ok(ACCOUNT_JSON)):
        check_login()  # must not raise


def test_check_login_failure() -> None:
    """AzError is raised with an az login hint when not authenticated."""
    with patch("subprocess.run", return_value=_fail("Please run 'az login'")), pytest.raises(AzError) as exc_info:
        check_login()
    assert "az login" in exc_info.value.stderr


# ---------------------------------------------------------------------------
# get_subscription
# ---------------------------------------------------------------------------


def test_get_subscription() -> None:
    """Returns the subscription name from az account show output."""
    with patch("subprocess.run", return_value=_ok(ACCOUNT_JSON)):
        result = get_subscription()
    assert result == "My Subscription"


# ---------------------------------------------------------------------------
# list_vms
# ---------------------------------------------------------------------------


def test_list_vms_parses_json() -> None:
    """Correctly maps all fields from az vm list JSON to VMInfo objects."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)):
        vms = list_vms()

    assert len(vms) == 3

    first = vms[0]
    assert first.name == "prod-web-01"
    assert first.resource_group == "prod-rg"
    assert first.power_state == "running"
    assert first.location == "eastus"
    assert first.vm_size == "Standard_D4s_v3"
    assert first.os_type == "Linux"
    assert first.tags == {"env": "prod"}

    second = vms[1]
    assert second.name == "dev-gpu-01"
    assert second.power_state == "deallocated"
    assert second.tags == {}

    third = vms[2]
    assert third.os_type == "Windows"
    assert third.tags == {"env": "staging", "team": "platform"}


def test_list_vms_with_resource_group() -> None:
    """Passes --resource-group flag when resource_group is specified."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)) as mock_run:
        list_vms(resource_group="prod-rg")

    called_args = mock_run.call_args[0][0]
    assert "--resource-group" in called_args
    assert "prod-rg" in called_args


def test_list_vms_with_subscription_id() -> None:
    """Passes --subscription flag when subscription_id is specified."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)) as mock_run:
        list_vms(subscription_id="my-sub-id")

    called_args = mock_run.call_args[0][0]
    assert "--subscription" in called_args
    assert "my-sub-id" in called_args


def test_list_vms_empty() -> None:
    """Returns an empty list when az returns an empty JSON array."""
    with patch("subprocess.run", return_value=_ok("[]")):
        vms = list_vms()
    assert vms == []


def test_list_vms_normalizes_power_state() -> None:
    """Power state strings are stripped of 'VM ' prefix and lowercased."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)):
        vms = list_vms()

    states = {vm.power_state for vm in vms}
    assert "running" in states
    assert "deallocated" in states
    assert "stopped" in states
    # Raw values like "VM running" must not appear
    for state in states:
        assert not state.startswith("VM "), f"Unnormalised state: {state!r}"


def test_list_vms_without_details() -> None:
    """Omits --show-details when show_details=False."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)) as mock_run:
        list_vms(show_details=False)

    called_args = mock_run.call_args[0][0]
    assert "--show-details" not in called_args


def test_list_vms_with_details_by_default() -> None:
    """Includes --show-details by default."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)) as mock_run:
        list_vms()

    called_args = mock_run.call_args[0][0]
    assert "--show-details" in called_args


# ---------------------------------------------------------------------------
# start_vm
# ---------------------------------------------------------------------------


def test_start_vm_command() -> None:
    """Passes correct args including --no-wait to az vm start."""
    with patch("subprocess.run", return_value=_ok("{}")) as mock_run:
        start_vm(name="prod-web-01", resource_group="prod-rg")

    called_args = mock_run.call_args[0][0]
    assert "vm" in called_args
    assert "start" in called_args
    assert "--name" in called_args
    assert "prod-web-01" in called_args
    assert "--resource-group" in called_args
    assert "prod-rg" in called_args
    assert "--no-wait" in called_args


# ---------------------------------------------------------------------------
# stop_vm
# ---------------------------------------------------------------------------


def test_stop_vm_command() -> None:
    """Passes correct args including --deallocate and --no-wait to az vm stop."""
    with patch("subprocess.run", return_value=_ok("{}")) as mock_run:
        stop_vm(name="prod-web-01", resource_group="prod-rg")

    called_args = mock_run.call_args[0][0]
    assert "vm" in called_args
    assert "stop" in called_args
    assert "--name" in called_args
    assert "prod-web-01" in called_args
    assert "--resource-group" in called_args
    assert "prod-rg" in called_args
    assert "--deallocate" in called_args
    assert "--no-wait" in called_args


# ---------------------------------------------------------------------------
# get_vm_ip
# ---------------------------------------------------------------------------


def test_get_vm_ip_found() -> None:
    """Returns the public IP string when the VM has one."""
    with patch("subprocess.run", return_value=_ok(IP_ADDRESSES_JSON)):
        ip = get_vm_ip(name="prod-web-01", resource_group="prod-rg")
    assert ip == "52.1.2.3"


def test_get_vm_ip_none() -> None:
    """Returns None when the VM has no public IP addresses."""
    with patch("subprocess.run", return_value=_ok(IP_ADDRESSES_EMPTY_JSON)):
        ip = get_vm_ip(name="dev-gpu-01", resource_group="dev-rg")
    assert ip is None


def test_get_vm_ip_empty_response() -> None:
    """Returns None when az returns an empty list."""
    with patch("subprocess.run", return_value=_ok("[]")):
        ip = get_vm_ip(name="dev-gpu-01", resource_group="dev-rg")
    assert ip is None


# ---------------------------------------------------------------------------
# AzError on failures
# ---------------------------------------------------------------------------


def test_az_error_on_failure() -> None:
    """AzError is raised with stderr content when az exits non-zero."""
    with (
        patch("subprocess.run", return_value=_fail("ResourceNotFound: VM not found", code=2)),
        pytest.raises(AzError) as exc_info,
    ):
        list_vms()

    err = exc_info.value
    assert err.exit_code == 2
    assert "ResourceNotFound" in err.stderr
    assert "Azure CLI error" in str(err)


def test_az_error_attributes() -> None:
    """AzError stores command, stderr, exit_code, and display_message attributes."""
    err = AzError(command="az vm list --output json", stderr="some error", exit_code=3)
    assert err.command == "az vm list --output json"
    assert err.stderr == "some error"
    assert err.exit_code == 3
    assert "exit 3" in err.display_message
    assert "some error" not in str(err)  # stderr must not leak into display


# ---------------------------------------------------------------------------
# VMInfo immutability
# ---------------------------------------------------------------------------


def test_vminfo_is_frozen() -> None:
    """VMInfo instances are immutable (frozen dataclass)."""
    vm = VMInfo(
        name="test-vm",
        resource_group="rg",
        power_state="running",
        location="eastus",
        vm_size="Standard_B2s",
        os_type="Linux",
    )
    with pytest.raises(Exception):  # noqa: B017
        vm.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# --output json always appended
# ---------------------------------------------------------------------------


def test_output_json_always_appended() -> None:
    """Every az invocation appends --output json as the last two args."""
    with patch("subprocess.run", return_value=_ok(LIST_VMS_JSON)) as mock_run:
        list_vms()

    called_args = mock_run.call_args[0][0]
    assert called_args[-2] == "--output"
    assert called_args[-1] == "json"


# ---------------------------------------------------------------------------
# validate_shutdown_time
# ---------------------------------------------------------------------------


class TestValidateShutdownTime:
    def test_valid_times(self) -> None:
        """Valid HHMM times do not raise."""
        for t in ("0000", "0900", "1200", "1900", "2359"):
            validate_shutdown_time(t)

    def test_invalid_hour(self) -> None:
        """Hour >= 24 raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("2400")

    def test_invalid_minute(self) -> None:
        """Minute >= 60 raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("1960")

    def test_too_short(self) -> None:
        """Three-digit string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("900")

    def test_too_long(self) -> None:
        """Five-digit string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("19000")

    def test_non_numeric(self) -> None:
        """Non-numeric string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("abcd")

    def test_empty(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            validate_shutdown_time("")


# ---------------------------------------------------------------------------
# get_auto_shutdown
# ---------------------------------------------------------------------------

AUTO_SHUTDOWN_ENABLED_JSON = json.dumps({
    "properties": {
        "status": "Enabled",
        "dailyRecurrence": {"time": "1900"},
        "timeZoneId": "UTC",
        "notificationSettings": {"emailRecipient": "admin@example.com", "status": "Enabled"},
    },
})

AUTO_SHUTDOWN_DISABLED_JSON = json.dumps({
    "properties": {
        "status": "Disabled",
        "dailyRecurrence": {"time": "2200"},
        "timeZoneId": "US/Eastern",
        "notificationSettings": {},
    },
})


class TestGetAutoShutdown:
    def test_returns_enabled_schedule(self) -> None:
        """Parses an enabled auto-shutdown schedule correctly."""
        with patch("subprocess.run", return_value=_ok(AUTO_SHUTDOWN_ENABLED_JSON)):
            info = get_auto_shutdown(name="prod-web-01", resource_group="prod-rg")

        assert info is not None
        assert info.enabled is True
        assert info.time == "1900"
        assert info.timezone == "UTC"
        assert info.email == "admin@example.com"

    def test_returns_disabled_schedule(self) -> None:
        """Parses a disabled auto-shutdown schedule correctly."""
        with patch("subprocess.run", return_value=_ok(AUTO_SHUTDOWN_DISABLED_JSON)):
            info = get_auto_shutdown(name="prod-web-01", resource_group="prod-rg")

        assert info is not None
        assert info.enabled is False
        assert info.time == "2200"
        assert info.timezone == "US/Eastern"
        assert info.email is None

    def test_returns_none_when_not_configured(self) -> None:
        """Returns None when the schedule resource does not exist."""
        with patch("subprocess.run", return_value=_fail("ResourceNotFound: The schedule was not found")):
            info = get_auto_shutdown(name="dev-vm", resource_group="dev-rg")

        assert info is None

    def test_returns_none_for_could_not_be_found(self) -> None:
        """Returns None for 'could not be found' error variant."""
        with patch("subprocess.run", return_value=_fail("The resource could not be found")):
            info = get_auto_shutdown(name="dev-vm", resource_group="dev-rg")

        assert info is None

    def test_returns_none_on_any_error(self) -> None:
        """Returns None for any CLI error (query is informational)."""
        with patch("subprocess.run", return_value=_fail("AuthenticationError: token expired")):
            info = get_auto_shutdown(name="vm-01", resource_group="rg")

        assert info is None

    def test_passes_correct_args(self) -> None:
        """Queries the DevTest Labs schedule resource by name."""
        with patch("subprocess.run", return_value=_ok(AUTO_SHUTDOWN_ENABLED_JSON)) as mock_run:
            get_auto_shutdown(name="my-vm", resource_group="my-rg")

        called_args = mock_run.call_args[0][0]
        assert "resource" in called_args
        assert "show" in called_args
        assert "--resource-group" in called_args
        assert "my-rg" in called_args
        assert "--resource-type" in called_args
        assert "Microsoft.DevTestLab/schedules" in called_args
        assert "--name" in called_args
        assert "shutdown-computevm-my-vm" in called_args


# ---------------------------------------------------------------------------
# enable_auto_shutdown
# ---------------------------------------------------------------------------


class TestEnableAutoShutdown:
    def test_creates_schedule_resource(self) -> None:
        """Creates a DevTest Labs schedule resource with correct properties."""
        responses = [
            _ok(ACCOUNT_JSON),  # _get_subscription_id
            _ok("{}"),          # az resource create
        ]
        with patch("subprocess.run", side_effect=responses) as mock_run:
            enable_auto_shutdown(name="vm-01", resource_group="rg", time="1900", timezone="UTC")

        create_args = mock_run.call_args_list[1][0][0]
        assert "resource" in create_args
        assert "create" in create_args
        assert "--resource-type" in create_args
        assert "Microsoft.DevTestLab/schedules" in create_args
        assert "--name" in create_args
        assert "shutdown-computevm-vm-01" in create_args
        assert "--properties" in create_args

        props_idx = create_args.index("--properties")
        props = json.loads(create_args[props_idx + 1])
        assert props["status"] == "Enabled"
        assert props["dailyRecurrence"]["time"] == "1900"
        assert props["timeZoneId"] == "UTC"
        assert props["notificationSettings"]["status"] == "Disabled"

    def test_includes_email_in_properties(self) -> None:
        """Includes email notification in schedule properties."""
        responses = [_ok(ACCOUNT_JSON), _ok("{}")]
        with patch("subprocess.run", side_effect=responses) as mock_run:
            enable_auto_shutdown(
                name="vm-01", resource_group="rg", time="2200",
                timezone="W. Europe Standard Time", email="admin@example.com",
            )

        create_args = mock_run.call_args_list[1][0][0]
        props_idx = create_args.index("--properties")
        props = json.loads(create_args[props_idx + 1])
        assert props["timeZoneId"] == "W. Europe Standard Time"
        assert props["notificationSettings"]["status"] == "Enabled"
        assert props["notificationSettings"]["emailRecipient"] == "admin@example.com"

    def test_rejects_invalid_time(self) -> None:
        """Raises ValueError before calling az when time is invalid."""
        with pytest.raises(ValueError, match="Invalid shutdown time"):
            enable_auto_shutdown(name="vm-01", resource_group="rg", time="2500")

    def test_raises_az_error_on_failure(self) -> None:
        """Raises AzError when the CLI call fails."""
        responses = [_ok(ACCOUNT_JSON), _fail("Some Azure error")]
        with (
            patch("subprocess.run", side_effect=responses),
            pytest.raises(AzError),
        ):
            enable_auto_shutdown(name="vm-01", resource_group="rg", time="1900")


# ---------------------------------------------------------------------------
# disable_auto_shutdown
# ---------------------------------------------------------------------------


class TestDisableAutoShutdown:
    def test_uses_resource_update_with_correct_args(self) -> None:
        """Calls az resource update with resource-type and name."""
        with patch("subprocess.run", return_value=_ok("{}")) as mock_run:
            disable_auto_shutdown(name="prod-web-01", resource_group="prod-rg")

        called_args = mock_run.call_args[0][0]
        assert "resource" in called_args
        assert "update" in called_args
        assert "--resource-group" in called_args
        assert "prod-rg" in called_args
        assert "--resource-type" in called_args
        assert "Microsoft.DevTestLab/schedules" in called_args
        assert "--name" in called_args
        assert "shutdown-computevm-prod-web-01" in called_args
        assert "properties.status=Disabled" in called_args

    def test_raises_on_resource_update_failure(self) -> None:
        """Raises AzError if the resource update call fails."""
        with (
            patch("subprocess.run", return_value=_fail("ResourceNotFound: schedule not found")),
            pytest.raises(AzError),
        ):
            disable_auto_shutdown(name="vm-01", resource_group="rg")


# ---------------------------------------------------------------------------
# AutoShutdownInfo immutability
# ---------------------------------------------------------------------------


def test_auto_shutdown_info_is_frozen() -> None:
    """AutoShutdownInfo instances are immutable."""
    info = AutoShutdownInfo(enabled=True, time="1900", timezone="UTC")
    with pytest.raises(Exception):  # noqa: B017
        info.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _get_subscription_id caching
# ---------------------------------------------------------------------------


class TestSubscriptionIdCache:
    def test_caches_after_first_call(self) -> None:
        """Second call returns cached value without hitting az CLI."""
        with patch("subprocess.run", return_value=_ok(ACCOUNT_JSON)) as mock_run:
            first = _get_subscription_id()
            second = _get_subscription_id()

        assert first == "abc-123"
        assert second == "abc-123"
        assert mock_run.call_count == 1

    def test_enable_auto_shutdown_reuses_cached_subscription(self) -> None:
        """Two enable calls produce only one az account show call."""
        responses = [
            _ok(ACCOUNT_JSON),  # first enable: account show
            _ok("{}"),          # first enable: resource create
            _ok("{}"),          # second enable: resource create (no account show)
        ]
        with patch("subprocess.run", side_effect=responses) as mock_run:
            enable_auto_shutdown(name="vm-01", resource_group="rg", time="1900")
            enable_auto_shutdown(name="vm-02", resource_group="rg", time="2200")

        assert mock_run.call_count == 3
