"""Local web UI. The only mutation (starting an analysis) checks Origin against the Host."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import psycopg

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.middleware.trustedhost import TrustedHostMiddleware

from legal_ai.config import load_settings
from legal_ai.db import connect
from legal_ai.retrieval.lexical import search
from legal_ai.retrieval.text import Term, normalize_for_search, parse_query, word_matches
from legal_ai.sources.courts import COURTS
from legal_ai.web import auth
from legal_ai.web.jobs import JobRunner, load_run, load_trace
from legal_ai.web.views import corpus_events, corpus_month, empty_month, list_reports, month_param

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
    hosts = ["127.0.0.1", "localhost"] + [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        hosts.append(os.environ["RAILWAY_PUBLIC_DOMAIN"])
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        hosts.append("healthcheck.railway.app")   # Railway's healthcheck Host (only /health is open)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/health") or path.startswith("/static/"):
            return await call_next(request)
        if not auth.password():
            if not auth.hosted() and (request.url.hostname or "") in auth.LOCAL_HOSTS:
                return await call_next(request)   # local use without a password, as before
            return RedirectResponse("/login", status_code=303)
        if auth.cookie_ok(request.cookies.get(auth.COOKIE)):
            return await call_next(request)
        if request.method != "GET":
            return HTMLResponse("Необходим е вход.", status_code=401)
        return RedirectResponse("/login?next=" + quote(path + ("?" + request.url.query if request.url.query else "")),
                                status_code=303)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    _TEMPLATES.env.globals["highlight"] = highlight
    storage = Path(os.environ.get("PRIVATE_STORAGE_PATH", "data"))
    runs_dir = storage / "runs"
    traces_dir = storage / "traces"
    runner = JobRunner(runs_dir, traces_dir)

    def render(request: Request, name: str, ctx: dict, status_code: int = 200):
        running = [j for j in runner.jobs.values() if j.status in ("queued", "running")]
        return _TEMPLATES.TemplateResponse(request, name, {
            "running_job": running[0] if running else None,
            "today": date.today().strftime("%d.%m.%Y"), **ctx}, status_code=status_code)

    def corpus_counts(cur) -> dict:
        cur.execute("""
            SELECT count(*) AS n, min(act_date) AS first, max(act_date) AS last,
                   count(*) FILTER (WHERE proceeding_article = '290') AS n290,
                   count(*) FILTER (WHERE source = 'vks-tr') AS ntr,
                   count(*) FILTER (WHERE source = 'vks' AND case_type = 'гр.') AS ngr,
                   count(*) FILTER (WHERE source = 'vks' AND case_type = 'търг.') AS ntarg
            FROM decisions WHERE current_version_id IS NOT NULL""")
        corpus = cur.fetchone()
        # a truncated list that was then split by chamber is covered by its parts
        cur.execute(r"""
            SELECT count(*) AS n FROM source_list_runs t
            WHERE t.truncated AND NOT EXISTS (
                SELECT 1 FROM source_list_runs s
                WHERE s.source = t.source AND s.description LIKE t.description || '\_\_%')""")
        corpus["truncated_lists"] = cur.fetchone()["n"]
        return corpus

    def safe_next(value: str) -> str:
        return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/analyze"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = Query("/analyze", max_length=500)):
        return _TEMPLATES.TemplateResponse(request, "login.html", {
            "configured": bool(auth.password()), "next": safe_next(next), "error": ""})

    @app.post("/login")
    def login(request: Request, password: str = Form("", max_length=500), next: str = Form("/analyze", max_length=500),
              remember: str = Form("")):
        client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or \
            (request.client.host if request.client else "?")
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        error = ""
        if auth.too_many_failures(client):
            error = "Твърде много грешни опити. Опитайте след 10 минути."
        elif not auth.check_password(password):
            auth.record_failure(client)
            error = "Грешна парола."
        if error:
            return _TEMPLATES.TemplateResponse(request, "login.html", {
                "configured": bool(auth.password()), "next": safe_next(next), "error": error}, status_code=401)
        resp = RedirectResponse(safe_next(next), status_code=303)
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        resp.set_cookie(auth.COOKIE, auth.make_cookie(), max_age=auth.MAX_AGE if remember else None,
                        httponly=True, secure=https, samesite="lax")
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(auth.COOKIE)
        return resp

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
            corpus = corpus_counts(cur)
        return render(request, "search.html", {
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
        return render(request, "decision.html", {
            "d": d, "paragraphs": paragraphs, "target": p, "terms": parse_query(q), "q": q,
        })

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        return bool(origin) and urlparse(origin).netloc == request.headers.get("host", "")

    def dashboard(m: str | None) -> dict:
        reports = list_reports(runs_dir, traces_dir)
        try:
            with connect(settings.database_url) as conn, conn.cursor() as cur:
                # default month: the latest month that is well covered (>= 20 decisions)
                cur.execute("""
                    SELECT date_trunc('month', act_date)::date AS last FROM decisions
                    WHERE source = 'vks' AND act_date IS NOT NULL
                    GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1 DESC LIMIT 1""")
                row = cur.fetchone()
                last = row["last"] if row else date.today()
                month = corpus_month(conn, *month_param(m, last))
                corpus = corpus_counts(cur)
        except psycopg.OperationalError:  # the case form must work even when the database is down
            month = empty_month(*month_param(m, date.today()))
            corpus = {"n": 0, "n290": 0, "ntr": 0, "ngr": 0, "ntarg": 0, "first": None, "last": None,
                      "truncated_lists": 0}
        return {"reports": reports, "month": month, "events": corpus_events(storage), "corpus": corpus}

    @app.get("/analyze", response_class=HTMLResponse)
    def analyze_form(request: Request, m: str | None = Query(None, max_length=7)):
        return render(request, "analyze.html", {"courts": COURTS, "busy": runner.busy(), **dashboard(m)})

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, m: str | None = Query(None, max_length=7)):
        return render(request, "reports.html", dashboard(m))

    @app.get("/corpus", response_class=HTMLResponse)
    def corpus_page(request: Request, m: str | None = Query(None, max_length=7)):
        return render(request, "corpus.html", dashboard(m))

    @app.post("/analyze")
    def analyze_start(request: Request, court: str = Form(""), case: int | None = Form(None, ge=1, le=999999),
                      year: int | None = Form(None, ge=2000, le=2100), case_type: str = Form(""),
                      until: str = Form(""), mode: str = Form("noai"),
                      document: UploadFile | None = File(None)):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")

        def form_error(msg: str, status: int = 400):
            return render(request, "analyze.html", {"courts": COURTS, "busy": runner.busy(), "error": msg,
                                                    **dashboard(None)}, status_code=status)

        if (court and court not in COURTS) or case_type not in ("", "Гражданско", "Търговско") \
                or mode not in ("noai", "ai"):
            return form_error("Невалиден съд, вид дело или режим.")
        if until and not re.fullmatch(r"\d{4}-\d{2}", until):
            return form_error("„Практика до“ трябва да е във вида ГГГГ-ММ, напр. 2022-05.")
        if runner.busy():
            return form_error("Вече тече справка. Изчакайте да приключи.", 409)
        params = {"court": court, "case": case, "year": year, "type": case_type, "until": until, "mode": mode}
        if document is not None and document.filename:
            from legal_ai.upload import MAX_BYTES, UploadError, extension, read_upload
            body = document.file.read(MAX_BYTES + 1)
            try:
                read_upload(document.filename, body)          # fail early with a plain message
            except UploadError as exc:
                return form_error(str(exc))
            uploads = storage / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            name = f"{uuid4().hex}{extension(document.filename)}"
            (uploads / name).write_bytes(body)
            params.update(file=f"uploads/{name}", filename=os.path.basename(document.filename)[:120])
        elif not (court and case and year):
            return form_error("Въведете съд, номер и година на делото или качете документ.")
        job = runner.start(params)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Няма такъв анализ.")
        if job.status == "done" and job.run_dir:
            kind = "traces" if job.params.get("mode") == "noai" else "runs"
            return RedirectResponse(f"/{kind}/{job.run_dir}", status_code=303)
        return render(request, "job.html", {"job": job, "courts": COURTS})

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_report(request: Request, run_id: str):
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        by_q: dict[str, list] = {}
        for a in run["assessments"]:
            if a["relevant"] and a["stance"] != "неотносимо":
                by_q.setdefault(a["question_id"], []).append(a)
        return render(request, "report.html", {"run": run, "by_q": by_q})

    @app.get("/runs/{run_id}/draft", response_class=HTMLResponse)
    def run_draft(request: Request, run_id: str):
        from legal_ai.cassation.draft import build_draft
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        return render(request, "draft.html", {"run": run, "run_id": run_id, "blocks": build_draft(run)})

    @app.get("/runs/{run_id}/draft.docx")
    def run_draft_docx(run_id: str):
        from fastapi.responses import Response

        from legal_ai.cassation.draft import build_draft, to_docx
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        return Response(to_docx(build_draft(run)),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="izlozhenie-{run_id}.docx"'})

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    def trace_report(request: Request, trace_id: str):
        t = load_trace(traces_dir, trace_id)
        if t is None:
            raise HTTPException(404, "Няма такава справка.")
        return render(request, "trace.html", {"t": t})

    return app
