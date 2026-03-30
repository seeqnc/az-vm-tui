"""fzf subprocess helper for fuzzy VM selection."""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def fzf_available() -> bool:
    """Check if fzf is available on PATH."""
    return shutil.which("fzf") is not None


def fzf_select(items: list[str], prompt: str = "VM> ") -> str | None:
    """Launch fzf with items and return the selected item.

    Args:
        items: List of strings to select from.
        prompt: Prompt string shown in fzf.

    Returns:
        The selected item string, or None if user cancelled or fzf is not available.
    """
    if not fzf_available():
        logger.warning("fzf not found on PATH, falling back to built-in navigation")
        return None

    try:
        result = subprocess.run(
            ["fzf", "--prompt", prompt, "--height=40%", "--border", "--ansi"],
            input="\n".join(items),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except OSError:
        logger.warning("Failed to launch fzf", exc_info=True)
        return None
