"""One-at-a-time background runner for cassation analyses started from the web UI."""

from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Job:
    id: str
    params: dict
    status: str = "queued"       # queued | running | done | failed
    message: str = ""
    run_dir: str | None = None
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class JobRunner:
    def __init__(self, runs_dir: Path, traces_dir: Path | None = None) -> None:
        self.runs_dir = runs_dir
        self.traces_dir = traces_dir or runs_dir.parent / "traces"
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self.jobs.values())

    def start(self, params: dict) -> Job:
        job = Job(uuid.uuid4().hex[:12], params)
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        if job.params.get("mode") == "noai":
            return self._run_noai(job)
        from legal_ai.ai import OpenAIProvider, load_ai_config
        from legal_ai.cassation.pipeline import fetch_appellate, run_analysis, save_run
        from legal_ai.http import PoliteClient
        from legal_ai.sources.courts import ALLOWED_HOSTS
        from legal_ai.sources.vks import HOST as VKS_HOST

        job.status = "running"
        p = job.params
        try:
            interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
            ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
            ai = OpenAIProvider(load_ai_config())
            with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as courts, \
                    PoliteClient([VKS_HOST], interval, ua) as vks:
                job.message = "Сваляне на въззивното решение…"
                _, appellate = fetch_appellate(courts, p["court"], p["case"], p["year"], p["type"])
                cutoff = (appellate.act_date + timedelta(days=60)) if appellate.act_date else date.today()
                if p.get("until"):
                    y, m = (int(x) for x in p["until"].split("-"))
                    cutoff = date(y, m, 28)
                job.message = f"Анализ на „{appellate.label}“ и търсене във ВКС…"
                conn = None
                if os.environ.get("DATABASE_URL"):
                    from legal_ai.db import connect
                    conn = connect(os.environ["DATABASE_URL"])
                try:
                    result = run_analysis(ai, vks, appellate, cutoff, conn=conn)
                finally:
                    if conn is not None:
                        conn.close()
                from legal_ai.tracing import as_dicts, trace
                job.message = "Проследяване на делото по инстанции…"
                result.path = as_dicts(trace(courts, vks, p["court"], p["case"], p["year"],
                                             appellate.act_date, result.analysis.get("lower_instance")))
            ai.close()
            job.run_dir = save_run(result, self.runs_dir).name
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)

    def _fail(self, job: Job, exc: Exception) -> None:
        job.status = "failed"
        job.message = f"{type(exc).__name__}: {exc}"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with open(self.runs_dir / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"{job.started} {json.dumps(job.params, ensure_ascii=False)}\n{traceback.format_exc()}\n")

    def _run_noai(self, job: Job) -> None:
        """Case path, the appealed act and cited VKS practice, without any AI call."""
        from legal_ai.cassation.noai import (as_dicts as cites_dicts, extract_appealed,
                                             extract_vks_citations, match_citations)
        from legal_ai.cassation.pipeline import fetch_appellate
        from legal_ai.http import PoliteClient
        from legal_ai.sources.courts import ALLOWED_HOSTS
        from legal_ai.sources.vks import HOST as VKS_HOST
        from legal_ai.tracing import as_dicts, trace

        job.status = "running"
        p = job.params
        try:
            interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
            ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
            with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as courts, \
                    PoliteClient([VKS_HOST], interval, ua) as vks:
                job.message = "Сваляне на въззивното решение…"
                _, appellate = fetch_appellate(courts, p["court"], p["case"], p["year"], p["type"])
                lower = extract_appealed(appellate.text)
                job.message = "Проследяване на делото по инстанции…"
                path = as_dicts(trace(courts, vks, p["court"], p["case"], p["year"],
                                      appellate.act_date, lower))
            cites = extract_vks_citations(appellate.text)
            if os.environ.get("DATABASE_URL"):
                from legal_ai.db import connect
                with connect(os.environ["DATABASE_URL"]) as conn:
                    match_citations(conn, cites)
            created = datetime.now(timezone.utc).isoformat()
            d = self.traces_dir / re.sub(r"[^0-9]", "", created)[:14]
            d.mkdir(parents=True, exist_ok=True)
            (d / "appellate.txt").write_text(appellate.text, encoding="utf-8")
            (d / "trace.json").write_text(json.dumps({
                "created_at": created, "params": p,
                "appellate": {"label": appellate.label, "url": appellate.url, "fmt": appellate.fmt,
                              "act_date": appellate.act_date, "retrieved_at": appellate.retrieved_at,
                              "warnings": appellate.warnings, "text_chars": len(appellate.text)},
                "lower_instance": lower, "path": path, "citations": cites_dicts(cites),
            }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            job.run_dir = d.name
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)


def list_runs(runs_dir: Path) -> list[dict]:
    out = []
    if not runs_dir.exists():
        return out
    for d in sorted(runs_dir.iterdir(), reverse=True):
        f = d / "run.json"
        if d.is_dir() and f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d.name, "label": data["appellate"]["label"], "created_at": data["created_at"],
                        "questions": len(data["analysis"]["questions"]),
                        "contra": sum(1 for a in data["assessments"]
                                      if a["relevant"] and a["stance"] == "противоречи")})
    return out


def load_run(runs_dir: Path, run_id: str) -> dict | None:
    if not run_id.isdigit():
        return None
    f = runs_dir / run_id / "run.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def list_traces(traces_dir: Path) -> list[dict]:
    out = []
    if not traces_dir.exists():
        return out
    for d in sorted(traces_dir.iterdir(), reverse=True):
        f = d / "trace.json"
        if d.is_dir() and f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d.name, "label": data["appellate"]["label"], "created_at": data["created_at"],
                        "citations": len(data["citations"]),
                        "in_corpus": sum(1 for c in data["citations"] if c["decision_id"])})
    return out


def load_trace(traces_dir: Path, trace_id: str) -> dict | None:
    if not trace_id.isdigit():
        return None
    f = traces_dir / trace_id / "trace.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    text = traces_dir / trace_id / "appellate.txt"
    data["text"] = text.read_text(encoding="utf-8") if text.exists() else ""
    return data
