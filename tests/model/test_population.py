import pandas as pd

from uticen_lite.model.population import ColumnMeta, Population


def _pop():
    df = pd.DataFrame({"entry_id": ["A", "B"], "amount": [10, 20]})
    cols = [
        ColumnMeta(original_name="entry_id", display_name="Entry", is_key=True),
        ColumnMeta(original_name="amount", display_name="Amount", data_type="number"),
    ]
    return Population(df=df, columns=cols, source_id="gl")


def test_size_and_key_columns():
    p = _pop()
    assert p.size == 2
    assert p.key_columns == ["entry_id"]


def test_key_for_row():
    p = _pop()
    assert p.key_for({"entry_id": "A", "amount": 10}) == "A"
