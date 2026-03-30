"""Tests for SSH stats collection module."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from azure_vm_tui.stats import (
    DiskStat,
    GPUStat,
    StatsError,
    VMStats,
    _build_command,
    _parse_cpu,
    _parse_disks,
    _parse_gpus,
    _parse_memory,
    collect_stats,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CPU_FIXTURE = "%Cpu(s):  5.3 us,  2.1 sy,  0.0 ni, 92.1 id,  0.3 wa,  0.0 hi,  0.2 si,  0.0 st"

MEMORY_FIXTURE = """              total        used        free      shared  buff/cache   available
Mem:          15896        4523        8234         312        3139       10861
Swap:          2048           0        2048"""

DISK_FIXTURE = """/dev/sda1       50G    23G  52%
/dev/sdb1      200G   150G  25%"""

GPU_FIXTURE = "Tesla V100-SXM2-16GB, 45, 4096, 16384"

NO_GPU_FIXTURE = "NO_GPU"


def _make_ssh_output(*sections: str) -> bytes:
    """Join sections with ---SEP--- separators, mimicking remote command output."""
    return ("\n---SEP---\n".join(sections) + "\n").encode()


# ---------------------------------------------------------------------------
# _parse_cpu
# ---------------------------------------------------------------------------


def test_parse_cpu() -> None:
    assert _parse_cpu(CPU_FIXTURE) == pytest.approx(7.4)


def test_parse_cpu_high() -> None:
    line = "%Cpu(s): 95.0 us,  4.5 sy,  0.0 ni,  0.5 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st"
    assert _parse_cpu(line) == pytest.approx(99.5)


def test_parse_cpu_malformed_returns_zero() -> None:
    assert _parse_cpu("not cpu output") == 0.0


def test_parse_cpu_empty_returns_zero() -> None:
    assert _parse_cpu("") == 0.0


# ---------------------------------------------------------------------------
# _parse_memory
# ---------------------------------------------------------------------------


def test_parse_memory() -> None:
    used, total = _parse_memory(MEMORY_FIXTURE)
    assert used == 4523
    assert total == 15896


def test_parse_memory_no_mem_line() -> None:
    assert _parse_memory("Swap: 2048 0 2048") == (0, 0)


def test_parse_memory_empty() -> None:
    assert _parse_memory("") == (0, 0)


# ---------------------------------------------------------------------------
# _parse_disks
# ---------------------------------------------------------------------------


def test_parse_disks() -> None:
    disks = _parse_disks(DISK_FIXTURE)
    assert len(disks) == 2
    assert disks[0] == DiskStat(source="/dev/sda1", size="50G", avail="23G", use_pct=52.0)
    assert disks[1] == DiskStat(source="/dev/sdb1", size="200G", avail="150G", use_pct=25.0)


def test_parse_disks_empty() -> None:
    assert _parse_disks("") == []


def test_parse_disks_skips_header() -> None:
    text = "Source          Size  Avail Use%\n/dev/sda1  50G  23G  52%"
    disks = _parse_disks(text)
    assert len(disks) == 1
    assert disks[0].source == "/dev/sda1"


def test_parse_disks_malformed_line_skipped() -> None:
    text = "/dev/sda1  50G\n/dev/sdb1  200G  150G  25%"
    disks = _parse_disks(text)
    assert len(disks) == 1
    assert disks[0].source == "/dev/sdb1"


# ---------------------------------------------------------------------------
# _parse_gpus
# ---------------------------------------------------------------------------


def test_parse_gpus() -> None:
    gpus = _parse_gpus(GPU_FIXTURE)
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu == GPUStat(name="Tesla V100-SXM2-16GB", utilization_pct=45.0, memory_used_mb=4096, memory_total_mb=16384)


def test_parse_gpus_no_gpu() -> None:
    assert _parse_gpus(NO_GPU_FIXTURE) == []


def test_parse_gpus_empty_string() -> None:
    assert _parse_gpus("") == []


def test_parse_gpus_multiple() -> None:
    text = "Tesla V100-SXM2-16GB, 45, 4096, 16384\nNVIDIA A100-SXM4-40GB, 72, 8192, 40960"
    gpus = _parse_gpus(text)
    assert len(gpus) == 2
    assert gpus[0].name == "Tesla V100-SXM2-16GB"
    assert gpus[1].name == "NVIDIA A100-SXM4-40GB"
    assert gpus[1].utilization_pct == pytest.approx(72.0)
    assert gpus[1].memory_used_mb == 8192
    assert gpus[1].memory_total_mb == 40960


def test_parse_gpus_malformed_line_skipped() -> None:
    text = "Bad line\nTesla V100-SXM2-16GB, 45, 4096, 16384"
    gpus = _parse_gpus(text)
    assert len(gpus) == 1


# ---------------------------------------------------------------------------
# _build_command
# ---------------------------------------------------------------------------


def test_build_command_contains_sep() -> None:
    cmd = _build_command()
    assert cmd.count("---SEP---") == 3


def test_build_command_contains_required_tools() -> None:
    cmd = _build_command()
    assert "top -bn1" in cmd
    assert "free -m" in cmd
    assert "df -h" in cmd
    assert "nvidia-smi" in cmd


# ---------------------------------------------------------------------------
# collect_stats (integration — paramiko mocked)
# ---------------------------------------------------------------------------


def _configure_mock_client(mock_ssh_class: MagicMock, stdout_bytes: bytes) -> MagicMock:
    """Configure mock_ssh_class.return_value to return stdout_bytes from exec_command."""
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_bytes

    mock_client = mock_ssh_class.return_value
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
    return mock_client


@patch("azure_vm_tui.stats.paramiko.SSHClient")
def test_collect_stats_success(mock_ssh_class: MagicMock) -> None:
    combined = _make_ssh_output(CPU_FIXTURE, MEMORY_FIXTURE, DISK_FIXTURE, GPU_FIXTURE)
    mock_client = _configure_mock_client(mock_ssh_class, combined)

    stats = collect_stats(host="10.0.0.1", user="azureuser", key_path="~/.ssh/id_rsa", timeout=8)

    assert isinstance(stats, VMStats)
    assert stats.cpu_pct == pytest.approx(7.4)
    assert stats.ram_used_mb == 4523
    assert stats.ram_total_mb == 15896
    assert len(stats.disks) == 2
    assert len(stats.gpus) == 1
    assert stats.gpus[0].name == "Tesla V100-SXM2-16GB"
    assert stats.collected_at.tzinfo == UTC

    mock_client.connect.assert_called_once_with(
        hostname="10.0.0.1", username="azureuser", key_filename="~/.ssh/id_rsa", timeout=8
    )
    mock_client.close.assert_called_once()


@patch("azure_vm_tui.stats.paramiko.SSHClient")
def test_collect_stats_success_no_gpu(mock_ssh_class: MagicMock) -> None:
    combined = _make_ssh_output(CPU_FIXTURE, MEMORY_FIXTURE, DISK_FIXTURE, NO_GPU_FIXTURE)
    _configure_mock_client(mock_ssh_class, combined)

    stats = collect_stats(host="10.0.0.1", user="azureuser", key_path="~/.ssh/id_rsa", timeout=8)

    assert stats.gpus == []


@patch("azure_vm_tui.stats.paramiko.SSHClient")
def test_collect_stats_timeout(mock_ssh_class: MagicMock) -> None:
    mock_client = mock_ssh_class.return_value
    mock_client.connect.side_effect = TimeoutError()

    with pytest.raises(StatsError) as exc_info:
        collect_stats(host="10.0.0.1", user="azureuser", key_path="~/.ssh/id_rsa", timeout=8)

    assert exc_info.value.reason == "SSH timeout"
    mock_client.close.assert_called_once()


@patch("azure_vm_tui.stats.paramiko.SSHClient")
def test_collect_stats_auth_failure(mock_ssh_class: MagicMock) -> None:
    mock_client = mock_ssh_class.return_value
    mock_client.connect.side_effect = paramiko.AuthenticationException()

    with pytest.raises(StatsError) as exc_info:
        collect_stats(host="10.0.0.1", user="azureuser", key_path="~/.ssh/id_rsa", timeout=8)

    assert exc_info.value.reason == "Permission denied"
    mock_client.close.assert_called_once()


@patch("azure_vm_tui.stats.paramiko.SSHClient")
def test_collect_stats_ssh_exception(mock_ssh_class: MagicMock) -> None:
    mock_client = mock_ssh_class.return_value
    mock_client.connect.side_effect = paramiko.SSHException("Connection reset by peer")

    with pytest.raises(StatsError) as exc_info:
        collect_stats(host="10.0.0.1", user="azureuser", key_path="~/.ssh/id_rsa", timeout=8)

    assert "Connection reset by peer" in exc_info.value.reason
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# StatsError
# ---------------------------------------------------------------------------


def test_stats_error_reason_attribute() -> None:
    err = StatsError("No public IP")
    assert err.reason == "No public IP"
    assert str(err) == "No public IP"
