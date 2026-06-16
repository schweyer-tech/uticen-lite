"""Test that contract/bundle.schema.json is byte-identical to the packaged schema.

This test is the CI gate that prevents the exported contract from silently drifting
away from the canonical schema shipped inside the package.  If it fails, run:

    python scripts/export_contract.py

to regenerate the contract file.
"""

import importlib.resources
from pathlib import Path

# Repo root is two levels up from this file (tests/test_contract_export.py → repo root)
_REPO_ROOT = Path(__file__).parent.parent
_CONTRACT_PATH = _REPO_ROOT / "contract" / "bundle.schema.json"


def test_contract_is_byte_identical_to_packaged_schema() -> None:
    """contract/bundle.schema.json must be byte-identical to the packaged schema."""
    # Read the canonical packaged bytes via importlib.resources (works after install too)
    pkg_bytes = (
        importlib.resources.files("controlflow_sdk.schema")
        .joinpath("bundle.schema.json")
        .read_bytes()
    )

    assert _CONTRACT_PATH.exists(), (
        f"{_CONTRACT_PATH} does not exist. Run `python scripts/export_contract.py` to generate it."
    )

    contract_bytes = _CONTRACT_PATH.read_bytes()

    assert contract_bytes == pkg_bytes, (
        "contract/bundle.schema.json has drifted from the packaged schema. "
        "Run `python scripts/export_contract.py` to regenerate it."
    )
