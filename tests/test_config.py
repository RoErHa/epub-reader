"""Configuration must come from exactly one .env file — no hardcoded
values, no second config source. These tests hold that line."""
from __future__ import annotations

from pathlib import Path

import pytest

from epub_config import Config, ConfigError

MINIMAL_ENV = """
EPUB_PORT=8770
EPUB_BIND=0.0.0.0
LIBRARY_ROOT=/tmp/books
DB_PATH=books.db
COVER_CACHE=cover_cache
LOG_LEVEL=INFO
LOG_FILE=gateway.log
"""


def write_env(tmp_path: Path, body: str = MINIMAL_ENV) -> Path:
    path = tmp_path / ".env"
    path.write_text(body)
    return path


class TestLoading:
    def test_reads_values_from_the_env_file(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.port == 8770
        assert cfg.bind == "0.0.0.0"
        assert cfg.log_level == "INFO"

    def test_missing_env_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            Config.from_file(tmp_path / "nope.env")

    def test_missing_required_key_raises_naming_the_key(self, tmp_path):
        body = MINIMAL_ENV.replace("LIBRARY_ROOT=/tmp/books\n", "")
        with pytest.raises(ConfigError, match="LIBRARY_ROOT"):
            Config.from_file(write_env(tmp_path, body))

    def test_blank_required_value_is_treated_as_missing(self, tmp_path):
        body = MINIMAL_ENV.replace("LIBRARY_ROOT=/tmp/books", "LIBRARY_ROOT=")
        with pytest.raises(ConfigError, match="LIBRARY_ROOT"):
            Config.from_file(write_env(tmp_path, body))

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        body = "# a comment\n\n" + MINIMAL_ENV + "\n# trailing\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.port == 8770


class TestTypes:
    def test_port_is_an_int(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert isinstance(cfg.port, int)

    def test_non_numeric_port_raises(self, tmp_path):
        body = MINIMAL_ENV.replace("EPUB_PORT=8770", "EPUB_PORT=eight")
        with pytest.raises(ConfigError, match="EPUB_PORT"):
            Config.from_file(write_env(tmp_path, body))

    def test_paths_are_path_objects(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert isinstance(cfg.library_root, Path)
        assert isinstance(cfg.db_path, Path)

    def test_relative_paths_resolve_against_the_env_file_directory(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.db_path == tmp_path / "books.db"

    def test_absolute_paths_are_left_alone(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.library_root == Path("/tmp/books")


class TestOptionalValues:
    def test_unset_cert_host_is_none(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.cert_host is None

    def test_set_cert_host_is_returned(self, tmp_path):
        body = MINIMAL_ENV + "TAILSCALE_CERT_HOST=host.tail1234.ts.net\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.cert_host == "host.tail1234.ts.net"

    def test_unset_provider_key_is_none(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.provider_key("GOOGLE_BOOKS_API_KEY") is None

    def test_set_provider_key_is_returned(self, tmp_path):
        body = MINIMAL_ENV + "GOOGLE_BOOKS_API_KEY=abc123\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.provider_key("GOOGLE_BOOKS_API_KEY") == "abc123"

    def test_unknown_provider_key_raises(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        with pytest.raises(ConfigError, match="NOT_A_KEY"):
            cfg.provider_key("NOT_A_KEY")


class TestSecrecy:
    def test_repr_does_not_leak_secret_values(self, tmp_path):
        body = MINIMAL_ENV + "ISBNDB_API_KEY=super-secret-value\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert "super-secret-value" not in repr(cfg)


class TestTLS:
    """Certificate locations are configuration, not a guess. The service
    died with a bare FileNotFoundError when TAILSCALE_CERT_HOST was set
    but the invented default path did not exist."""

    def test_no_cert_host_means_no_tls(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.cert_host is None
        assert cfg.tls_cert_file is None
        assert cfg.tls_key_file is None

    def test_explicit_cert_paths_are_used(self, tmp_path):
        crt, key = tmp_path / "h.crt", tmp_path / "h.key"
        crt.write_text("x")
        key.write_text("x")
        body = (MINIMAL_ENV + "TAILSCALE_CERT_HOST=h.ts.net\n"
                + f"TLS_CERT_FILE={crt}\nTLS_KEY_FILE={key}\n")
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.tls_cert_file == crt
        assert cfg.tls_key_file == key

    def test_paths_are_found_next_to_the_env_file_by_host_name(self, tmp_path):
        (tmp_path / "h.ts.net.crt").write_text("x")
        (tmp_path / "h.ts.net.key").write_text("x")
        body = MINIMAL_ENV + "TAILSCALE_CERT_HOST=h.ts.net\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.tls_cert_file == tmp_path / "h.ts.net.crt"

    def test_a_missing_certificate_is_reported_clearly(self, tmp_path):
        body = MINIMAL_ENV + "TAILSCALE_CERT_HOST=absent.ts.net\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        with pytest.raises(ConfigError) as exc:
            cfg.require_tls_files()
        message = str(exc.value)
        assert "absent.ts.net" in message
        assert "TLS_CERT_FILE" in message   # says how to fix it

    def test_tls_is_optional_and_silence_means_plain_http(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.require_tls_files() is None


class TestBindingSeveralAddresses:
    """`EPUB_BIND` takes a comma-separated list, because the useful
    answer is usually more than one address: a tailnet address for the
    devices this is read on, and loopback for the tests and `run.sh`.

    Before this it was pasted straight into one `host:port` string, so
    `100.x.x.x,127.0.0.1` became the hostname `100.x.x.x,127.0.0.1` and
    the service died on startup with a `gaierror` — having, until it was
    restarted, gone on serving 0.0.0.0 from the old configuration.
    """

    def test_one_address_is_a_list_of_one(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.bind_addresses == ["0.0.0.0"]

    def test_several_addresses_are_split(self, tmp_path):
        body = MINIMAL_ENV.replace("EPUB_BIND=0.0.0.0",
                                   "EPUB_BIND=100.100.100.100,127.0.0.1")
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.bind_addresses == ["100.100.100.100", "127.0.0.1"]

    def test_spacing_around_the_commas_is_forgiven(self, tmp_path):
        body = MINIMAL_ENV.replace("EPUB_BIND=0.0.0.0",
                                   "EPUB_BIND= 100.100.100.100 , 127.0.0.1 ")
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.bind_addresses == ["100.100.100.100", "127.0.0.1"]

    def test_a_stray_comma_is_not_an_empty_address(self, tmp_path):
        body = MINIMAL_ENV.replace("EPUB_BIND=0.0.0.0",
                                   "EPUB_BIND=127.0.0.1,")
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.bind_addresses == ["127.0.0.1"]


class TestPlainBind:
    """`EPUB_PLAIN_BIND` names addresses answered over plain HTTP even
    when TLS is configured. A tailnet address needs the certificate —
    a service worker and an installed app need a secure origin — while
    a LAN address has no certificate that could name it, so it is
    served plainly rather than with a warning page in front of it."""

    def test_absent_means_nothing_is_served_plainly(self, tmp_path):
        cfg = Config.from_file(write_env(tmp_path))
        assert cfg.plain_bind_addresses == []

    def test_several_addresses_are_split(self, tmp_path):
        body = MINIMAL_ENV + "EPUB_PLAIN_BIND= 192.168.1.125 , 127.0.0.1,\n"
        cfg = Config.from_file(write_env(tmp_path, body))
        assert cfg.plain_bind_addresses == ["192.168.1.125", "127.0.0.1"]

    def test_one_address_cannot_be_both(self, tmp_path):
        body = (MINIMAL_ENV.replace("EPUB_BIND=0.0.0.0",
                                    "EPUB_BIND=100.100.100.100,192.168.1.125")
                + "EPUB_PLAIN_BIND=192.168.1.125\n")
        with pytest.raises(ConfigError, match=r"192\.168\.1\.125"):
            Config.from_file(write_env(tmp_path, body))
