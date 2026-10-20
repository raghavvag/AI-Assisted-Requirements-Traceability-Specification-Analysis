"""
test_config.py — .traceability.yml loader (src/config.py)
"""

from pathlib import Path

import pytest

from src.config import (
    CONFIG_FILENAME,
    ConfigError,
    FailOn,
    TraceabilityConfig,
    config_from_dict,
    default_config,
    find_config,
    load_config,
    load_config_text,
)
from src.srs import ExtractionOptions

ROOT = Path(__file__).parent.parent

MINIMAL = "srs: docs/SRS.docx\nopenapi: openapi.yaml\n"


def _write(tmp_path: Path, text: str, name: str = CONFIG_FILENAME) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestLoading:
    def test_repo_config_is_valid_and_files_exist(self):
        cfg = load_config(ROOT / CONFIG_FILENAME, env=None)
        assert cfg.srs_path == ROOT / "data" / "requirements" / "ecommerce_srs.md"
        assert cfg.openapi_paths == [ROOT / "data" / "api_specs" / "store_v1.json"]
        assert cfg.missing_files() == []

    def test_minimal_uses_defaults(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL), env=None)
        t = cfg.thresholds
        assert (t.compliant, t.partial, t.drift) == (0.55, 0.40, -0.10)
        assert (t.regression_strong, t.regression_weak, t.strong_relevance, t.relevance_floor) == (-0.05, -0.08, 0.60, 0.35)
        assert cfg.fail_on is FailOn.BLOCK
        assert cfg.extraction_options() == ExtractionOptions()

    def test_paths_resolve_relative_to_config_file(self, tmp_path):
        sub = tmp_path / "repo"
        sub.mkdir()
        cfg = load_config(_write(sub, MINIMAL), env=None)
        assert cfg.srs_path == (sub / "docs" / "SRS.docx").resolve()
        assert cfg.base_dir == sub.resolve()

    def test_openapi_list(self, tmp_path):
        cfg = load_config(_write(tmp_path, "srs: a.md\nopenapi: [v1/api.yaml, v2/api.json]\n"), env=None)
        assert cfg.openapi == ["v1/api.yaml", "v2/api.json"]

    def test_full_file(self, tmp_path):
        cfg = load_config(_write(tmp_path, (
            "version: 1\nsrs: SRS.pdf\nopenapi: spec.yml\n"
            "thresholds: {compliant: 0.6, partial: 0.45, drift: -0.2, regression_strong: -0.04, regression_weak: -0.09}\n"
            "fail_on: critical\n"
            "srs_extraction: {id_pattern: 'CAP-\\d{3}', include_nfr: false, extra_blocklist: [Stakeholders]}\n"
        )), env=None)
        assert cfg.fail_on is FailOn.CRITICAL and cfg.thresholds.compliant == 0.6
        opts = cfg.extraction_options()
        assert opts.id_pattern == r"CAP-\d{3}" and not opts.include_nfr and opts.extra_blocklist == ("Stakeholders",)

    def test_directory_argument(self, tmp_path):
        _write(tmp_path, MINIMAL)
        assert load_config(tmp_path, env=None).srs == "docs/SRS.docx"

    def test_text_loader_for_github_api(self):
        cfg = load_config_text(MINIMAL, base_dir="/repo", source="github:org/repo@abc123:.traceability.yml")
        assert cfg.source.startswith("github:") and cfg.srs_path == Path("/repo").resolve() / "docs" / "SRS.docx"

    def test_default_config(self):
        cfg = default_config("srs.md", "api.json")
        assert isinstance(cfg, TraceabilityConfig) and cfg.openapi == ["api.json"]


class TestValidation:
    @pytest.mark.parametrize("text,needle", [
        ("openapi: a.yaml\n", "srs"),
        ("srs: a.md\n", "openapi"),
        ("srs: a.doc\nopenapi: a.yaml\n", "srs"),
        ("srs: a.md\nopenapi: a.xml\n", "openapi"),
        ("srs: a.md\nopenapi: []\n", "openapi"),
        ("srs: a.md\nopenapi: [a.yaml, a.yaml]\n", "more than once"),
        (MINIMAL + "thresholds: {compliant: 0.4, partial: 0.5}\n", "partial"),
        (MINIMAL + "thresholds: {compliant: 1.5}\n", "compliant"),
        (MINIMAL + "thresholds: {drift: 0.1}\n", "drift"),
        (MINIMAL + "thresholds: {regression_strong: -0.1, regression_weak: -0.05}\n", "regression_weak"),
        (MINIMAL + "thresholds: {strong_relevance: 0.3, relevance_floor: 0.4}\n", "relevance_floor"),
        (MINIMAL + "fail_on: sometimes\n", "fail_on"),
        (MINIMAL + "srs_extraction: {id_pattern: '(unclosed'}\n", "regular expression"),
        (MINIMAL + "srs_extraction: {id_pattern: '.*'}\n", "empty string"),
        (MINIMAL + "version: 2\n", "version"),
        (MINIMAL + "threshold: {compliant: 0.6}\n", "unknown key"),
        (MINIMAL + "thresholds: {complaint: 0.6}\n", "thresholds.complaint"),
    ])
    def test_rejected(self, tmp_path, text, needle):
        with pytest.raises(ConfigError) as exc:
            load_config(_write(tmp_path, text), env=None)
        assert needle in str(exc.value)

    def test_all_errors_reported_at_once(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_config(_write(tmp_path, "srs: a.doc\nopenapi: a.xml\nfail_on: x\n"), env=None)
        msg = str(exc.value)
        assert "srs" in msg and "openapi" in msg and "fail_on" in msg

    @pytest.mark.parametrize("text", ["", "   \n", "- just\n- a list\n", "srs: [unclosed\n"])
    def test_bad_yaml(self, tmp_path, text):
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, text), env=None)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yml")

    def test_config_error_is_value_error(self):
        assert issubclass(ConfigError, ValueError)


class TestEnvOverrides:
    def test_prefixed_overrides(self, tmp_path):
        env = {"TRACEABILITY_COMPLIANT": "0.7", "TRACEABILITY_REGRESSION_WEAK": "-0.12",
               "TRACEABILITY_FAIL_ON": "NEVER"}
        cfg = load_config(_write(tmp_path, MINIMAL), env=env)
        assert cfg.thresholds.compliant == 0.7 and cfg.thresholds.regression_weak == -0.12
        assert cfg.fail_on is FailOn.NEVER

    def test_legacy_drift_threshold(self, tmp_path):
        cfg = load_config(_write(tmp_path, MINIMAL), env={"DRIFT_THRESHOLD": "-0.2"})
        assert cfg.thresholds.drift == -0.2

    def test_env_beats_file_and_is_validated(self, tmp_path):
        text = MINIMAL + "thresholds: {compliant: 0.6}\n"
        assert load_config(_write(tmp_path, text), env={"TRACEABILITY_COMPLIANT": "0.65"}).thresholds.compliant == 0.65
        with pytest.raises(ConfigError, match="compliant"):
            load_config(_write(tmp_path, text), env={"TRACEABILITY_COMPLIANT": "abc"})

    def test_env_none_ignores_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACEABILITY_COMPLIANT", "0.9")
        assert load_config(_write(tmp_path, MINIMAL), env=None).thresholds.compliant == 0.55


class TestDiscovery:
    def test_finds_nearest_upwards(self, tmp_path):
        (tmp_path / ".git").mkdir()
        cfg = _write(tmp_path, MINIMAL)
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert find_config(deep) == cfg

    def test_stops_at_git_root(self, tmp_path):
        _write(tmp_path, MINIMAL)
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert find_config(repo) is None

    def test_alt_extension(self, tmp_path):
        p = _write(tmp_path, MINIMAL, ".traceability.yaml")
        assert find_config(tmp_path, search_parents=False) == p

    def test_config_from_dict_rejects_non_mapping(self):
        with pytest.raises(ConfigError):
            config_from_dict(["srs"])  # type: ignore[arg-type]
