"""SSH-based system stats collection for Azure VMs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import paramiko


@dataclass(frozen=True)
class DiskStat:
    """Stats for a single disk/filesystem."""

    source: str
    size: str
    avail: str
    use_pct: float


@dataclass(frozen=True)
class GPUStat:
    """Stats for a single GPU."""

    name: str
    utilization_pct: float
    memory_used_mb: int
    memory_total_mb: int


@dataclass(frozen=True)
class VMStats:
    """Aggregated system stats collected from a VM via SSH."""

    cpu_pct: float
    ram_used_mb: int
    ram_total_mb: int
    disks: list[DiskStat]
    gpus: list[GPUStat]
    collected_at: datetime


class StatsError(Exception):
    """Raised when stats collection fails.

    Attributes:
        reason: Human-readable description of the failure, e.g. "SSH timeout".
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse_cpu(text: str) -> float:
    """Parse CPU percentage from top output.

    Args:
        text: Single line from `top -bn1 | grep "Cpu(s)"`, e.g.
              `%Cpu(s):  5.3 us,  2.1 sy, ...`

    Returns:
        Sum of user + system CPU percentage, or 0.0 on parse failure.
    """
    try:
        us_pct = 0.0
        sy_pct = 0.0
        for part in text.split(","):
            tokens = part.strip().split()
            if len(tokens) < 2:
                continue
            label = tokens[-1]
            value = float(tokens[-2])
            if label == "us":
                us_pct = value
            elif label == "sy":
                sy_pct = value
        return round(us_pct + sy_pct, 1)
    except (ValueError, IndexError):
        return 0.0


def _parse_memory(text: str) -> tuple[int, int]:
    """Parse RAM usage from free -m output.

    Args:
        text: Full output of `free -m`.

    Returns:
        Tuple of (used_mb, total_mb), or (0, 0) on parse failure.
    """
    for line in text.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            try:
                total = int(parts[1])
                used = int(parts[2])
                return used, total
            except (ValueError, IndexError):
                return 0, 0
    return 0, 0


def _parse_disks(text: str) -> list[DiskStat]:
    """Parse disk usage from df output.

    Args:
        text: Output of `df -h --output=source,size,avail,pcent | grep -v tmpfs`.
              Header line (if present) is skipped automatically.

    Returns:
        List of DiskStat objects, empty list if text is empty or unparseable.
    """
    disks: list[DiskStat] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Filesystem") or line.startswith("Source"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            use_pct = float(parts[3].rstrip("%"))
            disks.append(DiskStat(source=parts[0], size=parts[1], avail=parts[2], use_pct=use_pct))
        except (ValueError, IndexError):
            continue
    return disks


def _parse_gpus(text: str) -> list[GPUStat]:
    """Parse GPU stats from nvidia-smi output.

    Args:
        text: Output of `nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total
              --format=csv,noheader,nounits`, or "NO_GPU" if no GPU is present.

    Returns:
        List of GPUStat objects, empty list when no GPU is present or text is unparseable.
    """
    text = text.strip()
    if not text or text == "NO_GPU":
        return []

    gpus: list[GPUStat] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            gpus.append(
                GPUStat(
                    name=parts[0],
                    utilization_pct=float(parts[1]),
                    memory_used_mb=int(parts[2]),
                    memory_total_mb=int(parts[3]),
                )
            )
        except (ValueError, IndexError):
            continue
    return gpus


def _build_command() -> str:
    """Build the combined shell command for collecting all stats in one SSH session.

    Returns:
        Shell command string that emits four sections separated by ---SEP--- markers.
    """
    sep = 'echo "---SEP---"'
    parts = [
        'top -bn1 | grep "Cpu(s)"',
        "free -m",
        "df -h --output=source,size,avail,pcent | grep -v tmpfs",
        "nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total "
        "--format=csv,noheader,nounits 2>/dev/null || echo NO_GPU",
    ]
    return f" && {sep} && ".join(parts)


def collect_stats(host: str, user: str, key_path: str, timeout: int) -> VMStats:
    """Collect system stats from a VM via SSH.

    Opens a single SSH connection, runs all stat commands in one session, and
    returns a parsed VMStats snapshot. The SSH client is always closed when done.

    Args:
        host: IP address or hostname of the VM.
        user: SSH username.
        key_path: Path to the private key file (e.g. "~/.ssh/id_rsa").
        timeout: Connection timeout in seconds.

    Returns:
        VMStats dataclass populated with the collected metrics.

    Raises:
        StatsError: When SSH authentication fails, connection times out, or
                    another SSH-level error occurs.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, username=user, key_filename=key_path, timeout=timeout)
        _, stdout, _ = client.exec_command(_build_command())
        output = stdout.read().decode()
        sections = output.split("---SEP---")

        cpu_text = sections[0].strip() if len(sections) > 0 else ""
        mem_text = sections[1].strip() if len(sections) > 1 else ""
        disk_text = sections[2].strip() if len(sections) > 2 else ""
        gpu_text = sections[3].strip() if len(sections) > 3 else "NO_GPU"

        ram_used, ram_total = _parse_memory(mem_text)

        return VMStats(
            cpu_pct=_parse_cpu(cpu_text),
            ram_used_mb=ram_used,
            ram_total_mb=ram_total,
            disks=_parse_disks(disk_text),
            gpus=_parse_gpus(gpu_text),
            collected_at=datetime.now(UTC),
        )
    except paramiko.AuthenticationException as exc:
        raise StatsError("Permission denied") from exc
    except TimeoutError:
        raise StatsError("SSH timeout") from None
    except paramiko.SSHException as exc:
        raise StatsError(str(exc)) from exc
    finally:
        client.close()
