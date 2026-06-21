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


# ── subfolder grouping (one subfolder ingests fully before the next) ──
def _j(*parts):
    return os.path.join(*parts)


def test_group_by_subfolder_buckets_and_orders():
    # root-level PDF + two subfolders; groups come back in name order, root first.
    root = _j("data")
    pdfs = sorted([_j(root, "z_root.pdf"),
                   _j(root, "beta", "b1.pdf"),
                   _j(root, "alpha", "a1.pdf"),
                   _j(root, "alpha", "a2.pdf")])
    groups = cli._group_by_subfolder(root, pdfs)
    assert [label for label, _ in groups] == ["(root)", "alpha", "beta"]
    by = dict(groups)
    assert by["(root)"] == [_j(root, "z_root.pdf")]
    assert by["alpha"] == [_j(root, "alpha", "a1.pdf"), _j(root, "alpha", "a2.pdf")]


def test_group_by_subfolder_rolls_nested_into_top_level():
    # a PDF nested deeper (sub/2024/x.pdf) belongs to its top-level subfolder.
    root = _j("data")
    pdfs = [_j(root, "sub", "2024", "deep.pdf"), _j(root, "sub", "top.pdf")]
    groups = cli._group_by_subfolder(root, pdfs)
    assert [label for label, _ in groups] == ["sub"]
    assert len(groups[0][1]) == 2                     # both roll up to 'sub'


def test_group_by_subfolder_flat_folder_is_single_root_group():
    root = _j("data")
    pdfs = [_j(root, "a.pdf"), _j(root, "b.pdf")]
    groups = cli._group_by_subfolder(root, pdfs)
    assert len(groups) == 1 and groups[0][0] == "(root)"
    assert groups[0][1] == pdfs


def test_group_by_subfolder_empty():
    assert cli._group_by_subfolder(_j("data"), []) == []
