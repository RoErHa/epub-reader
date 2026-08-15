from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from epub_config import Config  # noqa: E402
from epub_server import EpubGateway  # noqa: E402
from tests.epub_fixtures import EpubBuilder  # noqa: E402

ENV_BODY = """
EPUB_PORT=8770
EPUB_BIND=127.0.0.1
LIBRARY_ROOT={library}
DB_PATH=books.db
COVER_CACHE=cover_cache
LOG_LEVEL=WARNING
LOG_FILE=test.log
"""


def no_network(url, headers=None):
    """The suite never talks to a provider. Anything that tries gets an
    immediate refusal rather than a real request — and, before this
    existed, a 48-second one when Open Library was having a bad day."""
    raise OSError(f"the test suite does not use the network ({url})")


@pytest.fixture
def library(tmp_path) -> Path:
    """A library folder holding one plain and one hostile book."""
    root = tmp_path / "library"
    root.mkdir()
    EpubBuilder(title="Test Book", author="Test Author").build(
        root / "test-book.epub"
    )
    EpubBuilder(title="Hostile Book", author="Test Author", scripted=True).build(
        root / "hostile.epub"
    )
    return root


@pytest.fixture
def config(tmp_path, library) -> Config:
    env = tmp_path / ".env"
    env.write_text(ENV_BODY.format(library=library))
    return Config.from_file(env)


@pytest.fixture
def gateway(config) -> EpubGateway:
    gw = EpubGateway(config, fetch=no_network)
    gw.reindex()   # the real service indexes at startup
    yield gw
    gw.close()


@pytest.fixture
def client(gateway) -> TestClient:
    with TestClient(gateway.app) as c:
        yield c
