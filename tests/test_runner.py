"""What the server is told to listen on, and how. Nothing here binds a
socket: the hypercorn configuration is the whole of the decision."""
from __future__ import annotations

import pytest

from epub_config import Config
from epub_gateway import GatewayRunner

ENV = """
EPUB_PORT=8770
EPUB_BIND=100.100.100.100
LIBRARY_ROOT={library}
DB_PATH=books.db
COVER_CACHE=cover_cache
LOG_LEVEL=WARNING
LOG_FILE=test.log
"""


@pytest.fixture
def make_runner(tmp_path):
    runners = []

    def make(extra: str = "") -> GatewayRunner:
        (tmp_path / "library").mkdir(exist_ok=True)
        env = tmp_path / ".env"
        env.write_text(ENV.format(library=tmp_path / "library") + extra)
        runner = GatewayRunner(Config.from_file(env))
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.close()


@pytest.fixture
def tls(tmp_path) -> str:
    (tmp_path / "h.ts.net.crt").write_text("x")
    (tmp_path / "h.ts.net.key").write_text("x")
    return "TAILSCALE_CERT_HOST=h.ts.net\n"


class TestTlsOnTheTailnetOnly:
    def test_with_tls_the_plain_addresses_are_bound_insecurely(
            self, make_runner, tls):
        cfg = make_runner(tls + "EPUB_PLAIN_BIND=192.168.1.125,127.0.0.1\n"
                          ).hypercorn_config()
        assert cfg.ssl_enabled
        assert cfg.bind == ["100.100.100.100:8770"]
        assert cfg.insecure_bind == ["192.168.1.125:8770", "127.0.0.1:8770"]

    def test_without_tls_everything_is_plain_anyway(self, make_runner):
        cfg = make_runner("EPUB_PLAIN_BIND=192.168.1.125\n").hypercorn_config()
        assert not cfg.ssl_enabled
        assert cfg.bind == ["100.100.100.100:8770", "192.168.1.125:8770"]

    def test_the_sockets_are_split_as_configured(self, make_runner, tls):
        """Hypercorn only honours `insecure_bind` while TLS is on, so
        the split is checked where hypercorn makes it."""
        cfg = make_runner(tls + "EPUB_PLAIN_BIND=192.168.1.125\n"
                          ).hypercorn_config()
        assert cfg.bind and cfg.insecure_bind
        assert set(cfg.bind).isdisjoint(cfg.insecure_bind)
