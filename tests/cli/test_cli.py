"""Tests for the cflow CLI (validate subcommand — deprecated stub)."""

from __future__ import annotations

from pathlib import Path

import pytest

from controlflow_sdk.cli import main

# ---------------------------------------------------------------------------
# cflow validate (deprecated stub — returns 0)
# ---------------------------------------------------------------------------


class TestValidate:
    def test_validate_returns_0(self, tmp_path: Path) -> None:
        """validate is a deprecated stub — always exits 0."""
        rc = main(["validate", "."])
        assert rc == 0

    def test_validate_prints_deprecation_note(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """validate must print a deprecation note."""
        main(["validate", "."])
        combined = capsys.readouterr().err
        assert "deprecated" in combined.lower()


# ---------------------------------------------------------------------------
# No subcommand → usage (exit 2)
# ---------------------------------------------------------------------------


class TestNoSubcommand:
    def test_no_command_exits_2(self, tmp_path: Path) -> None:
        rc = main([])
        assert rc == 2


# ---------------------------------------------------------------------------
# Unknown subcommand (argparse exits the process; catch SystemExit)
# ---------------------------------------------------------------------------


class TestUnknownSubcommand:
    def test_unknown_subcommand_raises(self) -> None:
        with pytest.raises(SystemExit):
            main(["does-not-exist"])
