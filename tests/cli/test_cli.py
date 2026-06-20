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


# ---------------------------------------------------------------------------
# cflow --version (argparse "version" action prints to stdout and exits 0)
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_exits_0(self, capsys: pytest.CaptureFixture) -> None:
        """--version is recognized and exits cleanly with code 0."""
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_version_prints_installed_version(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """--version prints the prog name and the installed distribution version."""
        from importlib.metadata import version

        expected = version("controlflow-sdk")
        with pytest.raises(SystemExit):
            main(["--version"])
        out = capsys.readouterr().out
        assert "cflow" in out
        assert expected in out

    def test_version_helper_falls_back_gracefully(self) -> None:
        """_version returns 'unknown' when the distribution metadata is missing."""
        from importlib.metadata import PackageNotFoundError
        from unittest.mock import patch

        from controlflow_sdk.cli import _version

        with patch(
            "importlib.metadata.version", side_effect=PackageNotFoundError("x")
        ):
            assert _version() == "unknown"
