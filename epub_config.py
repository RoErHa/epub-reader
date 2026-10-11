"""Configuration loading.

One `.env` file is the only source of configuration in this project —
every secret, key, port and path lives there and nowhere else. Nothing
in the codebase may carry a hardcoded default for a deployment value.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values


class ConfigError(RuntimeError):
    """Raised when configuration is missing, blank or unusable."""


class Config:
    """Typed, read-only view over the `.env` file."""

    REQUIRED: tuple[str, ...] = (
        "EPUB_PORT",
        "EPUB_BIND",
        "LIBRARY_ROOT",
        "DB_PATH",
        "COVER_CACHE",
        "LOG_LEVEL",
        "LOG_FILE",
    )

    PROVIDER_KEYS: frozenset[str] = frozenset(
        {
            "OPENLIBRARY_CONTACT_EMAIL",
            "GOOGLE_BOOKS_API_KEY",
            "HARDCOVER_API_TOKEN",
            "ISBNDB_API_KEY",
        }
    )

    def __init__(self, values: Mapping[str, str | None], base_dir: Path) -> None:
        self._values = {k: (v or "").strip() for k, v in values.items()}
        self._base_dir = base_dir
        self._validate()

    # ── construction ──────────────────────────────────────────────────
    @classmethod
    def from_file(cls, path: Path | str) -> Config:
        path = Path(path)
        if not path.is_file():
            raise ConfigError(
                f"Configuration file not found: {path}. "
                "Copy .env.example to .env and fill it in."
            )
        return cls(dotenv_values(path), path.resolve().parent)

    def _validate(self) -> None:
        for key in self.REQUIRED:
            if not self._values.get(key):
                raise ConfigError(
                    f"{key} is required but missing or blank in the .env file."
                )
        self._as_int("EPUB_PORT")
        both = set(self.bind_addresses) & set(self.plain_bind_addresses)
        if both:
            raise ConfigError(
                f"{', '.join(sorted(both))} is in both EPUB_BIND and "
                "EPUB_PLAIN_BIND. An address is served over TLS or plainly, "
                "not both — remove it from one of them."
            )

    # ── helpers ───────────────────────────────────────────────────────
    def _as_int(self, key: str) -> int:
        raw = self._values[key]
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key} must be a whole number, got {raw!r}.") from exc

    def _as_path(self, key: str) -> Path:
        path = Path(self._values[key]).expanduser()
        return path if path.is_absolute() else self._base_dir / path

    # ── required values ───────────────────────────────────────────────
    @property
    def port(self) -> int:
        return self._as_int("EPUB_PORT")

    @property
    def bind(self) -> str:
        return self._values["EPUB_BIND"]

    @property
    def bind_addresses(self) -> list[str]:
        """Every address to answer on, because the useful answer is
        usually more than one: a tailnet address for the devices this is
        read on, and loopback for `run.sh` and the tests. Pasted into a
        single `host:port` it became a hostname with a comma in it, and
        the service died on startup with a `gaierror`."""
        return self._addresses("EPUB_BIND")

    @property
    def plain_bind_addresses(self) -> list[str]:
        """Addresses answered over plain HTTP even when TLS is on. The
        tailnet name needs the certificate — a service worker and an
        installed app need a secure origin — but a LAN address has no
        certificate that could name it, so TLS there is only a warning
        page in front of the library."""
        return self._addresses("EPUB_PLAIN_BIND")

    def _addresses(self, key: str) -> list[str]:
        raw = self._values.get(key, "")
        return [a.strip() for a in raw.split(",") if a.strip()]

    @property
    def library_root(self) -> Path:
        return self._as_path("LIBRARY_ROOT")

    @property
    def db_path(self) -> Path:
        return self._as_path("DB_PATH")

    @property
    def cover_cache(self) -> Path:
        return self._as_path("COVER_CACHE")

    @property
    def log_level(self) -> str:
        return self._values["LOG_LEVEL"].upper()

    @property
    def log_file(self) -> Path:
        return self._as_path("LOG_FILE")

    # ── optional values ───────────────────────────────────────────────
    @property
    def repair_on_scan(self) -> bool:
        """Whether a scan also checks authors and titles.

        Off unless asked for: the passes reach a provider, and doing
        that on every restart is not something to decide for someone.
        """
        return self._values.get("REPAIR_ON_SCAN", "").strip().lower() in (
            "1", "true", "yes", "on")

    @property
    def tidy_on_scan(self) -> bool:
        """Whether a scan also reads the filenames it just found.

        **On unless turned off**, which is the opposite of
        `REPAIR_ON_SCAN` and for the opposite reason: these passes read
        names and write to our own database. They reach no provider, cost
        nobody a request, and keep what the file said in `file_title`, so
        doing them for you is a kindness rather than a decision taken on
        your behalf.
        """
        return self._values.get("TIDY_ON_SCAN", "").strip().lower() not in (
            "0", "false", "no", "off")

    # Open Library publishes TWO limits: 1 request/second anonymously and
    # 3/second to a client that identifies itself with an application name
    # and a contact address. Which one applies to us is a fact about the
    # configuration — OPENLIBRARY_CONTACT_EMAIL is what does the
    # identifying — so neither the default nor the cap can be a constant.
    # Saying "which we do" here was wrong whenever that address was blank,
    # and it shipped a default of 2 to clients allowed 1.
    PROVIDER_RPS_ANONYMOUS = 1.0
    PROVIDER_RPS_IDENTIFIED = 2.0   # headroom under their 3; not a bulk backend
    PROVIDER_RPS_CEILING = 3.0

    @property
    def _identified(self) -> bool:
        """Whether we tell Open Library who is asking."""
        return bool(self.provider_key("OPENLIBRARY_CONTACT_EMAIL"))

    @property
    def provider_rps_default(self) -> float:
        return (self.PROVIDER_RPS_IDENTIFIED if self._identified
                else self.PROVIDER_RPS_ANONYMOUS)

    @property
    def provider_rps_ceiling(self) -> float:
        """Their ceiling depends on who is asking, so ours must too —
        otherwise 'fill the contact address in before raising this' is
        advice nothing enforces."""
        return (self.PROVIDER_RPS_CEILING if self._identified
                else self.PROVIDER_RPS_ANONYMOUS)

    @property
    def provider_max_rps(self) -> float:
        raw = self._values.get("PROVIDER_MAX_RPS", "").strip()
        try:
            rate = float(raw)
        except ValueError:
            return self.provider_rps_default
        if rate <= 0:
            return self.provider_rps_default
        return min(rate, self.provider_rps_ceiling)

    # A cover sweep is 400 lookups in one sitting, which is closer to
    # bulk than to browsing however politely it is paced — so it gets its
    # own, far slower clock: one request a minute, walking the library
    # overnight rather than in ten minutes.
    COVER_INTERVAL_DEFAULT = 60.0

    @property
    def cover_fetch_interval(self) -> float:
        """Seconds between provider requests while fetching covers."""
        raw = self._values.get("COVER_FETCH_INTERVAL", "").strip()
        try:
            seconds = float(raw)
        except ValueError:
            return self.COVER_INTERVAL_DEFAULT
        return seconds if seconds > 0 else self.COVER_INTERVAL_DEFAULT

    @property
    def cert_host(self) -> str | None:
        return self._values.get("TAILSCALE_CERT_HOST") or None

    def _tls_path(self, key: str, suffix: str) -> Path | None:
        """Explicit path if given, else <cert host><suffix> beside the
        .env file. Nothing is invented: if neither exists, this returns
        None and require_tls_files() explains what to set."""
        explicit = self._values.get(key)
        if explicit:
            return self._as_path(key)
        host = self.cert_host
        if not host:
            return None
        candidate = self._base_dir / f"{host}{suffix}"
        return candidate if candidate.is_file() else None

    @property
    def tls_cert_file(self) -> Path | None:
        return self._tls_path("TLS_CERT_FILE", ".crt")

    @property
    def tls_key_file(self) -> Path | None:
        return self._tls_path("TLS_KEY_FILE", ".key")

    def require_tls_files(self) -> tuple[Path, Path] | None:
        """Resolve the certificate pair, or explain what is missing.

        Returns None when TLS is not configured at all — plain HTTP is a
        valid choice, an unreadable certificate is not.
        """
        if not self.cert_host:
            return None
        cert, key = self.tls_cert_file, self.tls_key_file
        missing = [
            name for name, path in (("certificate", cert), ("private key", key))
            if path is None or not path.is_file()
        ]
        if missing:
            raise ConfigError(
                f"TAILSCALE_CERT_HOST is set to {self.cert_host!r} but the "
                f"{' and '.join(missing)} could not be found.\n"
                f"Looked for {self._base_dir}/{self.cert_host}.crt and .key.\n"
                "Set TLS_CERT_FILE and TLS_KEY_FILE in the .env file, or "
                "clear TAILSCALE_CERT_HOST to serve plain HTTP."
            )
        return cert, key

    def provider_key(self, name: str) -> str | None:
        if name not in self.PROVIDER_KEYS:
            raise ConfigError(
                f"{name} is not a known provider key. "
                f"Known keys: {', '.join(sorted(self.PROVIDER_KEYS))}."
            )
        return self._values.get(name) or None

    def __repr__(self) -> str:
        # Never render values — this object holds API keys.
        return f"<Config keys={len(self._values)} base_dir={self._base_dir}>"
