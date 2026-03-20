"""Tests for the az CLI wrapper module."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from azure_vm_tui.az import (
    AzError,
    VMInfo,
    check_az_cli,
    check_login,
    get_subscription,
    get_vm_ip,
    list_vms,
    start_vm,
    stop_vm,
)

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
    assert "https://aka.ms/installazurecli" in str(exc_info.value)


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
    assert "az login" in str(exc_info.value)


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
    assert "ResourceNotFound" in str(err)


def test_az_error_attributes() -> None:
    """AzError stores command, stderr, and exit_code attributes."""
    err = AzError(command="az vm list --output json", stderr="some error", exit_code=3)
    assert err.command == "az vm list --output json"
    assert err.stderr == "some error"
    assert err.exit_code == 3
    assert "exit 3" in str(err)


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
