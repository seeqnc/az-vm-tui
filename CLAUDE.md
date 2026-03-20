# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Azure VM TUI — a Textual-based terminal UI for listing, starting, stopping, and inspecting Azure VMs with optional live system stats via SSH.

**Status:** Greenfield. The spec below defines the target architecture; source code has not been written yet.

## Commands

```bash
# Setup
uv venv && uv pip install -e ".[dev]"

# Run
uv run python -m azure_vm_tui

# Test
uv run pytest tests/ -v

# Single test
uv run pytest tests/test_az.py -v -k "test_name"

# Lint & format
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# Type check
uv run ty check src/
```

## Stack

- **Python 3.11+** with `uv` for dependency management
- **textual** (TUI framework), **paramiko** (SSH for stats)
- **az CLI** (VM operations, always `--output json`)
- **fzf** (optional fuzzy search, subprocess)
- Config: `.azure-vm-tui` (TOML, parsed with stdlib `tomllib`)

## Architecture

The app follows a layered design: TUI screens → service modules → external tools (az CLI, SSH).

### Data flow

```
app.py (Textual App)
  ├── screens/vm_list.py   ← az.list_vms()
  ├── screens/vm_detail.py ← az.get_vm_info() + stats.collect()
  │
  ├── az.py         → subprocess: `az` CLI (JSON output only)
  ├── stats.py      → paramiko SSH session (background thread, never blocks UI)
  ├── config.py     → tomllib: loads .azure-vm-tui from $PWD then ~/
  └── fzf.py        → subprocess: `fzf` (graceful fallback if missing)
```

### Key design rules

- **az.py**: All `az` calls go through this module. Always use `--output json`. Raise `AzError` on failure — callers never inspect raw stderr.
- **stats.py**: Only active when `show_stats = true`. Runs in a background thread. Collects CPU/RAM/disk/GPU via a single SSH session per cycle using `---SEP---` delimiters. Returns `VMStats` dataclass. GPU stats via `nvidia-smi` with `NO_GPU` fallback.
- **config.py**: TOML config searched in order: `$PWD/.azure-vm-tui`, `~/.azure-vm-tui`. First found wins. All keys optional with sensible defaults.
- **fzf.py**: Pipes VM names to fzf stdin, returns selection or `None`. Falls back gracefully if fzf not on PATH.
- **Screens**: No direct subprocess or SSH calls — always go through service modules.

### Error strategy

- Missing `az` CLI → actionable message + exit 1
- Not logged in → detect `az account show` failure → prompt `az login`
- SSH failures → display in stats panel, never crash TUI
- VM operation failures → inline status bar error + log to `~/.azure-vm-tui.log`

## Testing

Mock subprocess calls in `test_az.py`, mock paramiko in `test_stats.py`, test config merging/validation in `test_config.py`. No Textual screen tests for now.

## Non-goals

No VM creation/deletion, no SSH terminal passthrough, no cost/billing, no Windows support, no multi-subscription simultaneous view.
