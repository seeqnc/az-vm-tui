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
from azure_vm_tui.az import AzError, VMInfo
from azure_vm_tui.config import AppConfig, validate_ssh_key_path
from azure_vm_tui.stats import StatsError, VMStats, collect_stats

logger = logging.getLogger("azure_vm_tui")


class VMDetailScreen(Screen):
    """Detail screen showing VM info and optional live system stats.

    Args:
        vm: The VM whose details are displayed.
        config: Application configuration.
    """

    BINDINGS: ClassVar = [
        Binding("b", "go_back", "Back", show=True),
        Binding("escape", "go_back", "Back", show=False),
    ]

    def __init__(self, vm: VMInfo, config: AppConfig) -> None:
        super().__init__()
        self._vm = vm
        self._config = config
        self._stats_running = False

    def compose(self) -> ComposeResult:
        """Build the detail screen layout."""
        yield Header()
        yield Vertical(
            Static(self._build_info_text(), id="vm-info"),
            id="info-panel",
        )
        if self._config.ui.show_stats:
            yield Vertical(
                Static("System Stats", classes="stats-label"),
                Static("Loading stats...", id="stats-content"),
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
        """Start stats collection if enabled and load IP address."""
        self._load_ip()
        if self._config.ui.show_stats:
            self._stats_running = True
            self._collect_stats_loop()

    @work(thread=True)
    def _load_ip(self) -> None:
        """Load the public IP address in a background thread."""
        try:
            ip = az.get_vm_ip(self._vm.name, self._vm.resource_group)
            ip_display = ip or "none"
        except AzError as exc:
            logger.error("Failed to get IP for %s: %s", self._vm.name, exc.stderr)
            ip_display = "error"
        self.app.call_from_thread(self._update_info, ip_display)

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

        Separated from the loop so each fetch is independently bounded.
        """
        try:
            ip = az.get_vm_ip(self._vm.name, self._vm.resource_group)
            if ip is None:
                self.app.call_from_thread(self._update_stats_error, "No public IP — cannot collect stats")
                return
            validate_ssh_key_path(self._config.ssh.key)
            key_path = str(Path(self._config.ssh.key).expanduser())
            vm_stats = collect_stats(
                host=ip,
                user=self._config.ssh.user,
                key_path=key_path,
                timeout=self._config.ssh.timeout,
            )
            self.app.call_from_thread(self._update_stats_display, vm_stats)
        except ValueError as exc:
            self.app.call_from_thread(self._update_stats_error, str(exc))
        except StatsError as exc:
            self.app.call_from_thread(self._update_stats_error, exc.reason)
        except AzError as exc:
            logger.error("Azure error fetching stats for %s: %s", self._vm.name, exc.stderr)
            self.app.call_from_thread(self._update_stats_error, f"Azure error: {exc.display_message}")

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

    def action_go_back(self) -> None:
        """Return to the VM list screen."""
        self._stats_running = False
        self.app.pop_screen()
