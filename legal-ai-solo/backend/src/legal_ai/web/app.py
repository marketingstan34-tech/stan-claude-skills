"""Local web UI. The only mutation (starting an analysis) checks Origin against the Host."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.middleware.trustedhost import TrustedHostMiddleware

from legal_ai.config import load_settings
from legal_ai.db import connect
from legal_ai.retrieval.lexical import search
from legal_ai.retrieval.text import Term, normalize_for_search, parse_query, word_matches
from legal_ai.sources.courts import COURTS
from legal_ai.web.jobs import JobRunner, list_runs, list_traces, load_run, load_trace

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_WORD = re.compile(r"[0-9A-Za-zА-Яа-яѝЍ]+")


def highlight(text: str, terms: list[Term]) -> Markup:
    """Escape, then wrap matching words in <mark>. Display only; stored text is untouched."""
    out, last = [], 0
    for m in _WORD.finditer(text):
        out.append(escape(text[last:m.start()]))
        word = m.group(0)
        norm = normalize_for_search(word)
        out.append(Markup("<mark>") + escape(word) + Markup("</mark>")
                   if norm and word_matches(norm, terms) else escape(word))
        last = m.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Legal AI Solo", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    _TEMPLATES.env.globals["highlight"] = highlight

    @app.get("/health")
    def health() -> JSONResponse:
        with connect(settings.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return JSONResponse({"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = Query("", max_length=500), only_290: bool = False):
        result = None
        if q.strip():
            with connect(settings.database_url) as conn:
                result = search(conn, q, only_290=only_290)
        with connect(settings.database_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) AS n, min(act_date) AS first, max(act_date) AS last,
                       count(*) FILTER (WHERE proceeding_article = '290') AS n290
                FROM decisions WHERE current_version_id IS NOT NULL""")
            corpus = cur.fetchone()
            # a truncated list that was then split by chamber is covered by its parts
            cur.execute(r"""
                SELECT count(*) AS n FROM source_list_runs t
                WHERE t.truncated AND NOT EXISTS (
                    SELECT 1 FROM source_list_runs s
                    WHERE s.source = t.source AND s.description LIKE t.description || '\_\_%')""")
            corpus["truncated_lists"] = cur.fetchone()["n"]
        return _TEMPLATES.TemplateResponse(request, "search.html", {
            "q": q, "only_290": only_290, "result": result, "corpus": corpus,
        })

    @app.get("/decisions/{decision_id}", response_class=HTMLResponse)
    def decision(request: Request, decision_id: UUID, p: int | None = None, q: str = ""):
        with connect(settings.database_url) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT d.*, v.id AS version_id, v.text_hash, v.parser_version, v.parsed_at,
                       a.retrieved_at, a.acquisition, a.sha256
                FROM decisions d
                JOIN decision_versions v ON v.id = d.current_version_id
                JOIN source_artifacts a ON a.id = v.artifact_id
                WHERE d.id = %s""", (decision_id,))
            d = cur.fetchone()
            if d is None:
                raise HTTPException(404, "Няма такъв акт в корпуса.")
            cur.execute("""
                SELECT paragraph_no, section, start_offset, end_offset, exact_text, is_admission
                FROM passages WHERE decision_version_id = %s ORDER BY paragraph_no""",
                        (d["version_id"],))
            paragraphs = cur.fetchall()
        return _TEMPLATES.TemplateResponse(request, "decision.html", {
            "d": d, "paragraphs": paragraphs, "target": p, "terms": parse_query(q), "q": q,
        })

    runs_dir = Path(os.environ.get("PRIVATE_STORAGE_PATH", "data")) / "runs"
    traces_dir = runs_dir.parent / "traces"
    runner = JobRunner(runs_dir, traces_dir)

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        return bool(origin) and urlparse(origin).netloc == request.headers.get("host", "")

    @app.get("/analyze", response_class=HTMLResponse)
    def analyze_form(request: Request):
        return _TEMPLATES.TemplateResponse(request, "analyze.html", {
            "courts": COURTS, "runs": list_runs(runs_dir), "traces": list_traces(traces_dir),
            "jobs": sorted(runner.jobs.values(), key=lambda j: j.started, reverse=True),
            "busy": runner.busy(),
        })

    @app.post("/analyze")
    def analyze_start(request: Request, court: str = Form(...), case: int = Form(..., ge=1, le=999999),
                      year: int = Form(..., ge=2000, le=2100), case_type: str = Form(""),
                      until: str = Form(""), mode: str = Form("noai")):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if court not in COURTS or case_type not in ("", "Гражданско", "Търговско") \
                or mode not in ("noai", "ai"):
            raise HTTPException(400, "Невалиден съд или вид дело.")
        if until and not re.fullmatch(r"\d{4}-\d{2}", until):
            raise HTTPException(400, "Датата трябва да е ГГГГ-ММ.")
        if runner.busy():
            raise HTTPException(409, "Вече тече анализ. Изчакайте да приключи.")
        job = runner.start({"court": court, "case": case, "year": year, "type": case_type,
                            "until": until, "mode": mode})
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Няма такъв анализ.")
        if job.status == "done" and job.run_dir:
            kind = "traces" if job.params.get("mode") == "noai" else "runs"
            return RedirectResponse(f"/{kind}/{job.run_dir}", status_code=303)
        return _TEMPLATES.TemplateResponse(request, "job.html", {"job": job, "courts": COURTS})

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_report(request: Request, run_id: str):
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        by_q: dict[str, list] = {}
        for a in run["assessments"]:
            if a["relevant"] and a["stance"] != "неотносимо":
                by_q.setdefault(a["question_id"], []).append(a)
        return _TEMPLATES.TemplateResponse(request, "report.html", {"run": run, "by_q": by_q})

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    def trace_report(request: Request, trace_id: str):
        t = load_trace(traces_dir, trace_id)
        if t is None:
            raise HTTPException(404, "Няма такава справка.")
        return _TEMPLATES.TemplateResponse(request, "trace.html", {"t": t})

    return app
