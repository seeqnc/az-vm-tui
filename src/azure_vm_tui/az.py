"""Thin wrapper around the Azure CLI for VM operations."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field

from azure_vm_tui.validators import validate_shutdown_time

logger = logging.getLogger("azure_vm_tui")

_AZ_TIMEOUT_SECONDS = 60
_SCHEDULE_RESOURCE_TYPE = "Microsoft.DevTestLab/schedules"


def _schedule_name(vm_name: str) -> str:
    return f"shutdown-computevm-{vm_name}"


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


@dataclass(frozen=True)
class AutoShutdownInfo:
    """Represents the auto-shutdown schedule for a VM."""

    enabled: bool
    time: str
    timezone: str
    email: str | None = None


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


def list_vms(
    resource_group: str = "",
    subscription_id: str = "",
    show_details: bool = True,
) -> list[VMInfo]:
    """List Azure VMs, optionally filtered by resource group or subscription.

    Args:
        resource_group: If non-empty, restrict results to this resource group.
        subscription_id: If non-empty, target this subscription.
        show_details: If True (default), include power state via ``--show-details``.
            Set to False for a faster initial load without power state info.

    Returns:
        List of VMInfo objects, one per VM returned by the CLI.

    Raises:
        AzError: If the CLI call fails.
    """
    args = ["vm", "list"]
    if show_details:
        args.append("--show-details")
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


def get_auto_shutdown(name: str, resource_group: str) -> AutoShutdownInfo | None:
    """Return the auto-shutdown schedule for a VM, or None if not configured.

    Queries the DevTest Labs schedule resource directly, which is the
    underlying resource that ``az vm auto-shutdown`` manages.

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.

    Returns:
        AutoShutdownInfo if a schedule exists, None if no schedule is found.
    """
    schedule_name = _schedule_name(name)
    try:
        raw = _run_az([
            "resource", "show",
            "--resource-group", resource_group,
            "--resource-type", _SCHEDULE_RESOURCE_TYPE,
            "--name", schedule_name,
        ])
    except AzError as exc:
        logger.debug("Auto-shutdown query failed for %s: %s", name, exc.stderr)
        return None
    data: dict = json.loads(raw)
    props: dict = data.get("properties", {})
    status = props.get("status", "Disabled")
    recurrence = props.get("dailyRecurrence", {})
    notification = props.get("notificationSettings", {})
    email = notification.get("emailRecipient") or None
    return AutoShutdownInfo(
        enabled=status == "Enabled",
        time=recurrence.get("time", ""),
        timezone=props.get("timeZoneId", ""),
        email=email,
    )


def enable_auto_shutdown(
    name: str,
    resource_group: str,
    time: str,
    timezone: str = "UTC",
    email: str | None = None,
) -> None:
    """Enable or update auto-shutdown for a VM.

    Uses the DevTest Labs schedule resource directly so that timezone
    is supported (``az vm auto-shutdown`` only accepts UTC).

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.
        time: Daily shutdown time in HHMM format (e.g. "1900").
        timezone: Windows timezone name (e.g. "UTC", "W. Europe Standard Time").
        email: Optional notification email address.

    Raises:
        ValueError: If the time format is invalid.
        AzError: If the CLI call fails.
    """
    validate_shutdown_time(time)
    sub_id = _get_subscription_id()
    vm_id = (
        f"/subscriptions/{sub_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{name}"
    )
    schedule_name = _schedule_name(name)
    notification_settings: dict = {"status": "Disabled"}
    if email:
        notification_settings = {
            "status": "Enabled",
            "emailRecipient": email,
            "notificationLocale": "en",
            "timeInMinutes": 30,
        }
    properties = json.dumps({
        "status": "Enabled",
        "taskType": "ComputeVmShutdownTask",
        "dailyRecurrence": {"time": time},
        "timeZoneId": timezone,
        "targetResourceId": vm_id,
        "notificationSettings": notification_settings,
    })
    _run_az([
        "resource", "create",
        "--resource-group", resource_group,
        "--resource-type", _SCHEDULE_RESOURCE_TYPE,
        "--name", schedule_name,
        "--properties", properties,
    ])


_subscription_id_cache: str | None = None


def _get_subscription_id() -> str:
    """Return the ID of the currently active Azure subscription.

    Result is cached for the lifetime of the process since the active
    subscription does not change during a session.

    Returns:
        The subscription UUID string.

    Raises:
        AzError: If the CLI call fails.
    """
    global _subscription_id_cache
    if _subscription_id_cache is not None:
        return _subscription_id_cache
    raw = _run_az(["account", "show"])
    data: dict = json.loads(raw)
    _subscription_id_cache = str(data["id"])
    return _subscription_id_cache


def disable_auto_shutdown(name: str, resource_group: str) -> None:
    """Disable auto-shutdown for a VM by setting the schedule status to Disabled.

    Args:
        name: VM name.
        resource_group: Resource group containing the VM.

    Raises:
        AzError: If the CLI call fails.
    """
    _run_az([
        "resource", "update",
        "--resource-group", resource_group,
        "--resource-type", _SCHEDULE_RESOURCE_TYPE,
        "--name", _schedule_name(name),
        "--set", "properties.status=Disabled",
    ])
