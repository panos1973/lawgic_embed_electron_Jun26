"""cli.py — command-line entry, with optional --json for the Electron shell.

    python cli.py ingest <folder> [--json]   # process all PDFs in folder
    python cli.py status [--json]            # show counts
    python cli.py review [--json]            # list docs needing human review
    python cli.py retry [--json]             # re-run pending/error docs

In --json mode, one JSON object is printed per line (stdout), flushed live, so a
parent process (Electron main) can stream progress.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
import time

# Force UTF-8 on stdout/stderr before anything writes. On Windows the frozen exe
# defaults to the legacy cp1252 ("charmap") codec, which cannot encode Greek
# (e.g. 'ν' ν) — emitting any Greek progress line then raises
# "'charmap' codec can't encode character". Passing PYTHONUTF8/PYTHONIOENCODING
# isn't reliable for a PyInstaller build whose streams are already created, so we
# reconfigure explicitly here.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import threading

import config
from state import State

JSON = False
_EMIT_LOCK = threading.Lock()   # serialize stdout writes across worker threads


def emit(obj: dict):
    if JSON:
        obj.setdefault("ts", time.strftime("%H:%M:%S"))   # event time for the UI log
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with _EMIT_LOCK:
            try:
                sys.stdout.write(line)
            except UnicodeEncodeError:
                # last-resort guard: never let a console-encoding issue kill the run
                sys.stdout.write(line.encode("utf-8", "replace").decode("utf-8", "replace"))
            sys.stdout.flush()


def _human(stage, msg):
    if not JSON:
        print(f"    [{stage}] {msg}")


def cmd_ingest(folder: str):
    import weaviate_io as wio
    import orchestrator
    from concurrent.futures import ThreadPoolExecutor, as_completed
    st = State(config.STATE_DB)
    client = wio.connect()
    try:
        pdfs = sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))
        # Oldest-first by filename keeps a stable order; true chronological ordering
        # is enforced later by the serial consolidation pass, which is order-safe.
        total = len(pdfs)
        emit({"type": "scan", "folder": folder, "total": total})
        if not JSON:
            print(f"Found {total} PDFs in {folder}")

        # PHASE 1 — parallel per-document ingest. Each law is processed start-to-
        # finish by ONE worker (its chunks never split across workers), so within
        # a law nothing is missed and identity/linkage is intact. Concurrency is
        # bounded; the rate limiter throttles the API fan-out. Single doc -> 1
        # worker (no thread overhead), preserving the simple serial path.
        workers = max(1, int(getattr(config, "CONCURRENCY", 4))) if total > 1 else 1
        done_n = [0]

        def _one(path):
            name = os.path.basename(path)
            emit({"type": "doc_start", "doc": name, "total": total})

            def progress(stage, msg, _n=name):
                _human(stage, msg)
                emit({"type": "stage", "doc": _n, "stage": stage, "msg": msg})
            try:
                status = orchestrator.process_document(client, st, path, progress)
            except Exception as e:                       # worker isolation
                status = "error"
                emit({"type": "stage", "doc": name, "stage": "error", "msg": str(e)})
            done_n[0] += 1
            emit({"type": "doc_done", "doc": name, "status": status,
                  "index": done_n[0], "total": total})
            return status

        if workers == 1:
            for path in pdfs:
                _one(path)
        else:
            emit({"type": "stage", "doc": "", "stage": "parallel",
                  "msg": f"processing {total} docs, {workers} workers"})
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_one, p) for p in pdfs]
                for _f in as_completed(futures):
                    pass                                 # progress already emitted

        # PHASE 2 — serial cross-law consolidation (order-sensitive: amendment
        # edges + version chains span laws and must NOT run concurrently). Safe,
        # idempotent, skips targets not yet ingested.
        try:
            res = wio.assemble_article_timeline(client)
            emit({"type": "stage", "doc": "", "stage": "consolidate",
                  "msg": f"articles={res.get('articles',0)} "
                         f"versions={res.get('versions_written',0)} "
                         f"pending={res.get('pending',0)}"})
        except Exception as e:                           # never fail the run on this
            emit({"type": "stage", "doc": "", "stage": "consolidate",
                  "msg": f"skipped: {e}"})

        counts = st.counts()
        emit({"type": "summary", "counts": counts})
        if not JSON:
            print("\nCounts:", counts)
    finally:
        client.close(); st.close()


def cmd_consolidate():
    """Build the versioned amendment timeline: append article versions from
    amendment edges whose target law is now ingested. Idempotent — safe to re-run."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        result = wio.assemble_article_timeline(client)
        emit({"type": "consolidate", **result})
        if not JSON:
            print(f"Timeline assembly: articles={result['articles']} "
                  f"versions_written={result['versions_written']} "
                  f"pending(target missing)={result['pending']}")
    finally:
        client.close()


def cmd_graph_status():
    """Amendment-graph completeness / QA report. Surfaces dangling targets —
    amendment edges whose target law is absent (missing base law or a bad
    resolution) — and undated edges. This is the extraction-accuracy lens."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        result = wio.graph_status(client)
        emit({"type": "graph-status", **result})
        if not JSON:
            print(f"Amendments: {result['amendments']}  "
                  f"target_law_present={result['target_law_present']}  "
                  f"target_law_missing={result['target_law_missing']}  "
                  f"undated={result['undated']}")
            if result["dangling_targets"]:
                print("Dangling targets (sample):")
                for t in result["dangling_targets"]:
                    print(f"  {t}")
    finally:
        client.close()


def cmd_status():
    st = State(config.STATE_DB)
    counts = st.counts()
    # APP_VERSION is injected by the Electron shell (from the release tag) so the
    # version is reported here too; falls back to "dev" when run standalone.
    version = os.environ.get("APP_VERSION", "dev")
    import logsetup
    logfile = logsetup.log_path()
    emit({"type": "status", "counts": counts, "version": version, "log": logfile})
    if not JSON:
        print(f"Lawgic FEK Ingest v{version}")
        print("Counts:", counts)
        print("Log file:", logfile)
    st.close()


def cmd_review():
    st = State(config.STATE_DB)
    rows = st.by_status("review")
    docs = [{"doc_id": r["doc_id"], "path": r["path"], "stage": r["stage"],
             "error": r["error"], "confidence": r["confidence"]} for r in rows]
    emit({"type": "review", "docs": docs})
    if not JSON:
        print(f"{len(docs)} documents need review")
        for d in docs:
            print(f"  {d['doc_id']:<40} stage={d['stage']}  {d['error'] or ''}")
    st.close()


def cmd_collections():
    """List the collections + their object counts (for the reset UI)."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        reg = wio.collection_registry()
        cols = []
        for key, name in reg.items():
            cols.append({"key": key, "name": name,
                         "count": wio.collection_count(client, name)})
        emit({"type": "collections", "collections": cols})
        if not JSON:
            for c in cols:
                shown = "absent" if c["count"] < 0 else c["count"]
                print(f"  {c['name']:<24} {shown}")
    finally:
        client.close()


def cmd_reset(target: str):
    """Reset (wipe objects, keep schema) one collection by key, or 'all'."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        reg = wio.collection_registry()
        if target == "all":
            keys = list(reg.keys())
        elif target in reg:
            keys = [target]
        else:
            emit({"type": "reset", "error": f"unknown collection '{target}'"})
            if not JSON:
                print(f"Unknown collection '{target}'. Known: {', '.join(reg)} | all")
            return
        results = [wio.reset_collection(client, reg[k]) for k in keys]
        emit({"type": "reset", "results": results})
        if not JSON:
            for r in results:
                print(f"  reset {r['collection']}: deleted {r.get('deleted', 0)}"
                      + (f" ({r['error']})" if r.get("error") else ""))
    finally:
        client.close()


def cmd_reset_state():
    """Clear local ingest history (dedup/resume) so docs re-process next run."""
    st = State(config.STATE_DB)
    try:
        n = st.reset()
        emit({"type": "reset_state", "cleared": n})
        if not JSON:
            print(f"Ingest history cleared: {n} record(s)")
    finally:
        st.close()


def cmd_retry():
    import weaviate_io as wio
    import orchestrator
    st = State(config.STATE_DB)
    client = wio.connect()
    try:
        rows = st.pending()
        emit({"type": "scan", "folder": "(retry)", "total": len(rows)})
        if not JSON:
            print(f"Retrying {len(rows)} documents")
        for i, r in enumerate(rows, 1):
            name = os.path.basename(r["path"])
            emit({"type": "doc_start", "doc": name, "index": i, "total": len(rows)})

            def progress(stage, msg, _n=name):
                _human(stage, msg)
                emit({"type": "stage", "doc": _n, "stage": stage, "msg": msg})

            status = orchestrator.process_document(client, st, r["path"], progress)
            emit({"type": "doc_done", "doc": name, "status": status})
        emit({"type": "summary", "counts": st.counts()})
        if not JSON:
            print("Counts:", st.counts())
    finally:
        client.close(); st.close()


def main():
    global JSON
    argv = sys.argv[1:]
    if "--json" in argv:                       # position-independent flag
        JSON = True
        argv = [a for a in argv if a != "--json"]
    ap = argparse.ArgumentParser(description="Lawgic FEK ingestion pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("folder")
    sub.add_parser("status")
    sub.add_parser("review")
    sub.add_parser("retry")
    sub.add_parser("consolidate")              # build versioned amendment timeline
    sub.add_parser("graph-status")             # amendment-graph QA / dangling report
    sub.add_parser("collections")              # list collections + counts
    sub.add_parser("reset-state")              # clear local ingest history
    rp = sub.add_parser("reset")               # wipe a collection (keep schema)
    rp.add_argument("target", help="collection key (flat|document|article|"
                                   "amendment|delegation) or 'all'")
    args = ap.parse_args(argv)
    import logsetup
    logsetup.init()                            # durable rotating file log
    {"ingest": lambda: cmd_ingest(args.folder), "status": cmd_status,
     "review": cmd_review, "retry": cmd_retry,
     "consolidate": cmd_consolidate,
     "graph-status": cmd_graph_status,
     "collections": cmd_collections,
     "reset-state": cmd_reset_state,
     "reset": lambda: cmd_reset(args.target)}[args.cmd]()


if __name__ == "__main__":
    main()
