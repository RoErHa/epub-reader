"""Configuration loading.

One `.env` file is the only source of configuration in this project —
every secret, key, port and path lives there and nowhere else. Nothing
in the codebase may carry a hardcoded default for a deployment value.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

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
    def from_file(cls, path: Path | str) -> "Config":
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
    def cert_host(self) -> str | None:
        return self._values.get("TAILSCALE_CERT_HOST") or None

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
