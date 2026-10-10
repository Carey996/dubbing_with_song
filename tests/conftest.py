from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


# The persistence layer resolves DUBBING_DATA_DIR at import time, so this has to run before
# the first "backend.app.*" import. pytest imports conftest.py first, which makes this the
# right place: without it every test run writes projects and rows into the real <repo>/data.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="dubbing-test-data-"))
os.environ["DUBBING_DATA_DIR"] = str(_TEST_DATA_DIR)

# backend.app.main calls load_env() at import time, which copies the developer's .env into
# os.environ for every key it does not already define. These tests assert against the documented
# defaults, so a local .env that selects another provider (for example TTS_PROVIDER=minimax with
# TTS_RESPONSE_FORMAT=wav) would silently change the behaviour under test. Skipping the file keeps
# the suite reproducible from any checkout; tests that need configuration set it themselves.
os.environ["DUBBING_SKIP_ENV_FILE"] = "1"


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir() -> Path:
    yield _TEST_DATA_DIR
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
