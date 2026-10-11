import textwrap
from pathlib import Path

import pytest

from porn_hunter.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def cfg(tmp_path):
    """A valid config whose paths all live under tmp_path, with no real delays."""
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent("""
        http: {min_delay: 0, max_delay: 0, retries: 2, backoff_base: 0}
        download: {min_delay: 0, max_delay: 0, retry_backoff: 0, rate_limit_backoff: 0}
    """))
    return load_config(path)
