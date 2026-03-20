"""VM list screen with DataTable and keyboard navigation."""

from __future__ import annotations

import logging
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
}
DEFAULT_STATE_INDICATOR = "⟳"


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
        table.add_columns("STATE", "NAME", "LOCATION", "SIZE", "RESOURCE GROUP")
        self._load_subscription()
        self._load_vms()

    @work(thread=True)
    def _load_subscription(self) -> None:
        """Load subscription name for the header (background thread)."""
        try:
            name = az.get_subscription()
            self.app.call_from_thread(setattr, self.app, "sub_title", name)
        except AzError:
            pass

    def _load_vms(self) -> None:
        """Trigger VM list loading with a loading indicator."""
        table = self.query_one("#vm-table", DataTable)
        table.loading = True
        self._fetch_vms()

    @work(thread=True)
    def _fetch_vms(self) -> None:
        """Fetch VM list from az CLI in a background thread."""
        try:
            vms = az.list_vms(
                resource_group=self.config.azure.resource_group,
                subscription_id=self.config.azure.subscription_id,
            )
            self._vms = vms
            self.app.call_from_thread(self._populate_table)
        except AzError as exc:
            logger.error("Failed to list VMs: %s", exc)
            self.app.call_from_thread(self._show_error, f"Failed to list VMs: {exc}")
        finally:
            self.app.call_from_thread(self._set_loading, False)

    def _set_loading(self, value: bool) -> None:
        """Set the loading state on the table (main thread).

        Args:
            value: Whether to show the loading indicator.
        """
        table = self.query_one("#vm-table", DataTable)
        table.loading = value

    def _populate_table(self) -> None:
        """Fill the DataTable with VM data (must run on main thread)."""
        table = self.query_one("#vm-table", DataTable)
        table.clear()
        for vm in self._vms:
            indicator = STATE_INDICATORS.get(vm.power_state, DEFAULT_STATE_INDICATOR)
            state_display = f"{indicator} {vm.power_state}"
            table.add_row(state_display, vm.name, vm.location, vm.vm_size, vm.resource_group, key=vm.name)
        self._hide_error()

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
        self._do_start_vm(vm)

    @work(thread=True)
    def _do_start_vm(self, vm: VMInfo) -> None:
        """Start a VM in a background thread.

        Args:
            vm: The VM to start.
        """
        try:
            az.start_vm(vm.name, vm.resource_group)
            self.app.call_from_thread(self.notify, f"Starting {vm.name}...")
            self.app.call_from_thread(self._load_vms)
        except AzError as exc:
            logger.error("Failed to start VM %s: %s", vm.name, exc)
            self.app.call_from_thread(self._show_error, f"Failed to start {vm.name}: {exc}")

    def action_stop_vm(self) -> None:
        """Stop the selected VM after confirmation."""
        vm = self._get_selected_vm()
        if vm is None:
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
        try:
            az.stop_vm(vm.name, vm.resource_group)
            self.app.call_from_thread(self.notify, f"Stopping {vm.name}...")
            self.app.call_from_thread(self._load_vms)
        except AzError as exc:
            logger.error("Failed to stop VM %s: %s", vm.name, exc)
            self.app.call_from_thread(self._show_error, f"Failed to stop {vm.name}: {exc}")

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
            logger.error("Failed to get IP for %s: %s", vm.name, exc)
            self.app.call_from_thread(self._show_error, f"Failed to get IP for {vm.name}: {exc}")

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
