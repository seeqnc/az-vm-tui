"""Thin wrapper around the Azure CLI for VM operations."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field

_AZ_TIMEOUT_SECONDS = 60


class AzError(Exception):
    """Raised when an az CLI command fails.

    Attributes:
        command: The full CLI command string.
        stderr: Raw stderr output (may contain sensitive Azure context — log only).
        exit_code: Process exit code.
        display_message: Sanitized message safe for display in the UI.
    """

    def __init__(self, command: str, stderr: str, exit_code: int) -> None:
        self.command = command
        self.stderr = stderr
        self.exit_code = exit_code
        self.display_message = f"Azure CLI error (exit {exit_code})"
        super().__init__(self.display_message)


@dataclass(frozen=True)
class VMInfo:
    """Represents an Azure virtual machine."""

    name: str
    resource_group: str
    power_state: str
    location: str
    vm_size: str
    os_type: str
    tags: dict[str, str] = field(default_factory=dict)


def _run_az(args: list[str]) -> str:
    """Run an az CLI command and return stdout as a string.

    Appends ``--output json`` to every invocation so callers always receive
    structured data.

    Args:
        args: Arguments to pass after ``az``, without ``--output json``.

    Returns:
        The captured stdout string from the command.

    Raises:
        AzError: If the process exits with a non-zero status.
    """
    cmd = ["az", *args, "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_AZ_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise AzError(
            command=" ".join(cmd),
            stderr=f"Command timed out after {_AZ_TIMEOUT_SECONDS}s",
            exit_code=-1,
        ) from exc
    if result.returncode != 0:
        raise AzError(command=" ".join(cmd), stderr=result.stderr, exit_code=result.returncode)
    return result.stdout


def check_az_cli() -> None:
    """Verify that the az CLI is available on PATH.

    Raises:
        AzError: If az is not found, with an installation URL in the message.
    """
    if shutil.which("az") is None:
        raise AzError(
            command="az",
            stderr="az CLI not found — install via https://aka.ms/installazurecli",
            exit_code=1,
        )


def check_login() -> None:
    """Verify that the user is logged in to the Azure CLI.

    Raises:
        AzError: If ``az account show`` fails, with a hint to run ``az login``.
    """
    try:
        _run_az(["account", "show"])
    except AzError as exc:
        raise AzError(
            command=exc.command,
            stderr="Not logged in to Azure CLI. Run: az login",
            exit_code=exc.exit_code,
        ) from exc


def get_subscription() -> str:
    """Return the name of the currently active Azure subscription.

    Returns:
        The subscription display name.

    Raises:
        AzError: If the CLI call fails.
    """
    raw = _run_az(["account", "show"])
    data: dict = json.loads(raw)
    return str(data["name"])


def _normalize_power_state(raw: str) -> str:
    """Strip the leading 'VM ' prefix from an Azure power state string.

    Args:
        raw: Raw powerState value such as ``"VM running"``.

    Returns:
        Normalised state such as ``"running"``.
    """
    return raw.removeprefix("VM ").strip().lower()


def _parse_vm(item: dict) -> VMInfo:
    """Build a VMInfo from a single entry in ``az vm list`` JSON output.

    Args:
        item: Dict representing one VM from the az CLI response.

    Returns:
        A populated VMInfo dataclass.
    """
    return VMInfo(
        name=item["name"],
        resource_group=item["resourceGroup"],
        power_state=_normalize_power_state(item.get("powerState", "")),
        location=item["location"],
        vm_size=item["hardwareProfile"]["vmSize"],
        os_type=item["storageProfile"]["osDisk"]["osType"],
        tags=item.get("tags") or {},
    )


def list_vms(resource_group: str = "", subscription_id: str = "") -> list[VMInfo]:
    """List Azure VMs, optionally filtered by resource group or subscription.

    Args:
        resource_group: If non-empty, restrict results to this resource group.
        subscription_id: If non-empty, target this subscription.

    Returns:
        List of VMInfo objects, one per VM returned by the CLI.

    Raises:
        AzError: If the CLI call fails.
    """
    args = ["vm", "list", "--show-details"]
    if resource_group:
        args += ["--resource-group", resource_group]
    if subscription_id:
        args += ["--subscription", subscription_id]

    raw = _run_az(args)
    items: list[dict] = json.loads(raw)
    return [_parse_vm(item) for item in items]


def start_vm(name: str, resource_group: str) -> None:
    """Start an Azure VM asynchronously.

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.

    Raises:
        AzError: If the CLI call fails.
    """
    _run_az(["vm", "start", "--name", name, "--resource-group", resource_group, "--no-wait"])


def stop_vm(name: str, resource_group: str) -> None:
    """Stop and deallocate an Azure VM asynchronously.

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.

    Raises:
        AzError: If the CLI call fails.
    """
    _run_az(["vm", "stop", "--name", name, "--resource-group", resource_group, "--deallocate", "--no-wait"])


def get_vm_ip(name: str, resource_group: str) -> str | None:
    """Return the public IP address of an Azure VM, or None if not present.

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.

    Returns:
        Public IP address string, or None if the VM has no public IP.

    Raises:
        AzError: If the CLI call fails.
    """
    raw = _run_az(["vm", "list-ip-addresses", "--name", name, "--resource-group", resource_group])
    entries: list[dict] = json.loads(raw)

    for entry in entries:
        interfaces = entry.get("virtualMachine", {}).get("network", {}).get("publicIpAddresses", [])
        for iface in interfaces:
            ip = iface.get("ipAddress")
            if ip:
                return str(ip)

    return None
