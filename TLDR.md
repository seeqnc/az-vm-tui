# az-vm-tui

Terminal UI for managing Azure VMs. List, start, stop, inspect — with optional live system stats over SSH.

## Quickstart

```bash
az login
uv venv && uv pip install -e ".[dev]"
uv run az-vm-tui
```

## Keys

| Key | Action |
|-----|--------|
| `j`/`k` | Navigate |
| `s` | Start VM |
| `x` | Stop VM |
| `i` | Detail view |
| `/` | Fuzzy search (fzf) |
| `r` | Refresh |
| `q` | Quit |

## Config

Optional. Place as `~/.az-vm-tui` or `./.az-vm-tui` (TOML):

```toml
[azure]
resource_group = "my-rg"

[ui]
show_stats = true

[ssh]
user = "azureuser"
key = "~/.ssh/id_rsa"
```

## Requirements

- Python 3.11+
- Azure CLI (`az`), logged in
- fzf (optional)
