"""
src/config.py — `.traceability.yml` loader
==========================================
Single source of truth for the review engine. The CLI, the GitHub Action, the
GitHub App and (later) the API all read the same file, so they apply the same
deterministic rules.

    # .traceability.yml
    version: 1
    srs: docs/SRS.docx
    openapi: openapi.yaml            # or a list of specs
    thresholds:
      compliant: 0.55                # best match >= this -> requirement covered
      partial: 0.40                  # >= this -> partial
      drift: -0.10                   # matrix drift: delta <= this is drift
      regression_strong: -0.05       # PR gate, requirement strongly matched (score_v1 >= strong_relevance)
      regression_weak: -0.08         # PR gate, weaker match (relevance_floor <= score_v1 < strong_relevance)
      strong_relevance: 0.60
      relevance_floor: 0.35
      link_top_k: 3                  # PR gate checks only the requirement's top-3 trace links (0 = all pairs)
    fail_on: block                   # block | critical | never
    srs_extraction:
      id_pattern: null
      include_nfr: true
      extra_blocklist: [Stakeholders]
    code:                            # optional (M5): also review changed Python code
      paths: ["app/**/*.py"]         # globs; ** spans directories
      exclude: ["tests/**"]
      model: jinaai/jina-embeddings-v2-base-code   # or "lexical" (offline fallback)
      thresholds: {}                 # overrides of the embedder's calibrated code thresholds

Relative paths are resolved against the directory that contains the file.
Environment variables override the file (CI convenience):
    TRACEABILITY_<THRESHOLD>   e.g. TRACEABILITY_COMPLIANT=0.6, TRACEABILITY_REGRESSION_WEAK=-0.1
    TRACEABILITY_FAIL_ON       block | critical | never
    DRIFT_THRESHOLD            legacy name, same as TRACEABILITY_DRIFT
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from src.srs.options import ExtractionOptions

CONFIG_FILENAME = ".traceability.yml"
CONFIG_ALT_FILENAMES = (".traceability.yaml",)
SUPPORTED_CONFIG_VERSIONS = (1,)
SRS_EXTENSIONS = (".docx", ".pdf", ".md", ".markdown", ".txt")
SPEC_EXTENSIONS = (".json", ".yaml", ".yml")
ENV_PREFIX = "TRACEABILITY_"


class ConfigError(ValueError):
    """The config file is missing, unreadable, or invalid. The message lists
    every problem as `field: reason`."""


class FailOn(str, Enum):
    BLOCK = "block"          # fail on any BLOCK verdict (any requirement regression)
    CRITICAL = "critical"    # fail only on Critical drift
    NEVER = "never"          # report only (advisory mode)


class _Strict(BaseModel):
    # Unknown keys are errors: a typo like `threshold:` must not silently fall back to defaults.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Thresholds(_Strict):
    compliant: float = Field(0.55, ge=0.0, le=1.0, description="Best match >= this: requirement covered")
    partial: float = Field(0.40, ge=0.0, le=1.0, description="Best match >= this: partially covered")
    drift: float = Field(-0.10, ge=-1.0, lt=0.0, description="Matrix drift: delta <= this is drift")
    regression_strong: float = Field(-0.05, ge=-1.0, lt=0.0,
                                     description="PR gate drop that flags a strongly matched requirement")
    regression_weak: float = Field(-0.08, ge=-1.0, lt=0.0,
                                   description="PR gate drop that flags a weaker match")
    strong_relevance: float = Field(0.60, ge=0.0, le=1.0,
                                    description="score_v1 >= this uses regression_strong")
    relevance_floor: float = Field(0.35, ge=0.0, le=1.0,
                                   description="score_v1 below this is not considered related")
    link_top_k: int = Field(3, ge=0, le=50,
                            description="PR gate only checks pairs where the endpoint is one of the "
                                        "requirement's top-k matches in the base spec (0 = all pairs)")

    @model_validator(mode="after")
    def _ordering(self) -> "Thresholds":
        if self.partial >= self.compliant:
            raise ValueError(f"partial ({self.partial}) must be lower than compliant ({self.compliant})")
        if self.relevance_floor >= self.strong_relevance:
            raise ValueError(f"relevance_floor ({self.relevance_floor}) must be lower than "
                             f"strong_relevance ({self.strong_relevance})")
        if self.regression_weak > self.regression_strong:
            raise ValueError(f"regression_weak ({self.regression_weak}) must be a drop at least as large "
                             f"as regression_strong ({self.regression_strong})")
        return self


class SrsExtractionConfig(_Strict):
    id_pattern: Optional[str] = Field(None, description="Regex for one requirement ID; null = built-in")
    include_nfr: bool = True
    extra_blocklist: List[str] = Field(default_factory=list)
    diagram_mode: Optional[Literal["off", "local", "vision"]] = Field(
        None, description="How figures are read; null = $SRS_DIAGRAM_MODE, else local")
    max_figures: int = Field(40, ge=0, le=500)

    @field_validator("id_pattern")
    @classmethod
    def _valid_regex(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        try:
            compiled = re.compile(v)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from exc
        if compiled.match(""):
            raise ValueError("pattern matches the empty string")
        return v

    def to_options(self) -> ExtractionOptions:
        return ExtractionOptions(id_pattern=self.id_pattern, include_nfr=self.include_nfr,
                                 extra_blocklist=tuple(self.extra_blocklist),
                                 diagram_mode=self.diagram_mode, max_figures=self.max_figures)


class CodeConfig(_Strict):
    """`code:` block: review changed Python symbols as well as the OpenAPI spec (M5)."""

    paths: List[str] = Field(..., min_length=1, description="Glob(s) of the Python files to review")
    exclude: List[str] = Field(default_factory=list, description="Glob(s) to leave out, e.g. tests/**")
    model: str = Field("jinaai/jina-embeddings-v2-base-code",
                       description='Code embedding model, or "lexical" for the offline fallback')
    thresholds: Dict[str, float] = Field(default_factory=dict,
                                         description="Overrides of the code embedder's calibrated thresholds")

    @field_validator("paths", "exclude", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("thresholds")
    @classmethod
    def _known_keys(cls, v: Dict[str, float]) -> Dict[str, float]:
        unknown = sorted(set(v) - set(Thresholds.model_fields))
        if unknown:
            raise ValueError(f"unknown threshold(s) {unknown}; known: {sorted(Thresholds.model_fields)}")
        return v

    def thresholds_for(self, base: "Thresholds", calibrated: Mapping[str, float]) -> "Thresholds":
        """Spec thresholds, then the embedder's calibrated code values, then the user's overrides."""
        try:
            return Thresholds.model_validate({**base.model_dump(), **calibrated, **self.thresholds})
        except ValidationError as exc:
            raise ConfigError(_format_errors(exc, "code.thresholds")) from exc


class TraceabilityConfig(_Strict):
    version: int = 1
    srs: str = Field(..., description="Path to the SRS document (.docx, .pdf, .md, .txt)")
    openapi: List[str] = Field(..., min_length=1, description="OpenAPI spec path(s)")
    thresholds: Thresholds = Field(default_factory=Thresholds)
    fail_on: FailOn = FailOn.BLOCK
    srs_extraction: SrsExtractionConfig = Field(default_factory=SrsExtractionConfig)
    code: Optional[CodeConfig] = None

    _base_dir: Path = PrivateAttr(default_factory=Path.cwd)
    _source: str = PrivateAttr(default="")

    @field_validator("version")
    @classmethod
    def _known_version(cls, v: int) -> int:
        if v not in SUPPORTED_CONFIG_VERSIONS:
            raise ValueError(f"unsupported config version {v}; supported: {list(SUPPORTED_CONFIG_VERSIONS)}")
        return v

    @field_validator("srs")
    @classmethod
    def _srs_ext(cls, v: str) -> str:
        v = str(v).strip()
        if Path(v).suffix.lower() not in SRS_EXTENSIONS:
            raise ValueError(f"'{v}' must be one of {', '.join(SRS_EXTENSIONS)}")
        return v

    @field_validator("openapi", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        return [v] if isinstance(v, (str, Path)) else v

    @field_validator("openapi")
    @classmethod
    def _spec_ext(cls, v: List[str]) -> List[str]:
        out = []
        for item in v:
            item = str(item).strip()
            if Path(item).suffix.lower() not in SPEC_EXTENSIONS:
                raise ValueError(f"'{item}' must be one of {', '.join(SPEC_EXTENSIONS)}")
            out.append(item)
        if len(set(out)) != len(out):
            raise ValueError("lists the same spec more than once")
        return out

    # -- paths -----------------------------------------------------------------

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def source(self) -> str:
        return self._source

    def resolve(self, relative: Union[str, Path]) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self._base_dir / p)

    @property
    def srs_path(self) -> Path:
        return self.resolve(self.srs)

    @property
    def openapi_paths(self) -> List[Path]:
        return [self.resolve(p) for p in self.openapi]

    def missing_files(self) -> List[str]:
        """Configured files that do not exist locally. Not checked at load time:
        the GitHub App reads the config first and fetches files through the API."""
        return [str(p) for p in [self.srs_path, *self.openapi_paths] if not p.exists()]

    def extraction_options(self) -> ExtractionOptions:
        return self.srs_extraction.to_options()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _format_errors(exc: ValidationError, source: str) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "extra_forbidden":
            msg = "unknown key (check the spelling)"
        lines.append(f"  - {loc}: {msg}")
    return f"Invalid {source or CONFIG_FILENAME}:\n" + "\n".join(lines)


def _apply_env(data: Dict[str, Any], env: Mapping[str, str]) -> Dict[str, Any]:
    data = dict(data)
    thresholds = dict(data.get("thresholds") or {})
    legacy = env.get("DRIFT_THRESHOLD")
    if legacy not in (None, ""):
        thresholds["drift"] = legacy
    for name in Thresholds.model_fields:
        value = env.get(f"{ENV_PREFIX}{name.upper()}")
        if value not in (None, ""):
            thresholds[name] = value
    if thresholds:
        data["thresholds"] = thresholds
    fail_on = env.get(f"{ENV_PREFIX}FAIL_ON")
    if fail_on:
        data["fail_on"] = fail_on.strip().lower()
    return data


def config_from_dict(
    data: Mapping[str, Any],
    *,
    base_dir: Union[str, Path, None] = None,
    source: str = "",
    env: Optional[Mapping[str, str]] = None,
) -> TraceabilityConfig:
    """Validate an already-parsed mapping. `env=None` means no overrides; pass
    `os.environ` to honour TRACEABILITY_* variables."""
    if not isinstance(data, Mapping):
        raise ConfigError(f"{source or CONFIG_FILENAME}: top level must be a mapping, got {type(data).__name__}")
    payload = _apply_env(dict(data), env) if env is not None else dict(data)
    try:
        cfg = TraceabilityConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc, source)) from exc
    cfg._base_dir = Path(base_dir).resolve() if base_dir is not None else Path.cwd()
    cfg._source = source
    return cfg


def load_config_text(
    text: str,
    *,
    base_dir: Union[str, Path, None] = None,
    source: str = CONFIG_FILENAME,
    env: Optional[Mapping[str, str]] = None,
) -> TraceabilityConfig:
    """Parse YAML text (e.g. fetched from a repo through the GitHub API)."""
    try:
        data = yaml.safe_load(text) if text and text.strip() else None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: not valid YAML: {exc}") from exc
    if data is None:
        raise ConfigError(f"{source} is empty; it needs at least `srs:` and `openapi:`.")
    return config_from_dict(data, base_dir=base_dir, source=source, env=env)


def load_config(
    path: Union[str, Path],
    *,
    env: Optional[Mapping[str, str]] = os.environ,
) -> TraceabilityConfig:
    """Load and validate a .traceability.yml file. Environment overrides apply
    by default; pass `env=None` to ignore them."""
    p = Path(path)
    if p.is_dir():
        found = find_config(p, search_parents=False)
        if found is None:
            raise ConfigError(f"No {CONFIG_FILENAME} in {p}")
        p = found
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"Cannot read {p}: {exc}") from exc
    return load_config_text(text, base_dir=p.parent, source=str(p), env=env)


def find_config(start: Union[str, Path] = ".", *, search_parents: bool = True) -> Optional[Path]:
    """Nearest .traceability.yml from `start` upwards (stops at a git root)."""
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    while True:
        for name in (CONFIG_FILENAME, *CONFIG_ALT_FILENAMES):
            candidate = current / name
            if candidate.is_file():
                return candidate
        if not search_parents or (current / ".git").exists() or current.parent == current:
            return None
        current = current.parent


def default_config(srs: str, openapi: Union[str, List[str]], base_dir: Union[str, Path, None] = None
                   ) -> TraceabilityConfig:
    """A config built from CLI flags when no file exists (all thresholds default)."""
    return config_from_dict({"srs": srs, "openapi": openapi}, base_dir=base_dir, source="(command line)")
