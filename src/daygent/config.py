"""Optional daygent.yml / daygent.yaml configuration. Local files only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from daygent.exceptions import DaygentConfigError

DEFAULT_OUTPUT = ".daygent/graph.json"
CONFIG_FILENAMES = ("daygent.yml", "daygent.yaml")
KNOWN_CONFIG_KEYS = frozenset({"include", "exclude", "output"})


class DaygentConfig(BaseModel):
    """Repo-local scan configuration.

    Keys (D-005): include, exclude, output.
    Unknown keys are ignored with a warning at load time.
    """

    model_config = ConfigDict(extra="ignore")

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    output: str = DEFAULT_OUTPUT

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def _require_string_list(cls, value: object) -> object:
        """Treat null as empty; reject a bare string (would look like a glob list)."""
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError("must be a list of glob strings")
        return value

    @field_validator("output", mode="before")
    @classmethod
    def _require_output_string(cls, value: object) -> object:
        """Empty output falls back to the default artifact path."""
        if value is None or value == "":
            return DEFAULT_OUTPUT
        return value

    def output_path(self, root: Path) -> Path:
        """Resolve the graph output path against the scan root."""
        path = Path(self.output)
        return path if path.is_absolute() else root / path


def default_config() -> DaygentConfig:
    """Return built-in defaults when no config file is present."""
    return DaygentConfig()


def _load_mapping(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Load YAML mapping and collect unknown-key warnings."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DaygentConfigError(f"Invalid YAML in {path}: {exc}") from exc
    except OSError as exc:
        raise DaygentConfigError(f"Unable to read {path}: {exc}") from exc

    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        raise DaygentConfigError(f"{path} must contain a YAML mapping")

    warnings: list[str] = []
    unknown = [str(key) for key in raw if key not in KNOWN_CONFIG_KEYS]
    if unknown:
        warnings.append(
            f"Unknown config keys in {path.name} ignored: {', '.join(sorted(unknown))}"
        )
    return raw, warnings


def load_config(
    root: Path,
    config_path: Path | None = None,
) -> tuple[DaygentConfig, list[str]]:
    """Load daygent.yml / daygent.yaml from root or an explicit path.

    Missing file → defaults. Invalid YAML or schema → DaygentConfigError.
    Does not resolve environment variables.
    """
    path = config_path
    if path is None:
        for name in CONFIG_FILENAMES:
            candidate = root / name
            if candidate.is_file():
                path = candidate
                break
    if path is None:
        return default_config(), []
    if not path.is_file():
        raise DaygentConfigError(f"Config file not found: {path}")

    mapping, warnings = _load_mapping(path)
    try:
        config = DaygentConfig.model_validate(mapping)
    except ValidationError as exc:
        raise DaygentConfigError(f"Invalid config in {path}: {exc}") from exc
    return config, warnings
