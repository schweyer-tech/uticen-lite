"""Tests for uticen_lite.project.loader — TDD (write first, implement second)."""

from __future__ import annotations

from pathlib import Path

import pytest

from uticen_lite.model.control import SourceBinding
from uticen_lite.project import ProjectConfig, ProjectError, load_project_config, load_sources

SAMPLE = Path(__file__).parent / "fixtures" / "sample"


class TestLoadProjectConfig:
    def test_returns_project_config(self):
        cfg = load_project_config(SAMPLE)
        assert isinstance(cfg, ProjectConfig)

    def test_name(self):
        cfg = load_project_config(SAMPLE)
        assert cfg.name == "Sample Audit Project"

    def test_framework(self):
        cfg = load_project_config(SAMPLE)
        assert cfg.framework == "NIST SP 800-53"

    def test_system_dict(self):
        cfg = load_project_config(SAMPLE)
        assert cfg.system["name"] == "General Ledger System"

    def test_defaults_dict(self):
        cfg = load_project_config(SAMPLE)
        assert cfg.defaults["severity"] == "medium"

    def test_missing_cflow_yaml_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_project_config(tmp_path)

    def test_framework_optional(self, tmp_path):
        """cflow.yaml without a framework key yields framework=None."""
        (tmp_path / "cflow.yaml").write_text("name: Minimal\nsystem: {}\ndefaults: {}\n")
        cfg = load_project_config(tmp_path)
        assert cfg.framework is None


class TestLoadSources:
    def test_returns_dict_by_id(self):
        sources = load_sources(SAMPLE)
        assert "gl" in sources

    def test_returns_source_binding(self):
        sources = load_sources(SAMPLE)
        assert isinstance(sources["gl"], SourceBinding)

    def test_source_type(self):
        sources = load_sources(SAMPLE)
        assert sources["gl"].type == "file"

    def test_key_config_mode(self):
        sources = load_sources(SAMPLE)
        assert sources["gl"].key_config["mode"] == "single"

    def test_column_mappings_count(self):
        sources = load_sources(SAMPLE)
        assert len(sources["gl"].column_mappings) == 3

    def test_column_mapping_key_field(self):
        sources = load_sources(SAMPLE)
        key_cols = [cm for cm in sources["gl"].column_mappings if cm.get("is_key")]
        assert len(key_cols) == 1
        assert key_cols[0]["original_name"] == "entry_id"

    def test_description_and_ca_default_to_none(self):
        """A source with no description/completeness_accuracy parses to None."""
        sources = load_sources(SAMPLE)
        assert sources["gl"].description is None
        assert sources["gl"].completeness_accuracy is None

    def test_description_and_ca_parsed_when_present(self, tmp_path):
        """Optional description + completeness_accuracy are parsed onto the binding."""
        (tmp_path / "sources.yaml").write_text(
            "sources:\n"
            "  - id: gl\n"
            "    type: file\n"
            "    description: General-ledger journal entries.\n"
            "    completeness_accuracy: Reconciled to the trial-balance posting count.\n"
            "    config:\n"
            "      path: gl.csv\n"
            "      format: csv\n"
            "    key_config:\n"
            "      mode: single\n"
            "      columns:\n"
            "        - entry_id\n"
            "    column_mappings:\n"
            "      - original_name: entry_id\n"
            "        display_name: Entry ID\n"
            "        is_key: true\n"
            "        include: true\n"
        )
        sources = load_sources(tmp_path)
        assert sources["gl"].description == "General-ledger journal entries."
        assert (
            sources["gl"].completeness_accuracy == "Reconciled to the trial-balance posting count."
        )

    def test_missing_sources_yaml_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_sources(tmp_path)

    def test_invalid_sources_raises_project_error(self, tmp_path):
        """A sources.yaml that fails schema validation raises ProjectError."""
        bad = tmp_path / "sources.yaml"
        bad.write_text("sources:\n  - id: bad\n    type: file\n")
        with pytest.raises(ProjectError) as exc_info:
            load_sources(tmp_path)
        # Error should mention the offending path
        assert "config" in str(exc_info.value) or "key_config" in str(exc_info.value)

    def test_project_error_aggregates_messages(self, tmp_path):
        """ProjectError message contains at least one schema error string."""
        bad = tmp_path / "sources.yaml"
        bad.write_text("sources:\n  - id: bad\n    type: file\n")
        with pytest.raises(ProjectError) as exc_info:
            load_sources(tmp_path)
        assert len(str(exc_info.value)) > 0
