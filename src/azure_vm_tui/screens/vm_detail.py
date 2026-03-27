"""VM detail screen with static info and optional live stats."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from azure_vm_tui import az
from azure_vm_tui.az import AutoShutdownInfo, AzError, VMInfo
from azure_vm_tui.config import AppConfig, validate_ssh_key_path
from azure_vm_tui.screens.auto_shutdown_dialog import AutoShutdownDialog
from azure_vm_tui.stats import StatsError, VMStats, collect_stats

logger = logging.getLogger("azure_vm_tui")


class VMDetailScreen(Screen):
    """Detail screen showing VM info and optional live system stats.

    Args:
        vm: The VM whose details are displayed.
        config: Application configuration.
    """

    BINDINGS: ClassVar = [
        Binding("a", "auto_shutdown", "AutoOff", show=True),
        Binding("A", "disable_auto_shutdown", "DisableAutoOff", show=True),
        Binding("b", "go_back", "Back", show=True),
        Binding("escape", "go_back", "Back", show=False),
    ]

    def __init__(self, vm: VMInfo, config: AppConfig) -> None:
        super().__init__()
        self._vm = vm
        self._config = config
        self._stats_running = False
        self._auto_shutdown: AutoShutdownInfo | None = None
        self._cached_ip: str | None = None

    def compose(self) -> ComposeResult:
        """Build the detail screen layout."""
        yield Header()
        yield Vertical(
            Static(self._build_info_text(), id="vm-info"),
            Static("", id="auto-shutdown-info"),
            id="info-panel",
        )
        if self._config.ui.show_stats:
            yield Vertical(
                Static("System Stats", classes="stats-label"),
                Static("Resolving IP...", id="stats-content"),
                id="stats-panel",
                classes="stats-panel",
            )
        yield Footer()

    def _build_info_text(self, ip: str = "loading...") -> str:
        """Build the static VM info display text.

        Args:
            ip: Public IP address string to display.

        Returns:
            Markup string with the VM's static properties.
        """
        vm = self._vm
        tags_str = ", ".join(f"{k}={v}" for k, v in vm.tags.items()) if vm.tags else "none"
        lines = [
            f"[b]Name:[/b]           {vm.name}",
            f"[b]Resource Group:[/b] {vm.resource_group}",
            f"[b]Location:[/b]       {vm.location}",
            f"[b]Size:[/b]           {vm.vm_size}",
            f"[b]OS:[/b]             {vm.os_type}",
            f"[b]State:[/b]          {vm.power_state}",
            f"[b]Tags:[/b]           {tags_str}",
            f"[b]Public IP:[/b]      {ip}",
        ]
        return "\n".join(lines)

    def on_mount(self) -> None:
        """Load IP, auto-shutdown info, and start stats after IP resolves."""
        self._load_ip()
        self._load_auto_shutdown()

    @work(thread=True)
    def _load_ip(self) -> None:
        """Load the public IP address, then start stats if enabled."""
        try:
            ip = az.get_vm_ip(self._vm.name, self._vm.resource_group)
            if ip is not None:
                self._cached_ip = ip
            ip_display = ip or "none"
        except AzError as exc:
            logger.error("Failed to get IP for %s: %s", self._vm.name, exc.stderr)
            ip_display = "error"
        self.app.call_from_thread(self._update_info, ip_display)
        if self._cached_ip is not None and self._config.ui.show_stats:
            self._stats_running = True
            self.app.call_from_thread(self._collect_stats_loop)

    def _update_info(self, ip: str) -> None:
        """Rebuild the info panel with the resolved IP (main thread).

        Args:
            ip: Public IP address string to display.
        """
        info = self.query_one("#vm-info", Static)
        info.update(self._build_info_text(ip=ip))

    @work(thread=True)
    def _collect_stats_loop(self) -> None:
        """Periodically collect and display stats (background thread)."""
        while self._stats_running:
            self._fetch_and_update_stats()
            time.sleep(self._config.ui.refresh_interval)

    def _fetch_and_update_stats(self) -> None:
        """Fetch stats once and push result to the UI.

        Uses the cached IP from ``_load_ip()`` to avoid a CLI call per cycle.
        """
        if self._cached_ip is None:
            self.app.call_from_thread(self._update_stats_error, "No public IP — cannot collect stats")
            return
        try:
            validate_ssh_key_path(self._config.ssh.key)
            key_path = str(Path(self._config.ssh.key).expanduser())
            vm_stats = collect_stats(
                host=self._cached_ip,
                user=self._config.ssh.user,
                key_path=key_path,
                timeout=self._config.ssh.timeout,
            )
            self.app.call_from_thread(self._update_stats_display, vm_stats)
        except ValueError as exc:
            self.app.call_from_thread(self._update_stats_error, str(exc))
        except StatsError as exc:
            self.app.call_from_thread(self._update_stats_error, exc.reason)

    def _update_stats_display(self, stats: VMStats) -> None:
        """Update the stats panel with fresh data (main thread).

        Args:
            stats: The freshly collected VM statistics.
        """
        content = self.query_one("#stats-content", Static)
        ram_pct = (stats.ram_used_mb / stats.ram_total_mb * 100) if stats.ram_total_mb > 0 else 0

        lines = [
            f"[b]CPU:[/b]  {stats.cpu_pct:.1f}%",
            f"[b]RAM:[/b]  {stats.ram_used_mb} / {stats.ram_total_mb} MB ({ram_pct:.0f}%)",
            "",
            "[b]Disks:[/b]",
        ]
        for disk in stats.disks:
            lines.append(f"  {disk.source}  {disk.avail} free / {disk.size}  ({disk.use_pct:.0f}% used)")

        if stats.gpus:
            lines.append("")
            lines.append("[b]GPUs:[/b]")
            for gpu in stats.gpus:
                lines.append(
                    f"  {gpu.name}  {gpu.utilization_pct:.0f}% util  "
                    f"{gpu.memory_used_mb}/{gpu.memory_total_mb} MB"
                )
        else:
            lines.append("")
            lines.append("[dim]No GPU detected[/dim]")

        lines.append("")
        lines.append(f"[dim]Last updated: {stats.collected_at.strftime('%H:%M:%S')}[/dim]")
        content.update("\n".join(lines))

    def _update_stats_error(self, message: str) -> None:
        """Show a stats error message in the panel (main thread).

        Args:
            message: Human-readable description of why stats failed.
        """
        content = self.query_one("#stats-content", Static)
        content.update(f"[red]{message}[/red]")

    @work(thread=True)
    def _load_auto_shutdown(self) -> None:
        """Load auto-shutdown info in a background thread."""
        try:
            info = az.get_auto_shutdown(self._vm.name, self._vm.resource_group)
            self.app.call_from_thread(self._update_auto_shutdown_display, info)
        except AzError as exc:
            logger.error("Failed to get auto-shutdown for %s: %s", self._vm.name, exc.stderr)
            self.app.call_from_thread(
                self._update_auto_shutdown_display_text,
                "[b]Auto-Shutdown:[/b] [red]error[/red]",
            )

    def _update_auto_shutdown_display(self, info: AutoShutdownInfo | None) -> None:
        """Update auto-shutdown display on the main thread.

        Args:
            info: Auto-shutdown info, or None if not configured.
        """
        self._auto_shutdown = info
        if info is None:
            text = "[b]Auto-Shutdown:[/b] [dim]not configured[/dim]"
        else:
            time_str = f"{info.time[:2]}:{info.time[2:]}" if len(info.time) == 4 else info.time
            if info.enabled:
                text = f"[b]Auto-Shutdown:[/b] [green]{time_str} {info.timezone}[/green]"
            else:
                text = f"[b]Auto-Shutdown:[/b] [dim]disabled (was {time_str} {info.timezone})[/dim]"
        self._update_auto_shutdown_display_text(text)

    def _update_auto_shutdown_display_text(self, text: str) -> None:
        """Set the auto-shutdown Static widget text.

        Args:
            text: Rich markup string to display.
        """
        widget = self.query_one("#auto-shutdown-info", Static)
        widget.update(text)

    def action_auto_shutdown(self) -> None:
        """Open the auto-shutdown configuration dialog."""
        cfg = self._config.auto_shutdown
        default_time = cfg.default_time
        default_tz = cfg.default_timezone
        if self._auto_shutdown is not None and self._auto_shutdown.time:
            default_time = self._auto_shutdown.time
            default_tz = self._auto_shutdown.timezone

        def on_result(result: tuple[str, str] | None = None) -> None:
            if result is not None:
                shutdown_time, timezone = result
                self._do_enable_auto_shutdown(shutdown_time, timezone)

        self.app.push_screen(AutoShutdownDialog(default_time, default_tz), callback=on_result)

    @work(thread=True)
    def _do_enable_auto_shutdown(self, shutdown_time: str, timezone: str) -> None:
        """Enable auto-shutdown in a background thread.

        Args:
            shutdown_time: HHMM time string.
            timezone: Windows timezone name (e.g. "UTC", "Eastern Standard Time").
        """
        self.app.call_from_thread(self.notify, f"Enabling auto-shutdown at {shutdown_time} {timezone}...")
        try:
            az.enable_auto_shutdown(self._vm.name, self._vm.resource_group, shutdown_time, timezone)
            self.app.call_from_thread(self._load_auto_shutdown)
        except AzError as exc:
            logger.error("Failed to enable auto-shutdown for %s: %s", self._vm.name, exc.stderr)
            self.app.call_from_thread(self._show_auto_shutdown_error, exc.display_message)

    def action_disable_auto_shutdown(self) -> None:
        """Disable auto-shutdown after confirmation."""
        if self._auto_shutdown is None or not self._auto_shutdown.enabled:
            self.notify("Auto-shutdown is not enabled", severity="warning")
            return

        from azure_vm_tui.screens.vm_list import ConfirmScreen

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._do_disable_auto_shutdown()

        self.app.push_screen(
            ConfirmScreen(f"Disable auto-shutdown for {self._vm.name}?"),
            callback=on_confirm,
        )

    @work(thread=True)
    def _do_disable_auto_shutdown(self) -> None:
        """Disable auto-shutdown in a background thread."""
        self.app.call_from_thread(self.notify, f"Disabling auto-shutdown for {self._vm.name}...")
        try:
            az.disable_auto_shutdown(self._vm.name, self._vm.resource_group)
            self.app.call_from_thread(self._load_auto_shutdown)
        except AzError as exc:
            logger.error("Failed to disable auto-shutdown for %s: %s", self._vm.name, exc.stderr)
            self.app.call_from_thread(self._show_auto_shutdown_error, exc.display_message)

    def _show_auto_shutdown_error(self, message: str) -> None:
        """Show auto-shutdown error in the info panel (main thread).

        Args:
            message: Sanitized error message safe for display.
        """
        from rich.markup import escape

        first_line = escape(message.strip().split("\n")[0][:120])
        self._update_auto_shutdown_display_text(
            f"[b]Auto-Shutdown:[/b] [red]{first_line}[/red]"
        )

    def action_go_back(self) -> None:
        """Return to the VM list screen."""
        self._stats_running = False
        self.app.pop_screen()
