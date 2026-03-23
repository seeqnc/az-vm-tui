"""Textual TUI application for Azure VM management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

from textual.app import App

from azure_vm_tui.config import AppConfig
from azure_vm_tui.screens.vm_list import VMListScreen

LOG_FILE = Path.home() / ".az-vm-tui.log"

logger = logging.getLogger("azure_vm_tui")


class AzureVMApp(App):
    """Main application for Azure VM TUI."""

    TITLE = "az-vm-tui"

    CSS = """
    Screen {
        background: $surface;
    }

    DataTable {
        height: 1fr;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }

    #error-bar {
        dock: bottom;
        height: 1;
        background: $error;
        color: $text;
        padding: 0 1;
        display: none;
    }

    .stats-panel {
        height: auto;
        max-height: 50%;
        border: solid $primary;
        padding: 1;
    }

    .stats-label {
        width: 100%;
        text-style: bold;
    }

    .stats-error {
        color: $error;
    }

    ProgressBar {
        width: 100%;
        height: 1;
    }
    """

    BINDINGS: ClassVar = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: AppConfig) -> None:
        """Initialise the app with the loaded config.

        Args:
            config: Fully resolved application configuration.
        """
        super().__init__()
        self.config = config
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Configure file logging for VM operation errors."""
        handler = logging.FileHandler(LOG_FILE)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def on_mount(self) -> None:
        """Push the VM list screen on startup."""
        self.push_screen(VMListScreen(self.config))
