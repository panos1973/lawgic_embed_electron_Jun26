"""Azure DI output cache + retry (extract._di_markdown): deterministic, content-keyed,
free on re-embed. No network — _azure_layout_markdown is stubbed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                       # noqa: E402
import pipeline.extract as extract  # noqa: E402


def test_di_markdown_caches_by_content_and_skips_redundant_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DI_CACHE_DIR", str(tmp_path / "di_cache"))
    calls = {"n": 0}

    def fake(path, pages=None):
        calls["n"] += 1
        return f"# OCR {pages}"
    monkeypatch.setattr(extract, "_azure_layout_markdown", fake)

    a = extract._di_markdown("/x.pdf", "filehashA", pages=[5])
    assert calls["n"] == 1 and a == "# OCR [5]"
    b = extract._di_markdown("/x.pdf", "filehashA", pages=[5])     # same file+page -> cache hit
    assert calls["n"] == 1 and b == a                              # NO new DI call (free re-embed)
    extract._di_markdown("/x.pdf", "filehashA", pages=[6])         # different page -> miss
    assert calls["n"] == 2
    extract._di_markdown("/x.pdf", "filehashB", pages=[5])         # different file content -> miss
    assert calls["n"] == 3


def test_di_markdown_does_not_cache_empty_result(tmp_path, monkeypatch):
    # an empty DI result (a failed/blank page) must NOT be cached, so a later run retries
    monkeypatch.setattr(config, "DI_CACHE_DIR", str(tmp_path / "di_cache"))
    calls = {"n": 0}
    monkeypatch.setattr(extract, "_azure_layout_markdown",
                        lambda path, pages=None: calls.__setitem__("n", calls["n"] + 1) or "")
    extract._di_markdown("/x.pdf", "fk", pages=[1])
    extract._di_markdown("/x.pdf", "fk", pages=[1])
    assert calls["n"] == 2          # empty not cached -> re-attempted


def test_di_cache_disabled_when_dir_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DI_CACHE_DIR", "")
    calls = {"n": 0}
    monkeypatch.setattr(extract, "_azure_layout_markdown",
                        lambda path, pages=None: calls.__setitem__("n", calls["n"] + 1) or "x")
    extract._di_markdown("/x.pdf", "fk", pages=[1])
    extract._di_markdown("/x.pdf", "fk", pages=[1])
    assert calls["n"] == 2          # no cache configured -> always calls
