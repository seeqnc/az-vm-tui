"""VM list screen with DataTable and keyboard navigation."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from azure_vm_tui import az
from azure_vm_tui.az import AzError, VMInfo
from azure_vm_tui.config import AppConfig
from azure_vm_tui.fzf import fzf_select
from azure_vm_tui.screens.vm_detail import VMDetailScreen

logger = logging.getLogger("azure_vm_tui")

STATE_INDICATORS = {
    "running": "●",
    "deallocated": "○",
    "stopped": "◌",
    "starting": "⟳",
    "stopping": "⟳",
    "deallocating": "⟳",
}
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

_POLL_INTERVAL = 5
_POLL_MAX_ATTEMPTS = 24


class ConfirmScreen(Screen[bool]):
    """Simple yes/no confirmation dialog.

    Args:
        message: The question to display to the user.
    """

    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        """Render the confirmation message and hint."""
        yield Static(self._message, id="confirm-message")
        yield Static("[y] Yes  [n] No", id="confirm-hint")

    def action_confirm(self) -> None:
        """Dismiss with True when user presses y."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Dismiss with False when user presses n or Escape."""
        self.dismiss(False)


class VMListScreen(Screen):
    """Main screen displaying a list of Azure VMs.

    Args:
        config: Application configuration.
    """

    BINDINGS: ClassVar = [
        Binding("s", "start_vm", "Start", show=True),
        Binding("x", "stop_vm", "Stop", show=True),
        Binding("i", "show_info", "Info", show=True),
        Binding("c", "connect_ssh", "SSH", show=True),
        Binding("slash", "search", "Search", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self._vms: list[VMInfo] = []
        self._spinner_tick = 0
        self._spinner_timer = None

    def compose(self) -> ComposeResult:
        """Build the screen layout."""
        yield Header()
        table = DataTable(id="vm-table")
        table.cursor_type = "row"
        table.zebra_stripes = True
        yield table
        yield Static("", id="error-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the table columns and load VMs."""
        table = self.query_one("#vm-table", DataTable)
        state_col, *_ = table.add_columns("STATE", "NAME", "LOCATION", "SIZE", "RESOURCE GROUP")
        self._state_col_key = state_col
        self._load_subscription()
        self._load_vms_two_phase()

    @work(thread=True)
    def _load_subscription(self) -> None:
        """Load subscription name for the header (background thread)."""
        try:
            name = az.get_subscription()
            self.app.call_from_thread(setattr, self.app, "sub_title", name)
        except AzError:
            pass

    def _load_vms_two_phase(self) -> None:
        """Fast initial load without power state, then backfill details."""
        table = self.query_one("#vm-table", DataTable)
        table.loading = True
        self._fetch_vms_fast()

    @work(thread=True)
    def _fetch_vms_fast(self) -> None:
        """Phase 1: Fetch VM list without --show-details (fast)."""
        try:
            vms = az.list_vms(
                resource_group=self.config.azure.resource_group,
                subscription_id=self.config.azure.subscription_id,
                show_details=False,
            )
            self.app.call_from_thread(self._populate_table, vms)
        except AzError as exc:
            logger.error("Failed to list VMs: %s", exc.stderr)
            self.app.call_from_thread(self._show_error, f"Failed to list VMs: {exc.display_message}")
        finally:
            self.app.call_from_thread(self._set_loading, False)
        self._fetch_vms_details()

    @work(thread=True)
    def _fetch_vms_details(self) -> None:
        """Phase 2: Fetch full VM details with power state (slow)."""
        try:
            vms = az.list_vms(
                resource_group=self.config.azure.resource_group,
                subscription_id=self.config.azure.subscription_id,
                show_details=True,
            )
            self.app.call_from_thread(self._populate_table, vms)
        except AzError as exc:
            logger.error("Failed to fetch VM details: %s", exc.stderr)

    def _load_vms(self) -> None:
        """Full refresh with details (used by manual refresh and after operations)."""
        table = self.query_one("#vm-table", DataTable)
        table.loading = True
        self._fetch_vms_full()

    @work(thread=True)
    def _fetch_vms_full(self) -> None:
        """Fetch VM list with full details in one pass."""
        try:
            vms = az.list_vms(
                resource_group=self.config.azure.resource_group,
                subscription_id=self.config.azure.subscription_id,
                show_details=True,
            )
            self.app.call_from_thread(self._populate_table, vms)
        except AzError as exc:
            logger.error("Failed to list VMs: %s", exc.stderr)
            self.app.call_from_thread(self._show_error, f"Failed to list VMs: {exc.display_message}")
        finally:
            self.app.call_from_thread(self._set_loading, False)

    def _set_loading(self, value: bool) -> None:
        """Set the loading state on the table (main thread).

        Args:
            value: Whether to show the loading indicator.
        """
        table = self.query_one("#vm-table", DataTable)
        table.loading = value

    def _populate_table(self, vms: list[VMInfo]) -> None:
        """Fill the DataTable with VM data (must run on main thread).

        Args:
            vms: List of VMs to display.
        """
        table = self.query_one("#vm-table", DataTable)
        cursor_name = self._get_cursor_vm_name()
        self._vms = vms
        table.clear()
        restore_row = 0
        has_pending = False
        for idx, vm in enumerate(self._vms):
            if vm.power_state:
                indicator = STATE_INDICATORS.get(vm.power_state, "?")
                state_display = f"{indicator} {vm.power_state}"
            else:
                has_pending = True
                frame = _SPINNER_FRAMES[self._spinner_tick % len(_SPINNER_FRAMES)]
                state_display = f"{frame} loading"
            table.add_row(state_display, vm.name, vm.location, vm.vm_size, vm.resource_group, key=vm.name)
            if vm.name == cursor_name:
                restore_row = idx
        if restore_row and table.row_count > restore_row:
            table.move_cursor(row=restore_row)
        self._hide_error()
        self._toggle_spinner(has_pending)

    def _toggle_spinner(self, active: bool) -> None:
        """Start or stop the spinner timer for loading state cells.

        Args:
            active: Whether any cells need the spinner animation.
        """
        if active and self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.15, self._advance_spinner)
        elif not active and self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _advance_spinner(self) -> None:
        """Tick the spinner and update state cells that are still loading."""
        self._spinner_tick += 1
        table = self.query_one("#vm-table", DataTable)
        frame = _SPINNER_FRAMES[self._spinner_tick % len(_SPINNER_FRAMES)]
        for vm in self._vms:
            if not vm.power_state:
                with contextlib.suppress(Exception):
                    table.update_cell(vm.name, self._state_col_key, f"{frame} loading")

    def _get_cursor_vm_name(self) -> str | None:
        """Return the name of the currently highlighted VM, or None."""
        table = self.query_one("#vm-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            return str(row_key.value)
        except Exception:
            return None

    def _show_error(self, message: str) -> None:
        """Display an error message in the status bar.

        Args:
            message: Human-readable error text to display.
        """
        error_bar = self.query_one("#error-bar", Static)
        error_bar.update(message)
        error_bar.styles.display = "block"

    def _hide_error(self) -> None:
        """Hide the error bar."""
        error_bar = self.query_one("#error-bar", Static)
        error_bar.styles.display = "none"

    def _get_selected_vm(self) -> VMInfo | None:
        """Get the currently selected VM from the table.

        Returns:
            The VMInfo for the highlighted row, or None if the table is empty.
        """
        table = self.query_one("#vm-table", DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        name = str(row_key.value)
        for vm in self._vms:
            if vm.name == name:
                return vm
        return None

    def action_start_vm(self) -> None:
        """Start the selected VM."""
        vm = self._get_selected_vm()
        if vm is None:
            return
        if vm.power_state in ("running", "starting"):
            self.notify(f"{vm.name} is already {vm.power_state}", severity="warning")
            return
        self._do_start_vm(vm)

    @work(thread=True)
    def _do_start_vm(self, vm: VMInfo) -> None:
        """Start a VM in a background thread.

        Args:
            vm: The VM to start.
        """
        self.app.call_from_thread(self.notify, f"Starting {vm.name}...")
        try:
            az.start_vm(vm.name, vm.resource_group)
            self._poll_until_state(vm.name, vm.resource_group, "running")
        except AzError as exc:
            logger.error("Failed to start VM %s: %s", vm.name, exc.stderr)
            self.app.call_from_thread(self._show_error, f"Failed to start {vm.name}: {exc.display_message}")

    def action_stop_vm(self) -> None:
        """Stop the selected VM after confirmation."""
        vm = self._get_selected_vm()
        if vm is None:
            return
        if vm.power_state in ("deallocated", "stopped", "stopping", "deallocating"):
            self.notify(f"{vm.name} is already {vm.power_state}", severity="warning")
            return
        self._confirm_stop(vm)

    def _confirm_stop(self, vm: VMInfo) -> None:
        """Show confirmation dialog and stop VM if confirmed.

        Args:
            vm: The VM to stop.
        """
        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._do_stop_vm(vm)

        self.app.push_screen(ConfirmScreen(f"Stop and deallocate {vm.name}?"), callback=on_confirm)

    @work(thread=True)
    def _do_stop_vm(self, vm: VMInfo) -> None:
        """Stop a VM in a background thread.

        Args:
            vm: The VM to stop.
        """
        self.app.call_from_thread(self.notify, f"Stopping {vm.name}...")
        try:
            az.stop_vm(vm.name, vm.resource_group)
            self._poll_until_state(vm.name, vm.resource_group, "deallocated")
        except AzError as exc:
            logger.error("Failed to stop VM %s: %s", vm.name, exc.stderr)
            self.app.call_from_thread(self._show_error, f"Failed to stop {vm.name}: {exc.display_message}")

    def _poll_until_state(self, name: str, resource_group: str, target_state: str) -> None:
        """Poll VM state until it reaches the target, refreshing the table each cycle.

        Runs on a background thread. Stops after reaching the target state
        or after ``_POLL_MAX_ATTEMPTS`` cycles.

        Args:
            name: VM name to watch.
            resource_group: Resource group of the VM.
            target_state: Power state to wait for (e.g. "running", "deallocated").
        """
        for _ in range(_POLL_MAX_ATTEMPTS):
            self.app.call_from_thread(self._load_vms)
            time.sleep(_POLL_INTERVAL)
            for vm in self._vms:
                if vm.name == name and vm.power_state == target_state:
                    return

    def action_show_info(self) -> None:
        """Open detail screen for the selected VM."""
        vm = self._get_selected_vm()
        if vm is None:
            return
        self.app.push_screen(VMDetailScreen(vm, self.config))

    @work(thread=True)
    def action_search(self) -> None:
        """Launch fzf search for VMs (background thread)."""
        if not self._vms:
            return
        names = [vm.name for vm in self._vms]
        selected = fzf_select(names)
        if selected:
            table = self.query_one("#vm-table", DataTable)
            for idx, vm in enumerate(self._vms):
                if vm.name == selected:
                    self.app.call_from_thread(table.move_cursor, row=idx)
                    break

    def action_connect_ssh(self) -> None:
        """Resolve IP and exit the app to hand off to ssh."""
        vm = self._get_selected_vm()
        if vm is None:
            return
        self._do_connect_ssh(vm)

    @work(thread=True)
    def _do_connect_ssh(self, vm: VMInfo) -> None:
        """Resolve VM IP and exit with SSH connection info.

        Args:
            vm: The VM to connect to.
        """
        try:
            ip = az.get_vm_ip(vm.name, vm.resource_group)
            if ip is None:
                self.app.call_from_thread(self._show_error, f"No public IP for {vm.name}")
                return
            ssh_user = self.config.ssh.user
            ssh_key = self.config.ssh.key
            self.app.call_from_thread(self.app.exit, {"host": ip, "user": ssh_user, "key": ssh_key})
        except AzError as exc:
            logger.error("Failed to get IP for %s: %s", vm.name, exc.stderr)
            self.app.call_from_thread(self._show_error, f"Failed to get IP for {vm.name}: {exc.display_message}")

    def action_refresh(self) -> None:
        """Force refresh the VM list."""
        self._load_vms()

    def action_cursor_down(self) -> None:
        """Move table cursor down (j key)."""
        table = self.query_one("#vm-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move table cursor up (k key)."""
        table = self.query_one("#vm-table", DataTable)
        table.action_cursor_up()
