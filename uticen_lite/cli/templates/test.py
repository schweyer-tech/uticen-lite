"""Test script for the SLUG control.

The ``test`` function receives a population object and must return a list of
violation dicts (empty list = all items passed).

Each violation should contain at minimum:
    item_key   (str) – unique row identifier
    description (str) – why this item is a violation
    severity   (str, optional) – "low" | "medium" | "high" | "critical"
    details    (dict, optional) – additional context
"""


def test(pop):  # noqa: ANN001, ANN201
    """Run the SLUG control test against *pop* and return violations."""
    return []
