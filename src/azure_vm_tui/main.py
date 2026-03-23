"""Entrypoint for az-vm-tui."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path

from azure_vm_tui.app import AzureVMApp
from azure_vm_tui.az import AzError, check_az_cli, check_login
from azure_vm_tui.config import load_config, validate_ssh_key_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments to parse, defaults to sys.argv[1:].

    Returns:
        Parsed namespace with a config attribute.
    """
    parser = argparse.ArgumentParser(
        prog="az-vm-tui",
        description="Fast TUI for managing Azure VMs",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to config file (default: search .az-vm-tui in $PWD then ~/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the az-vm-tui application.

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
        try:
            validate_ssh_key_path(result["key"])
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        _exec_ssh(result["host"], result["user"], result["key"])


_SSH_USER_RE = re.compile(r"[a-zA-Z0-9._-]{1,64}")


def _exec_ssh(host: str, user: str, key: str) -> None:
    """Replace this process with an SSH connection.

    Args:
        host: IP address (validated before use).
        user: SSH username (alphanumeric, dots, hyphens, underscores).
        key: Path to the private key file.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        print(f"Error: invalid IP address: {host!r}", file=sys.stderr)
        sys.exit(1)
    if not _SSH_USER_RE.fullmatch(user):
        print(f"Error: invalid SSH username: {user!r}", file=sys.stderr)
        sys.exit(1)
    key_path = str(Path(key).expanduser())
    ssh_args = ["ssh", "-i", key_path, f"{user}@{host}"]
    print(f"Connecting to {user}@{host}...")
    os.execvp("ssh", ssh_args)


if __name__ == "__main__":
    main()
