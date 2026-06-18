"""cli.py — command-line entry, with optional --json for the Electron shell.

    python cli.py ingest <folder> [--json]   # process all PDFs in folder
    python cli.py status [--json]            # show counts
    python cli.py review [--json]            # list docs needing human review
    python cli.py retry [--json]             # re-run pending/error docs
    python cli.py history --law 4619/2019 --article 5   # one provision's timeline
    python cli.py graph-status [--json]      # amendment-graph QA / dangling report

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


def _json_default(o):
    """Make Weaviate values JSON-safe. DATE properties come back from the client as
    Python datetime objects (and the odd uuid/Decimal), which json.dumps cannot
    encode — that crashed the inspect/Browse path. Dates -> ISO string; anything
    else -> str (never let a stray type kill the JSON event stream)."""
    import datetime as _dt
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    return str(o)


def emit(obj: dict):
    if JSON:
        obj.setdefault("ts", time.strftime("%H:%M:%S"))   # event time for the UI log
        line = json.dumps(obj, ensure_ascii=False, default=_json_default) + "\n"
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


def _warn_if_no_llm_key():
    """Surface a missing LLM key up front: enrichment (summaries/keywords) is then
    skipped silently, which is easy to miss in a long run — so say it loudly."""
    keyname = config.PROVIDERS.get(config.LLM_PROVIDER, {}).get("key", "")
    if not getattr(config, keyname, ""):
        emit({"type": "stage", "doc": "", "stage": "warning",
              "msg": f"no {config.LLM_PROVIDER} key set — LLM summaries/keywords will be "
                     "empty (classification, title and embeddings still run)"})
        if not JSON:
            print(f"WARNING: no {config.LLM_PROVIDER} key — LLM enrichment disabled")


def cmd_ingest(folder: str):
    import weaviate_io as wio
    import orchestrator
    from concurrent.futures import ThreadPoolExecutor, as_completed
    st = State(config.STATE_DB)
    st.requeue_stale()      # stale 'processing' from an interrupted run -> resumable
    # Connecting is the first credential gate: a bad Weaviate URL/key fails here,
    # before any file — surface it as a clean PAUSE instead of a crash or churn.
    try:
        main_client = wio.connect()
    except (Exception, SystemExit) as e:                 # noqa: BLE001
        fe = orchestrator.as_fatal(e, "Weaviate", "connect")
        emit({"type": "fatal", "provider": fe.provider, "stage": fe.stage,
              "doc": "", "detail": fe.detail})
        emit({"type": "summary", "counts": st.counts(), "paused": True})
        if not JSON:
            print(f"\nPAUSED — {fe.provider} ({fe.stage}): {fe.detail}")
        st.close()
        return

    # Each POOL worker uses its OWN Weaviate client. The v4 client is not built for
    # concurrent batch writes sharing one connection across many threads, so at high
    # concurrency a shared client can throw; a per-thread client removes that risk.
    # The main thread (serial path + the Phase-2 consolidation) reuses the gate
    # client. Connect once per worker thread (reused across its documents); close all
    # at the end.
    _tls = threading.local()
    _extra_clients: list = []
    _extra_lock = threading.Lock()

    def worker_client():
        if threading.current_thread() is threading.main_thread():
            return main_client
        c = getattr(_tls, "client", None)
        if c is None:
            c = wio.connect()
            _tls.client = c
            with _extra_lock:
                _extra_clients.append(c)
        return c

    _warn_if_no_llm_key()
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
        stop = threading.Event()       # set on the first fatal -> no new files start
        fatal = {}                     # the first fatal's provider/stage/doc/detail

        def _one(path):
            if stop.is_set():
                return "skipped"
            name = os.path.basename(path)
            emit({"type": "doc_start", "doc": name, "total": total})

            def progress(stage, msg, _n=name):
                _human(stage, msg)
                emit({"type": "stage", "doc": _n, "stage": stage, "msg": msg})
            try:
                status = orchestrator.process_document(worker_client(), st, path, progress)
            except orchestrator.FatalIngestError as fe:
                # credential/endpoint failure: stop the whole run (first one wins).
                # process_document already released this doc to 'pending' for resume.
                if not stop.is_set():
                    stop.set()
                    fatal.update(provider=fe.provider, stage=fe.stage,
                                 doc=name, detail=fe.detail)
                return "fatal"
            except Exception as e:                       # per-document isolation
                status = "error"
                emit({"type": "stage", "doc": name, "stage": "error", "msg": str(e)})
            done_n[0] += 1
            emit({"type": "doc_done", "doc": name, "status": status,
                  "index": done_n[0], "total": total})
            return status

        if workers == 1:
            for path in pdfs:
                if stop.is_set():
                    break
                _one(path)
        else:
            emit({"type": "stage", "doc": "", "stage": "parallel",
                  "msg": f"processing {total} docs, {workers} workers"})
            pool = ThreadPoolExecutor(max_workers=workers)
            futures = [pool.submit(_one, p) for p in pdfs]
            for _f in as_completed(futures):
                if stop.is_set():
                    break                                # stop waiting on the rest
            # cancel not-yet-started files; let the few in-flight ones wind down
            pool.shutdown(wait=True, cancel_futures=True)

        if fatal:
            # Credential/endpoint failure — STOP (do not consolidate). The operator
            # fixes the key/URL in Settings, saves, and resumes by re-running ingest:
            # the state DB skips done files and retries the interrupted one.
            emit({"type": "fatal", **fatal})
            emit({"type": "summary", "counts": st.counts(), "paused": True})
            if not JSON:
                print(f"\nPAUSED — {fatal['provider']} ({fatal['stage']}): "
                      f"{fatal['detail']}\n  Fix the key/URL and re-run to resume.")
            return

        # PHASE 2 — serial cross-law consolidation (only when the batch finished
        # without a fatal stop). Order-sensitive; safe, idempotent.
        try:
            res = wio.assemble_article_timeline(main_client)
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
        for c in _extra_clients:                         # per-worker clients
            try:
                c.close()
            except Exception:                            # noqa: BLE001
                pass
        main_client.close()
        st.close()


def cmd_enrich(folder: str):
    """Backfill LLM enrichment (summaries/keywords) + document title onto laws
    ALREADY embedded from `folder`, WITHOUT re-embedding the vectors. Run this after
    a deterministic-only / no-LLM-key bulk embed to fill the enrichment cheaply."""
    import weaviate_io as wio
    import orchestrator
    try:
        client = wio.connect()
    except (Exception, SystemExit) as e:                 # noqa: BLE001
        fe = orchestrator.as_fatal(e, "Weaviate", "connect")
        emit({"type": "fatal", "provider": fe.provider, "stage": fe.stage,
              "doc": "", "detail": fe.detail})
        if not JSON:
            print(f"\nPAUSED — {fe.provider} ({fe.stage}): {fe.detail}")
        return
    _warn_if_no_llm_key()
    try:
        pdfs = sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))
        total = len(pdfs)
        emit({"type": "scan", "folder": folder, "total": total})
        if not JSON:
            print(f"Enriching {total} already-embedded document(s) from {folder}")
        done = 0
        for i, path in enumerate(pdfs, 1):
            name = os.path.basename(path)
            emit({"type": "doc_start", "doc": name, "index": i, "total": total})

            def progress(stage, msg, _n=name):
                _human(stage, msg)
                emit({"type": "stage", "doc": _n, "stage": stage, "msg": msg})

            try:
                res = orchestrator.enrich_document(client, path, progress)
            except orchestrator.FatalIngestError as fe:
                # bad LLM key / exhausted quota -> stop cleanly; fix and re-run.
                emit({"type": "fatal", "provider": fe.provider, "stage": fe.stage,
                      "doc": name, "detail": fe.detail})
                emit({"type": "summary", "counts": {"enriched": done}, "paused": True})
                if not JSON:
                    print(f"\nPAUSED — {fe.provider} ({fe.stage}): {fe.detail}")
                return
            except Exception as e:                       # noqa: BLE001 — isolate one bad doc
                emit({"type": "stage", "doc": name, "stage": "error", "msg": str(e)})
                continue
            done += 1
            emit({"type": "doc_done", "doc": name, "status": "done", "index": i,
                  "total": total, "chunks_updated": res.get("chunks_updated", 0)})
        emit({"type": "summary", "counts": {"enriched": done, "total": total}})
        if not JSON:
            print(f"Enriched {done}/{total} document(s)")
    finally:
        client.close()


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
                  f"undated={result['undated']}  "
                  f"unresolved={result.get('unresolved', 0)}")
            if result["dangling_targets"]:
                print("Dangling targets — target law absent (sample):")
                for t in result["dangling_targets"]:
                    print(f"  {t}")
            if result.get("unresolved_targets"):
                print("Unresolved references — not pinned to a canonical target (sample):")
                for t in result["unresolved_targets"]:
                    print(f"  {t}")
    finally:
        client.close()


def cmd_history(law: str, article: str):
    """Full timeline of one provision: the amendment edges that target it (who
    changed it, how, when) merged with its text versions (the wording at each
    stage). The chain-tracing / "how did article N change over time" view."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        h = wio.provision_history(client, law, article)
        emit({"type": "history", **h})
        if not JSON:
            line = (f"History of {law} άρθρο {article}: "
                    f"{h['version_count']} version(s), {h['edit_count']} amendment edge(s)")
            if h["unresolved_edits"]:
                line += f", {h['unresolved_edits']} unresolved"
            print(line)
            if h["edit_count"] and not h["target_present"]:
                print("  (no text versions — the target/base law is not ingested yet; "
                      "edges are shown below and resolve once it is)")
            if h["versions"]:
                print("  Text timeline:")
                for v in h["versions"]:
                    span = f"{v['valid_from'] or '?'} → {v['valid_to'] or 'current'}"
                    cur = " [current]" if v.get("is_current") else ""
                    print(f"    v{v.get('version', '?')}  {span}  "
                          f"{v.get('legal_force_status', '')}{cur}  ({v['chars']} chars)")
                    if v["text_preview"]:
                        print(f"        {v['text_preview']}")
            if h["edits"]:
                print("  Amendment edges (chronological):")
                for e in h["edits"]:
                    by = f"ν.{e.get('source_law_number') or '?'}"
                    if e.get("source_article_number"):
                        by += f" άρθρο {e['source_article_number']}"
                    flag = "" if e.get("resolved", True) else "  [UNRESOLVED]"
                    print(f"    {e.get('effective_date') or '????-??-??'}  "
                          f"{e.get('action', '?'):<12} by {by}{flag}")
    finally:
        client.close()


def cmd_diag():
    """Credential / connectivity self-check — confirms keys without a full embed."""
    import diag
    rep = diag.run_diagnostics()
    emit({"type": "diag", **rep})
    if not JSON:
        cfg = rep["configured"]
        print("Configured credentials (presence only):")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
        print("Connectivity:")
        for c in rep["checks"]:
            mark = "OK " if c["ok"] else "FAIL"
            print(f"  [{mark}] {c['service']}: {c['detail']}")
        print("ALL OK" if rep["ok"] else "Some checks FAILED — see above")


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
    st.requeue_stale()
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

            try:
                status = orchestrator.process_document(client, st, r["path"], progress)
            except orchestrator.FatalIngestError as fe:
                emit({"type": "fatal", "provider": fe.provider, "stage": fe.stage,
                      "doc": name, "detail": fe.detail})
                emit({"type": "summary", "counts": st.counts(), "paused": True})
                if not JSON:
                    print(f"\nPAUSED — {fe.provider} ({fe.stage}): {fe.detail}")
                return
            emit({"type": "doc_done", "doc": name, "status": status})
        emit({"type": "summary", "counts": st.counts()})
        if not JSON:
            print("Counts:", st.counts())
    finally:
        client.close(); st.close()


def cmd_laws():
    """List the laws embedded in the flat collection (Browse picker source)."""
    import weaviate_io as wio
    client = wio.connect()
    try:
        laws = wio.list_laws(client)
        emit({"type": "laws", "laws": laws})
        if not JSON:
            print(f"{len(laws)} law(s) embedded:")
            for r in laws:
                print(f"  {r['law_number']:<14} {r.get('instrument_key',''):<14} "
                      f"chunks={r['chunks']:<4} {r.get('document_title','')[:50]}")
    finally:
        client.close()


def cmd_inspect(collection: str, law: str):
    """Dump every object embedded for one law in one collection (Browse view)."""
    import weaviate_io as wio
    reg = wio.collection_registry()
    if collection not in reg:
        emit({"type": "inspect", "error": f"unknown collection '{collection}'"})
        if not JSON:
            print(f"Unknown collection '{collection}'. Known: {', '.join(reg)}")
        return
    client = wio.connect()
    try:
        objs = wio.fetch_law_objects(client, reg[collection], law)
        emit({"type": "inspect", "collection": collection, "law": law,
              "count": len(objs), "objects": objs})
        if not JSON:
            print(f"{collection} / {law}: {len(objs)} object(s)")
            for o in objs:
                print(f"  {o.get('canonical_id') or o.get('_uuid')}  "
                      f"art={o.get('article_number','')}  v={o.get('version','')}  "
                      f"current={o.get('is_current','')}")
    finally:
        client.close()


def main():
    global JSON
    argv = sys.argv[1:]
    if "--json" in argv:                       # position-independent flag
        JSON = True
        argv = [a for a in argv if a != "--json"]
    ap = argparse.ArgumentParser(description="Lawgic FEK ingestion pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("folder")
    ep = sub.add_parser("enrich"); ep.add_argument("folder")   # backfill enrichment, no re-embed
    sub.add_parser("status")
    sub.add_parser("diag")                     # credential/connectivity self-check
    sub.add_parser("review")
    sub.add_parser("retry")
    sub.add_parser("consolidate")              # build versioned amendment timeline
    sub.add_parser("graph-status")             # amendment-graph QA / dangling report
    hp = sub.add_parser("history")             # one provision's full timeline
    hp.add_argument("--law", required=True)
    hp.add_argument("--article", required=True)
    sub.add_parser("collections")              # list collections + counts
    sub.add_parser("laws")                     # list embedded laws (Browse picker)
    ip = sub.add_parser("inspect")             # dump one law's objects in a collection
    ip.add_argument("--collection", required=True)
    ip.add_argument("--law", required=True)
    sub.add_parser("reset-state")              # clear local ingest history
    rp = sub.add_parser("reset")               # wipe a collection (keep schema)
    rp.add_argument("target", help="collection key (flat|document|article|"
                                   "amendment|delegation) or 'all'")
    args = ap.parse_args(argv)
    import logsetup
    logsetup.init()                            # durable rotating file log
    import ratelimit                           # honor optional per-process RPM caps
    ratelimit.configure(voyage_rpm=config.VOYAGE_RPM,
                        llm_provider=config.LLM_PROVIDER, llm_rpm=config.LLM_RPM)
    {"ingest": lambda: cmd_ingest(args.folder), "status": cmd_status,
     "enrich": lambda: cmd_enrich(args.folder),
     "diag": cmd_diag,
     "review": cmd_review, "retry": cmd_retry,
     "consolidate": cmd_consolidate,
     "graph-status": cmd_graph_status,
     "history": lambda: cmd_history(args.law, args.article),
     "collections": cmd_collections,
     "laws": cmd_laws,
     "inspect": lambda: cmd_inspect(args.collection, args.law),
     "reset-state": cmd_reset_state,
     "reset": lambda: cmd_reset(args.target)}[args.cmd]()


if __name__ == "__main__":
    main()
