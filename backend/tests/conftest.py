import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Isolated data dir per test session; share the (large) CLIP model cache with the real app.
_TMP = tempfile.mkdtemp(prefix="pm-test-")
os.environ["PM_DATA_DIR"] = str(Path(_TMP) / "data")
os.environ.setdefault("PM_MODEL_DIR", str(REPO / "data" / "models"))


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    from tests.make_fixtures import make

    d = Path(_TMP) / "photos"
    make(d)
    return d
