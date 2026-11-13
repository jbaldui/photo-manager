"""Only one job that reads or changes the library may run at a time."""
from __future__ import annotations


def busy(exclude: str | None = None) -> str | None:
    """Name of the job currently running, or None."""
    from . import autoprocess, enhance, organize, quarantine
    from .jobs import runner

    checks = {
        "auto": lambda: autoprocess.runner.running(),
        "scan": runner.running,
        "organize": lambda: organize.job.running(),
        "quarantine": lambda: quarantine.job.running(),
        "enhance": lambda: enhance.job.running(),
    }
    for name, fn in checks.items():
        if name != exclude and fn():
            return name
    return None


LABELS = {
    "auto": "An Auto Process run",
    "scan": "A scan",
    "organize": "An organize copy",
    "quarantine": "A move to quarantine",
    "enhance": "An enhance job",
}


def ensure_idle(exclude: str | None = None) -> None:
    b = busy(exclude)
    if b:
        raise RuntimeError(f"{LABELS[b]} is in progress; wait for it to finish or stop it first")
