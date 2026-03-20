"""Entrypoint for azure-vm-tui."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from azure_vm_tui.app import AzureVMApp
from azure_vm_tui.az import AzError, check_az_cli, check_login
from azure_vm_tui.config import load_config


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments to parse, defaults to sys.argv[1:].

    Returns:
        Parsed namespace with a config attribute.
    """
    parser = argparse.ArgumentParser(
        prog="azure-vm-tui",
        description="Fast TUI for managing Azure VMs",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to config file (default: search .azure-vm-tui in $PWD then ~/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the azure-vm-tui application.

    Args:
        argv: CLI arguments, defaults to sys.argv[1:].
    """
    args = _parse_args(argv)

    try:
        check_az_cli()
    except AzError as exc:
        print(f"Error: {exc.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        check_login()
    except AzError as exc:
        print(f"Error: {exc.stderr}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    app = AzureVMApp(config)
    result = app.run()

    if isinstance(result, dict) and "host" in result:
        _exec_ssh(result["host"], result["user"], result["key"])


def _exec_ssh(host: str, user: str, key: str) -> None:
    """Replace this process with an SSH connection.

    Args:
        host: IP address or hostname.
        user: SSH username.
        key: Path to the private key file.
    """
    key_path = str(Path(key).expanduser())
    ssh_args = ["ssh", "-i", key_path, f"{user}@{host}"]
    print(f"Connecting to {user}@{host}...")
    os.execvp("ssh", ssh_args)


if __name__ == "__main__":
    main()
