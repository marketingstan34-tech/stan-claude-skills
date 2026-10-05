"""One-at-a-time background runner for cassation analyses started from the web UI."""

from __future__ import annotations

import calendar
import json
import os
import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
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
    redirect: str | None = None   # where the finished job's page leads (judge, filings, benchmark)
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
        if job.params.get("mode") == "filing":
            return self._run_filing(job)
        if job.params.get("mode") == "benchmark":
            return self._run_benchmark(job)
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
            from legal_ai.web.casedocs import case_docs
            storage = self.runs_dir.parent
            job.message = "Четене на документите по делото (сканираните се разчитат – до минута-две)…"
            folder = case_docs(storage, run_id)
            context += folder.texts()
            if folder.chronology():
                context.insert(0, ("Хронология на делото (документите по дата)", folder.chronology()))
            job.message = "Сваляне на първоинстанционното решение от сайта на съда…"
            context = self._first_instance(run) + context
            style, past = self._style_and_archive(storage, run, chosen)
            job.message = "AI пише жалбата (обстойна – обикновено 3–8 минути)…"
            appeal = generate(ai, run, text, chosen, context, style, archive_docs=past)
            write_atomic(d / "appeal.json", json.dumps(appeal, ensure_ascii=False, indent=1))
            job.run_dir = run_id
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)
        finally:
            if job.cleanup:
                job.cleanup()

    def _run_filing(self, job: Job) -> None:
        """Answer (чл. 287), appeal against the first-instance decision or private appeal (one AI call)."""
        from legal_ai.ai import OpenAIProvider, load_ai_config
        from legal_ai.cassation import casefile
        from legal_ai.cassation.appeal import generate
        from legal_ai.cassation.filings import FILINGS, instructions
        from legal_ai.web.casedocs import case_docs
        job.status = "running"
        run_id, kind = job.params["run_id"], job.params["kind"]
        spec = FILINGS[kind]
        try:
            d = self.runs_dir / run_id
            run = load_run(self.runs_dir, run_id)
            if run is None:
                raise ValueError("Няма такава справка.")
            storage = self.runs_dir.parent
            job.message = "Четене на документите по делото…"
            try:
                context = [(c["name"], c["text"]) for c in json.loads((d / "context.json").read_text(encoding="utf-8"))]
            except (OSError, ValueError, KeyError, TypeError):
                context = []
            folder = case_docs(storage, run_id)
            docs = folder.read_all()
            context += [(f"{x['kind']} – {x['filename']}", x["text"]) for x in docs]
            if spec["needs"] is None:
                main = (d / "appellate.txt").read_text(encoding="utf-8")
                context = self._first_instance(run) + context
            elif spec["needs"] == "първоинстанционно решение":
                own = [x for x in docs if x["kind"] == "първоинстанционно решение"]
                fetched = self._first_instance(run) if not own else []
                if not own and not fetched:
                    raise ValueError("Няма първоинстанционно решение – качете го в „Папка на делото“ на страницата на жалбата.")
                main = own[0]["text"] if own else fetched[0][1]
                context = [(n, t) for n, t in context if t != main]
                context.insert(0, ("Въззивно решение (по-късен акт по делото)", (d / "appellate.txt").read_text(encoding="utf-8")))
            else:
                rulings = sorted((x for x in docs if x["kind"] == "определение"), key=lambda x: x["date"] or "")
                if not rulings:
                    raise ValueError("Няма определение – качете обжалваното определение в „Папка на делото“.")
                main = rulings[-1]["text"]
                context = [(n, t) for n, t in context if t != main]
            if folder.chronology():
                context.insert(0, ("Хронология на делото (документите по дата)", folder.chronology()))
            chosen = casefile.load_case(d).get("questions") or casefile.default_questions(run)
            style, past = self._style_and_archive(storage, run, chosen)
            ai = OpenAIProvider(load_ai_config())
            job.cleanup = ai.close
            job.message = f"AI пише: {spec['title']} (обикновено 3–8 минути)…"
            result = generate(ai, run, main, chosen, context, style, instructions(kind), spec["decision_title"],
                              spec["stance"], past)
            write_atomic(d / f"filing-{kind}.json", json.dumps(result, ensure_ascii=False, indent=1))
            job.run_dir = run_id
            job.redirect = f"/runs/{run_id}/filing/{kind}"
            job.status = "done"
            job.message = "Готово."
        except Exception as exc:  # noqa: BLE001 - shown to the local user
            self._fail(job, exc)
        finally:
            if job.cleanup:
                job.cleanup()

    @staticmethod
    def _style_and_archive(storage, run: dict, chosen) -> tuple[list, list]:
        """Style samples (or, if none, the first two archived filings) and the passages of the archive
        that fit this case's questions and holdings."""
        from legal_ai.web.casedocs import archive, archive_excerpts, style_samples
        style = style_samples(storage).texts()
        past_all = archive(storage).texts()
        if not style:
            style = past_all[:2]
        a = run.get("analysis", {})
        topic = " ".join([q["text"] for q in a.get("questions", []) if not chosen or q["id"] in chosen]
                         + [h.get("summary", "") for h in a.get("holdings", [])])
        return style, archive_excerpts(past_all, topic)

    def _run_benchmark(self, job: Job) -> None:
        """Accuracy check on real VKS admission rulings (legal_ai/benchmark.py): one AI report per case."""
        from legal_ai.ai import OpenAIProvider, Usage, load_ai_config
        from legal_ai.benchmark import in_database, judge_questions, pick_cases, score, summary
        from legal_ai.cassation.pipeline import fetch_appellate, run_analysis
        from legal_ai.db import connect
        from legal_ai.http import PoliteClient
        from legal_ai.sources.courts import ALLOWED_HOSTS
        from legal_ai.sources.vks import HOST as VKS_HOST
        job.status = "running"
        out_dir = self.runs_dir.parent / "benchmark"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        result = {"created_at": datetime.now(timezone.utc).isoformat(), "cases": []}
        try:
            interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
            ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
            with connect(os.environ["DATABASE_URL"]) as conn:
                job.message = "Избор на дела от определенията на ВКС…"
                cases = pick_cases(conn, int(job.params.get("n", 3)))
                if not cases:
                    raise ValueError("В базата още няма подходящи определения по чл. 288. Опитайте след ден-два.")
                usage = Usage()
                with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as courts, \
                        PoliteClient([VKS_HOST], interval, ua) as vks:
                    for i, c in enumerate(cases, 1):
                        ruling_text = c.pop("ruling_text")
                        result["cases"].append(c)
                        job.message = f"Дело {i} от {len(cases)}: {c['court_name']}, дело {c['case']}/{c['year']}…"
                        # each case is one analysis with its own call cap (AI_MAX_CALLS_PER_RUN), as a report
                        ai = OpenAIProvider(load_ai_config())
                        job.cleanup = ai.close
                        try:
                            _, appellate = fetch_appellate(courts, c["court"], c["case"], c["year"])
                            cutoff = (appellate.act_date + timedelta(days=60)) if appellate.act_date else date.today()
                            run = asdict(run_analysis(ai, vks, appellate, cutoff, conn=conn))
                            c["appellate"] = appellate.label
                            c["cutoff"] = cutoff.isoformat()
                            c["score"] = sc = score(run, ruling_text, cutoff)
                            lost = [m["ref"] for m in sc["missed"] if m["why"] == "not_found"]
                            for ref, art in in_database(conn, lost).items():
                                for m in sc["missed"]:
                                    if m["ref"] == ref:
                                        if art is None:
                                            m["why"] = "not_in_db"
                                        elif art not in ("290", "ТР"):
                                            m["detail"] = "в базата е, но не е решение по чл. 290 – справката търси само такива"
                            if sc["pairs"] and sc["our_questions"]:
                                # one more call, outside the report's cap: same question by meaning?
                                job.message = f"Дело {i} от {len(cases)}: сравнение на въпросите по смисъл…"
                                cfg = load_ai_config()
                                judge_ai = OpenAIProvider(cfg)
                                try:
                                    judge_questions(judge_ai, cfg.stance_model or cfg.analysis_model,
                                                    cfg.stance_effort, sc)
                                except Exception as exc:  # noqa: BLE001 - words still count
                                    sc["judge_error"] = str(exc)[:200]
                                finally:
                                    for model, (inp, out) in judge_ai.usage.by_model.items():
                                        usage.add(model, inp, out)
                                    judge_ai.close()
                        except Exception as exc:  # noqa: BLE001 - one case must not stop the others
                            c["error"] = str(exc)[:300]
                        finally:
                            for model, (inp, out) in ai.usage.by_model.items():
                                usage.add(model, inp, out)
                            ai.close()
                            job.cleanup = None
                        result["summary"] = summary(result["cases"])
                        write_atomic(out_dir / f"{stamp}.json", json.dumps(result, ensure_ascii=False, indent=1))
            result["usage"] = {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                               "by_model": usage.by_model}
            write_atomic(out_dir / f"{stamp}.json", json.dumps(result, ensure_ascii=False, indent=1))
            job.status = "done"
            job.message = "Готово."
            job.redirect = "/benchmark"
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
            job.redirect = f"/runs/{run_id}/judge"
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
