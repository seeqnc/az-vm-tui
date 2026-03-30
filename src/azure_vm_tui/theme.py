"""seeqnc brand theme for the TUI."""

from __future__ import annotations

from textual.theme import Theme

SEEQNC_YELLOW = "#F5D10D"
SEEQNC_BLACK = "#0A0A0A"
SEEQNC_SURFACE = "#1A1A1A"
SEEQNC_TEXT = "#F5F5F5"
SEEQNC_MUTED = "#A0A0A0"

seeqnc_theme = Theme(
    name="seeqnc",
    primary=SEEQNC_YELLOW,
    secondary=SEEQNC_MUTED,
    accent=SEEQNC_YELLOW,
    background=SEEQNC_BLACK,
    surface=SEEQNC_SURFACE,
    panel="#222222",
    boost="#2A2A2A",
    warning=SEEQNC_YELLOW,
    error="#E05252",
    success="#4EC970",
    foreground=SEEQNC_TEXT,
    dark=True,
    variables={
        "footer-background": SEEQNC_SURFACE,
        "footer-foreground": SEEQNC_MUTED,
        "block-cursor-foreground": SEEQNC_BLACK,
        "block-cursor-background": SEEQNC_YELLOW,
    },
)
