"""One-at-a-time background runner for cassation analyses started from the web UI."""

from __future__ import annotations

import calendar
import json
import os
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    id: str
    params: dict
    status: str = "queued"       # queued | running | done | failed
    message: str = ""
    run_dir: str | None = None
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cleanup: Callable[[], None] | None = field(default=None, repr=False)


class JobRunner:
    def __init__(self, runs_dir: Path, traces_dir: Path | None = None) -> None:
        self.runs_dir = runs_dir
        self.traces_dir = traces_dir or runs_dir.parent / "traces"
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self.jobs.values())

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs.values() if j.status in ("queued", "running")), None)

    def start(self, params: dict) -> Job | None:
        """Start a job unless one is already running (one at a time, so the court sites see one client)."""
        with self._lock:
            if any(j.status in ("queued", "running") for j in self.jobs.values()):
                return None
            job = Job(uuid.uuid4().hex[:12], params)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        if job.params.get("mode") == "noai":
            return self._run_noai(job)
        if job.params.get("mode") == "appeal":
            return self._run_appeal(job)
        if job.params.get("mode") == "judge":
            return self._run_judge(job)
        from legal_ai.ai import OpenAIProvider, load_ai_config
        from legal_ai.cassation.pipeline import run_analysis, save_run
        from legal_ai.http import PoliteClient
        from legal_ai.sources.courts import ALLOWED_HOSTS
        from legal_ai.sources.vks import HOST as VKS_HOST

        job.status = "running"
        p = job.params
        try:
            interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
            ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
            ai = OpenAIProvider(load_ai_config())
            job.cleanup = ai.close
            with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as courts, \
                    PoliteClient([VKS_HOST], interval, ua) as vks:
                job.message = "Четене на документа…" if p.get("file") else "Сваляне на въззивното решение…"
                appellate = self._appellate(courts, p)
                self._identify(p, appellate.text)
                self._standard_label(appellate)
                cutoff = (appellate.act_date + timedelta(days=60)) if appellate.act_date else date.today()
                if p.get("until"):   # practice up to the end of that month
                    y, m = (int(x) for x in p["until"].split("-"))
                    cutoff = date(y, m, calendar.monthrange(y, m)[1])
                job.message = f"Анализ на „{appellate.label}“ и търсене във ВКС…"
                conn = None
                if os.environ.get("DATABASE_URL"):
                    from legal_ai.db import connect
                    conn = connect(os.environ["DATABASE_URL"])
                try:
                    result = run_analysis(ai, vks, appellate, cutoff, conn=conn, notes=p.get("notes", ""),
                                          context_docs=self._extras(p))
                finally:
                    if conn is not None:
                        conn.close()
                from legal_ai.tracing import as_dicts, trace
                if self._can_trace(p):
                    job.message = "Проследяване на делото по инстанции…"
                    result.path = as_dicts(trace(courts, vks, p["court"], p["case"], p["year"],
                                                 appellate.act_date, result.analysis.get("lower_instance"),
                                                 kind=self._kind(p)))
            run_dir = save_run(result, self.runs_dir)
            if p.get("extras"):   # kept for the appeal draft, next to the report (private storage)
                write_atomic(run_dir / "context.json", json.dumps(
                    [{"name": n, "text": t} for n, t in self._extras(p)], ensure_ascii=False))
            job.run_dir = run_dir.name
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)
        finally:
            if job.cleanup:
                job.cleanup()

    def _run_appeal(self, job: Job) -> None:
        """Draft of the cassation appeal for a saved AI report (one AI call)."""
        from legal_ai.ai import OpenAIProvider, load_ai_config
        from legal_ai.cassation import casefile
        from legal_ai.cassation.appeal import generate
        job.status = "running"
        run_id = job.params["run_id"]
        try:
            d = self.runs_dir / run_id
            run = load_run(self.runs_dir, run_id)
            if run is None:
                raise ValueError("Няма такава справка.")
            text = (d / "appellate.txt").read_text(encoding="utf-8")
            case = casefile.load_case(d)
            chosen = case.get("questions") or casefile.default_questions(run)
            ai = OpenAIProvider(load_ai_config())
            job.cleanup = ai.close
            try:
                context = [(c["name"], c["text"]) for c in json.loads((d / "context.json").read_text(encoding="utf-8"))]
            except (OSError, ValueError, KeyError, TypeError):
                context = []
            from legal_ai.web.casedocs import case_docs, style_samples
            storage = self.runs_dir.parent
            job.message = "Четене на документите по делото (сканираните се разчитат – до минута-две)…"
            folder = case_docs(storage, run_id)
            context += folder.texts()
            if folder.chronology():
                context.insert(0, ("Хронология на делото (документите по дата)", folder.chronology()))
            job.message = "Сваляне на първоинстанционното решение от сайта на съда…"
            context = self._first_instance(run) + context
            style = style_samples(storage).texts()
            job.message = "AI пише жалбата (обстойна – обикновено 3–8 минути)…"
            appeal = generate(ai, run, text, chosen, context, style)
            write_atomic(d / "appeal.json", json.dumps(appeal, ensure_ascii=False, indent=1))
            job.run_dir = run_id
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)
        finally:
            if job.cleanup:
                job.cleanup()

    def _run_judge(self, job: Job) -> None:
        """„Съдия от ВКС": one AI call reviews the statement and the appeal (texts given by the page)."""
        from legal_ai.ai import OpenAIProvider, load_ai_config
        from legal_ai.cassation.judge import review
        job.status = "running"
        run_id = job.params["run_id"]
        try:
            d = self.runs_dir / run_id
            run = load_run(self.runs_dir, run_id)
            if run is None:
                raise ValueError("Няма такава справка.")
            text = (d / "appellate.txt").read_text(encoding="utf-8")
            ai = OpenAIProvider(load_ai_config())
            job.cleanup = ai.close
            job.message = "„Съдия от ВКС“ чете изложението и жалбата (обикновено 1–3 минути)…"
            result = review(ai, run, text, job.params.get("statement", ""), job.params.get("appeal", ""),
                            job.params.get("admission", ""))
            write_atomic(d / "judge.json", json.dumps(result, ensure_ascii=False, indent=1))
            job.run_dir = run_id
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)
        finally:
            if job.cleanup:
                job.cleanup()

    @staticmethod
    def _first_instance(run: dict) -> list[tuple[str, str]]:
        """The first-instance decision from the court's site, when the case path links to it."""
        from urllib.parse import urlparse

        from legal_ai.http import PoliteClient
        from legal_ai.sources.courts import ALLOWED_HOSTS
        from legal_ai.sources.courts.document import extract_text
        for inst in run.get("path") or []:
            if inst.get("level") != "първа":
                continue
            for act in inst.get("acts", []):
                url = act.get("url") or ""
                if act.get("type") != "Решение" or urlparse(url).hostname not in ALLOWED_HOSTS:
                    continue
                try:
                    interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
                    ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
                    with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as c:
                        f = c.get(url)
                    text = extract_text(f.body, f.content_type).text
                except Exception:   # noqa: BLE001 - the appeal is written without it
                    return []
                if len(text) < 300:
                    return []
                return [(f"Първоинстанционно решение – {inst.get('court', '')}, дело {inst.get('case', '')}, "
                         f"{act.get('date', '')} (сайт на съда)", text)]
        return []

    def _extras(self, p: dict) -> list[tuple[str, str]]:
        """The other case documents uploaded with the decision: (file name, text)."""
        from legal_ai.upload import read_stored
        out = []
        for e in p.get("extras", []):
            out.append((e["filename"], read_stored(self.runs_dir.parent / e["file"], e["filename"])[0]))
        return out

    def _appellate(self, courts, p: dict):
        """The appellate decision: an uploaded document, or downloaded from the court's site."""
        from legal_ai.cassation.pipeline import SourceDoc, fetch_appellate
        if p.get("file"):
            from legal_ai.upload import first_date, read_stored
            text, fmt, warnings = read_stored(self.runs_dir.parent / p["file"], p["filename"])
            label = "Поставен текст" if p.get("pasted") else f"Качен документ: {p['filename']}"
            return SourceDoc(label, "", text, fmt,
                             datetime.now(timezone.utc).isoformat(), first_date(text), warnings)
        return fetch_appellate(courts, p["court"], p["case"], p["year"], p["type"])[1]

    @staticmethod
    def _identify(p: dict, text: str) -> None:
        """For a document without case data: court, number and year from its heading, so the
        case path can be traced. Only a court on the verified list is used; the court-site lookup
        then confirms the case."""
        if not p.get("file") or (p.get("court") and p.get("case") and p.get("year")):
            return
        from legal_ai.cassation.noai import extract_case_header
        from legal_ai.sources.courts import find_court
        head = extract_case_header(text)
        if not head:
            p["identified"] = {"note": "Съдът и делото не се разчитат от началото на документа."}
            return
        from legal_ai.tracing import find_named_court
        site = find_court(head["court"]) or find_named_court(head["court"])
        p["identified"] = {**head, "supported": bool(site)}
        if site:
            p.update(court=site.key, case=head["number"], year=head["year"])

    @staticmethod
    def _standard_label(appellate) -> None:
        """A document gets the label read from its heading (court, case, date), not the file name."""
        from legal_ai.cassation.labels import GENERIC, standard_label
        if appellate.label.startswith(GENERIC):
            appellate.label = standard_label(appellate.text) or appellate.label

    @staticmethod
    def _kind(p: dict) -> str:
        return (p.get("identified") or {}).get("kind") or p.get("type") or ""

    @staticmethod
    def _can_trace(p: dict) -> bool:
        return bool(p.get("court") and p.get("case") and p.get("year"))

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
                job.message = "Четене на документа…" if p.get("file") else "Сваляне на въззивното решение…"
                appellate = self._appellate(courts, p)
                self._identify(p, appellate.text)
                self._standard_label(appellate)
                lower = extract_appealed(appellate.text)
                path = []
                if self._can_trace(p):
                    job.message = "Проследяване на делото по инстанции…"
                    path = as_dicts(trace(courts, vks, p["court"], p["case"], p["year"],
                                          appellate.act_date, lower, kind=self._kind(p)))
            cites = extract_vks_citations(appellate.text)
            if os.environ.get("DATABASE_URL"):
                from legal_ai.db import connect
                with connect(os.environ["DATABASE_URL"]) as conn:
                    match_citations(conn, cites)
            created = datetime.now(timezone.utc).isoformat()
            from legal_ai.cassation.pipeline import unique_dir
            d = unique_dir(self.traces_dir, created)
            (d / "appellate.txt").write_text(appellate.text, encoding="utf-8")
            write_atomic(d / "trace.json", json.dumps({
                "created_at": created, "params": p,
                "appellate": {"label": appellate.label, "url": appellate.url, "fmt": appellate.fmt,
                              "act_date": appellate.act_date, "retrieved_at": appellate.retrieved_at,
                              "warnings": appellate.warnings, "text_chars": len(appellate.text)},
                "lower_instance": lower, "path": path, "citations": cites_dicts(cites), "notes": p.get("notes", ""),
                "context_names": [e["filename"] for e in p.get("extras", [])],
            }, ensure_ascii=False, indent=2, default=str))
            job.run_dir = d.name
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)


def load_run(runs_dir: Path, run_id: str) -> dict | None:
    if not run_id.isdigit():
        return None
    f = runs_dir / run_id / "run.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


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


def write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so readers never see a half-written report."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
