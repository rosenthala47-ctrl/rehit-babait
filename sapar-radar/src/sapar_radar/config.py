"""Configuration loading: YAML files + environment secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return data


@dataclass
class Platforms:
    """Domain lists and page keywords used to detect existing booking systems."""

    booking: set[str] = field(default_factory=set)
    social: set[str] = field(default_factory=set)
    builders: set[str] = field(default_factory=set)
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Platforms":
        data = _load_yaml(path or CONFIG_DIR / "booking_platforms.yaml")
        booking = {
            d.lower().strip()
            for key in ("israeli", "global")
            for d in data.get(key, []) or []
        }
        keywords = [
            k.lower().strip()
            for key in ("booking_keywords_he", "booking_keywords_en")
            for k in data.get(key, []) or []
        ]
        return cls(
            booking=booking,
            social={d.lower().strip() for d in data.get("social_only", []) or []},
            builders={d.lower().strip() for d in data.get("site_builders", []) or []},
            keywords=keywords,
        )


@dataclass
class Config:
    """The whole run configuration."""

    raw: dict[str, Any]
    platforms: Platforms
    do_not_contact: set[str]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_DIR / "config.yaml"
        if not path.exists():
            example = CONFIG_DIR / "config.example.yaml"
            if example.exists():
                path = example
            else:
                raise ConfigError(
                    "no config found - copy config/config.example.yaml "
                    "to config/config.yaml"
                )
        return cls(
            raw=_load_yaml(path),
            platforms=Platforms.load(),
            do_not_contact=load_do_not_contact(),
        )

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a nested key, e.g. cfg.get('filters.min_score', 60)."""
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- convenience accessors used all over the pipeline -----------------
    @property
    def queries(self) -> list[str]:
        return list(self.get("search.queries", []) or [])

    @property
    def areas(self) -> list[str]:
        return list(self.get("search.areas", []) or [])

    @property
    def min_score(self) -> int:
        return int(self.get("filters.min_score", 60))


def load_do_not_contact(path: Path | None = None) -> set[str]:
    """Phone numbers / place ids that must never be reported."""
    from .models import normalize_phone

    path = path or CONFIG_DIR / "do_not_contact.txt"
    if not path.exists():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        entries.add(line)
        if any(ch.isdigit() for ch in line):
            normalized = normalize_phone(line)
            if normalized:
                entries.add(normalized)
    return entries


def require_env(name: str) -> str:
    """Fetch a secret from the environment or fail with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"environment variable {name} is not set - see .env.example"
        )
    return value
