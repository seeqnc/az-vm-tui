"""Modal dialog for configuring VM auto-shutdown time and timezone."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, Static

from azure_vm_tui.validators import validate_shutdown_time


class AutoShutdownDialog(Screen[tuple[str, str] | None]):
    """Dialog for setting auto-shutdown time and timezone.

    Args:
        default_time: Pre-filled time value in HHMM format.
        default_timezone: Pre-filled timezone string.
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
    ]

    def __init__(self, default_time: str = "1900", default_timezone: str = "UTC") -> None:
        super().__init__()
        self._default_time = default_time
        self._default_timezone = default_timezone

    def compose(self) -> ComposeResult:
        """Build the dialog layout."""
        yield Static("Configure Auto-Shutdown", id="dialog-title")
        yield Static("Time (HHMM, 24h format):")
        yield Input(value=self._default_time, id="time-input", max_length=4)
        yield Static("Timezone (e.g. UTC, Eastern Standard Time):")
        yield Input(value=self._default_timezone, id="timezone-input")
        yield Static("", id="dialog-error")
        yield Static("[Tab] Next field  [Enter] Confirm  [Escape] Cancel", id="dialog-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter on an input field.

        If pressed on the time field, move focus to timezone.
        If pressed on the timezone field, validate and submit.
        """
        if event.input.id == "time-input":
            self.query_one("#timezone-input", Input).focus()
            return

        self._try_submit()

    def _try_submit(self) -> None:
        """Validate both fields and dismiss if valid."""
        time_value = self.query_one("#time-input", Input).value.strip()
        tz_value = self.query_one("#timezone-input", Input).value.strip()
        error_bar = self.query_one("#dialog-error", Static)

        try:
            validate_shutdown_time(time_value)
        except ValueError as exc:
            error_bar.update(f"[red]{exc}[/red]")
            self.query_one("#time-input", Input).focus()
            return

        if not tz_value:
            error_bar.update("[red]Timezone cannot be empty[/red]")
            self.query_one("#timezone-input", Input).focus()
            return

        self.dismiss((time_value, tz_value))

    def action_cancel(self) -> None:
        """Dismiss without changes."""
        self.dismiss(None)
