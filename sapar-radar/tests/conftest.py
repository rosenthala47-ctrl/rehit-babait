import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sapar_radar.config import Platforms  # noqa: E402


@pytest.fixture
def platforms() -> Platforms:
    """The real config, so the tests also guard the YAML from typos."""
    return Platforms.load()
