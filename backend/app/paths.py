"""Path safety checks shared by every feature that writes outside the app's data folder."""
from __future__ import annotations

import os
from pathlib import Path

from . import config, db


def inside(p: Path, parent: Path) -> bool:
    return p == parent or parent in p.parents


def check_outside_library(dest: str | Path, *, create: bool = False, what: str = "Destination") -> Path:
    """Resolve `dest` and make sure it is a writable folder outside DATA_DIR and every scanned folder.

    Anything written inside a scanned folder would be picked up as new photos (and duplicates) on the
    next scan, so that is always refused.
    """
    p = Path(dest).expanduser()
    if create and not p.exists():
        p.mkdir(parents=True)
    p = p.resolve()
    if not p.is_dir():
        raise ValueError(f"{what} folder does not exist")
    if inside(p, config.DATA_DIR) or inside(config.DATA_DIR, p):
        raise ValueError(f"{what} overlaps the app's data folder")
    for r in db.get().execute("SELECT path FROM roots"):
        root = Path(r["path"])
        if inside(p, root):
            raise ValueError(f"{what} is inside a scanned folder ({root}); files there would be picked up as duplicates on the next scan")
        if inside(root, p):
            raise ValueError(f"{what} contains a scanned folder ({root})")
    if not os.access(p, os.W_OK):
        raise ValueError(f"{what} folder is not writable")
    return p
