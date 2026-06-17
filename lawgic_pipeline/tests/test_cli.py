"""cli.py helpers — JSON safety for the event stream (Browse/inspect crashed on
Weaviate datetime values)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli  # noqa: E402


def test_json_default_serializes_dates():
    assert cli._json_default(datetime.datetime(2024, 3, 26, 0, 0, 0)) \
        == "2024-03-26T00:00:00"
    assert cli._json_default(datetime.date(2024, 3, 26)) == "2024-03-26"


def test_json_default_stringifies_unknown():
    class _X:
        def __str__(self):
            return "x-obj"
    assert cli._json_default(_X()) == "x-obj"


def test_emit_inspect_payload_with_datetime_is_serializable():
    import json
    payload = {"type": "inspect", "objects": [
        {"canonical_id": "ν.5090/2024#αρ.1",
         "publication_date": datetime.datetime(2024, 3, 26)}]}
    # the exact call emit() makes — must not raise TypeError
    line = json.dumps(payload, ensure_ascii=False, default=cli._json_default)
    assert "2024-03-26T00:00:00" in line
