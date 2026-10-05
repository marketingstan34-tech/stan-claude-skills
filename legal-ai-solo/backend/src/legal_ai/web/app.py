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
from legal_ai.web import auth, credits
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

    def price_info() -> dict:
        from legal_ai.ai import pricing
        try:
            max_calls = int(os.environ.get("AI_MAX_CALLS_PER_RUN", "60"))
        except ValueError:
            max_calls = 60
        return {"report": pricing.TYPICAL_REPORT_USD, "appeal": pricing.TYPICAL_APPEAL_USD, "max_calls": max_calls}

    def render(request: Request, name: str, ctx: dict, status_code: int = 200):
        return _TEMPLATES.TemplateResponse(request, name, {
            "running_job": runner.running(),
            "today": date.today().strftime("%d.%m.%Y"), "today_iso": date.today().isoformat(),
            "price": price_info(), "credits": credits.summary(storage, runs_dir),
            **ctx}, status_code=status_code)

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
        return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/start"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = Query("/start", max_length=500)):
        return _TEMPLATES.TemplateResponse(request, "login.html", {
            "configured": bool(auth.password()), "next": safe_next(next), "error": ""})

    @app.post("/login")
    def login(request: Request, password: str = Form("", max_length=500), next: str = Form("/start", max_length=500),
              remember: str = Form("")):
        # X-Forwarded-For is set by Railway's edge; without that proxy the header is the client's own
        forwarded = request.headers.get("x-forwarded-for", "") if os.environ.get("RAILWAY_ENVIRONMENT") else ""
        client = auth.client_address(forwarded, request.client.host if request.client else "")
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

    KINDS = {   # lists opened from the corpus page: (title, SQL condition without user values)
        "gr": ("Граждански дела (ВКС)", "source = 'vks' AND case_type = 'гр.'"),
        "targ": ("Търговски дела (ВКС)", "source = 'vks' AND case_type = 'търг.'"),
        "290": ("Решения по чл. 290 ГПК", "proceeding_article = '290'"),
        "tr": ("Тълкувателни решения", "source = 'vks-tr'"),
        "all": ("Всички актове в базата", "true"),
    }
    PER_PAGE = 50

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = Query("", max_length=500), only_290: bool = False,
              d: str = Query("", max_length=10), kind: str = Query("", max_length=10),
              page: int = Query(1, ge=1, le=10_000)):
        result, day, day_list = None, None, []
        listing = None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            try:
                day = date.fromisoformat(d)
            except ValueError:
                day = None
        if q.strip() and not day and kind not in KINDS:   # a day or a list is its own page
            with connect(settings.database_url) as conn:
                result = search(conn, q, only_290=only_290)
        with connect(settings.database_url) as conn, conn.cursor() as cur:
            corpus = corpus_counts(cur)
            if day:   # the decisions of one day (from the calendar)
                cur.execute("""
                    SELECT id, source, act_type, act_number, act_date, case_type, case_number, case_year,
                           chamber, proceeding_article FROM decisions
                    WHERE act_date = %s AND current_version_id IS NOT NULL
                    ORDER BY source DESC, chamber,
                             NULLIF(regexp_replace(act_number, '\\D', '', 'g'), '')::bigint NULLS LAST""", (day,))
                day_list = cur.fetchall()
            elif kind in KINDS:   # one kind of decision, newest first
                title, cond = KINDS[kind]
                cur.execute(f"SELECT count(*) AS n FROM decisions WHERE current_version_id IS NOT NULL AND {cond}")
                total = cur.fetchone()["n"]
                cur.execute(f"""
                    SELECT id, source, act_type, act_number, act_date, case_type, case_number, case_year,
                           chamber, proceeding_article FROM decisions
                    WHERE current_version_id IS NOT NULL AND {cond}
                    ORDER BY act_date DESC NULLS LAST, id LIMIT %s OFFSET %s""",
                            (PER_PAGE, (page - 1) * PER_PAGE))
                day_list = cur.fetchall()
                listing = {"kind": kind, "title": title, "total": total, "page": page,
                           "pages": max(1, -(-total // PER_PAGE))}
        return render(request, "search.html", {
            "q": q, "only_290": only_290, "result": result, "corpus": corpus, "day": day, "day_list": day_list,
            "listing": listing,
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
    def reports_page(request: Request, m: str | None = Query(None, max_length=7),
                     mode: str = Query("", max_length=5), status: str = Query("", max_length=20)):
        from legal_ai.cassation.casefile import STATUSES
        ctx = dashboard(m)
        all_reports = ctx["reports"]
        ctx["statuses"] = [(s, sum(r["status"] == s for r in all_reports)) for s in STATUSES]
        ctx["status"] = status if status in STATUSES else ""
        if ctx["status"]:
            all_reports = [r for r in all_reports if r["status"] == ctx["status"]]
            ctx["reports"] = all_reports
        ctx["counts"] = {"noai": sum(r["rtype"] == "trace" for r in all_reports),
                         "ai": sum(r["rtype"] == "run" for r in all_reports)}
        mode = mode if mode in ("ai", "noai") else ""
        if mode:
            ctx["reports"] = [r for r in all_reports if r["rtype"] == ("run" if mode == "ai" else "trace")]
        ctx["mode"] = mode
        return render(request, "reports.html", ctx)

    @app.get("/start", response_class=HTMLResponse)
    def start_page(request: Request):
        from legal_ai.web.views import overview
        try:
            with connect(settings.database_url) as conn, conn.cursor() as cur:
                corpus = corpus_counts(cur)
        except psycopg.OperationalError:
            corpus = None
        return render(request, "start.html", {**overview(runs_dir, traces_dir), "corpus": corpus,
                                              "events": corpus_events(storage)[:3]})

    @app.get("/help", response_class=HTMLResponse)
    def help_page(request: Request):
        return render(request, "help.html", {})

    @app.get("/corpus", response_class=HTMLResponse)
    def corpus_page(request: Request, m: str | None = Query(None, max_length=7)):
        return render(request, "corpus.html", dashboard(m))

    @app.post("/analyze")
    def analyze_start(request: Request, court: str = Form(""), case_text: str = Form("", alias="case", max_length=20),
                      year_text: str = Form("", alias="year", max_length=20), case_type: str = Form(""),
                      until: str = Form(""), mode: str = Form("noai"),
                      document: UploadFile | None = File(None), text: str = Form("", max_length=400_000),
                      extra: list[UploadFile] | None = File(None)):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")

        def form_error(msg: str, status: int = 400):
            return render(request, "analyze.html", {"courts": COURTS, "busy": runner.busy(), "error": msg,
                                                    **dashboard(None)}, status_code=status)

        # empty number and year are allowed (the document is enough); browsers send them as ""
        case = int(case_text) if case_text.strip().isdigit() else None
        year = int(year_text) if year_text.strip().isdigit() else None
        if (case_text.strip() and not (case and 1 <= case <= 999999)) or \
                (year_text.strip() and not (year and 2000 <= year <= 2100)):
            return form_error("Номерът на делото трябва да е число до 999999, а годината – между 2000 и 2100.")
        if (court and court not in COURTS) or case_type not in ("", "Гражданско", "Търговско") \
                or mode not in ("noai", "ai"):
            return form_error("Невалиден съд, вид дело или режим.")
        if until and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", until):
            return form_error("„Практика до“ трябва да е във вида ГГГГ-ММ, напр. 2022-05.")
        if runner.busy():
            return form_error("Вече тече справка. Изчакайте да приключи.", 409)
        params = {"court": court, "case": case, "year": year, "type": case_type, "until": until, "mode": mode}
        text = text.strip()
        has_document = document is not None and bool(document.filename)
        if text and (has_document or (court and case and year)):
            # the decision comes from the file or the court's site: the text is the lawyer's notes
            if len(text) > 20_000:
                return form_error("Бележките са над 20 000 знака. Съкратете ги или ги качете като документ.")
            params["notes"] = text
            text = ""
        if text:   # pasted text (the decision, or a description of the case) is treated like an uploaded .txt
            from legal_ai.upload import UploadError, read_upload
            body = text.encode("utf-8")
            try:
                read_upload("text.txt", body)
            except UploadError as exc:
                return form_error(str(exc).replace("В документа почти няма текст (може да е сканиран). "
                                                   "Качете PDF с текст или Word файл.",
                                                   "Текстът е твърде кратък: поставете поне 300 знака."))
            uploads = storage / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            name = f"{uuid4().hex}.txt"
            (uploads / name).write_bytes(body)
            params.update(file=f"uploads/{name}", filename="Поставен текст.txt", pasted=True)
        elif has_document:
            from legal_ai.upload import MAX_BYTES, UploadError, extension, read_upload
            body = document.file.read(MAX_BYTES + 1)
            try:
                read_upload(document.filename, body, ocr=False)   # fail early with a plain message
            except UploadError as exc:
                return form_error(str(exc))
            uploads = storage / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            name = f"{uuid4().hex}{extension(document.filename)}"
            (uploads / name).write_bytes(body)
            params.update(file=f"uploads/{name}", filename=os.path.basename(document.filename)[:120])
        elif not (court and case and year):
            return form_error("Въведете съд, номер и година на делото, качете документ или поставете текст.")
        extras = [f for f in (extra or []) if f is not None and f.filename]
        if len(extras) > 5:
            return form_error("Най-много 5 други документа.")
        if extras:
            from legal_ai.upload import MAX_BYTES, UploadError, extension, read_upload
            uploads = storage / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            params["extras"] = []
            for f in extras:
                body = f.file.read(MAX_BYTES + 1)
                try:
                    read_upload(f.filename, body, ocr=False)
                except UploadError as exc:
                    return form_error(f"{os.path.basename(f.filename)[:80]}: {exc}")
                name = f"{uuid4().hex}{extension(f.filename)}"
                (uploads / name).write_bytes(body)
                params["extras"].append({"file": f"uploads/{name}", "filename": os.path.basename(f.filename)[:120]})
        job = runner.start(params)
        if job is None:
            return form_error("Вече тече справка. Изчакайте да приключи.", 409)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_status(request: Request, job_id: str):
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Няма такъв анализ.")
        if job.status == "done" and job.redirect:
            return RedirectResponse(job.redirect, status_code=303)
        if job.status == "done" and job.run_dir:
            if job.params.get("mode") == "appeal":
                return RedirectResponse(f"/runs/{job.run_dir}/appeal", status_code=303)
            kind = "traces" if job.params.get("mode") == "noai" else "runs"
            return RedirectResponse(f"/{kind}/{job.run_dir}", status_code=303)
        return render(request, "job.html", {"job": job, "courts": COURTS})

    def refresh(run: dict, d: Path, appeal: dict | None = None) -> None:
        """Standard labels for reports from documents and dated interpretative decisions."""
        from legal_ai.cassation.labels import refresh_run
        try:
            conn = connect(settings.database_url)
        except psycopg.OperationalError:
            conn = None
        try:
            refresh_run(run, d, conn, appeal)
        finally:
            if conn is not None:
                conn.close()

    def case_context(base: Path, item_id: str, label: str, run: dict | None = None) -> dict:
        """The lawyer's data for a report, with the deadline, the threshold check and hints."""
        from legal_ai.cassation import casefile, deadline
        d = base / item_id
        case = casefile.load_case(d)
        try:
            text = (d / "appellate.txt").read_text(encoding="utf-8")
        except OSError:
            text = ""
        hint_amount = casefile.suggest_amount(text)
        due = None
        if case.get("served"):
            due = deadline.appeal_deadline(date.fromisoformat(case["served"]))
        kind = case.get("kind") or casefile.case_kind(label)
        threshold = deadline.threshold_check(case.get("amount"), case.get("currency", "BGN"), kind,
                                             bool(case.get("property")))
        chosen = case.get("questions") or (casefile.default_questions(run) if run else [])
        return {"case": case, "due": due, "threshold": threshold, "kind": kind, "kinds": deadline.CASE_KINDS, "chosen": chosen,
                "ranked": casefile.rank_questions(run) if run else [],
                "hint_amount": hint_amount, "act_number": casefile.decision_number(text),
                "saved": bool(case)}

    async def save_case_form(request: Request, base: Path, item_id: str, run: dict | None, back: str):
        from legal_ai.cassation import casefile
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        form = await request.form()
        data = {k: form.get(k) for k in ("served", "amount", "currency", "kind", "property", *casefile.FIELDS)}
        qids = [q["id"] for q in run["analysis"]["questions"]] if run else []
        if run is not None:
            data["questions"] = form.getlist("questions")
        clean, error = casefile.parse_form(data, qids)
        if error:
            return RedirectResponse(f"{back}?error={quote(error)}#case-data", status_code=303)
        if run is not None and not clean.get("questions"):
            return RedirectResponse(f"{back}?error={quote('Отметнете поне един въпрос.')}#case-data", status_code=303)
        clean["status"] = casefile.load_case(base / item_id).get("status", "нов")
        casefile.save_case(base / item_id, clean)
        return RedirectResponse(f"{back}?saved=1#case-data", status_code=303)

    async def save_status(request: Request, base: Path, item_id: str, back: str):
        from legal_ai.cassation import casefile
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        form = await request.form()
        casefile.set_status(base / item_id, str(form.get("status", "")))
        return RedirectResponse(back, status_code=303)

    @app.post("/credits")
    async def set_credits(request: Request):
        """The balance shown on the OpenAI billing page, typed by the lawyer after a top-up."""
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        form = await request.form()
        back = str(form.get("next") or "/start")
        if not back.startswith("/") or back.startswith("//") or "\\" in back:
            back = "/start"
        balance = credits.parse_balance(str(form.get("balance") or ""))
        if balance is None:
            sep = "&" if "?" in back else "?"
            return RedirectResponse(f"{back}{sep}credits_error=1", status_code=303)
        credits.save(storage, balance)
        return RedirectResponse(back, status_code=303)

    def to_trash(base: Path, kind: str, item_id: str) -> None:
        """A report is moved to storage/trash/<kind>/ (not destroyed), so a mistake can be undone on the server."""
        import shutil
        dest = storage / "trash" / kind
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / item_id
        n = 1
        while target.exists():
            target = dest / f"{item_id}-{n}"
            n += 1
        shutil.move(str(base / item_id), str(target))

    @app.post("/runs/{run_id}/delete")
    def run_delete(request: Request, run_id: str):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        job = runner.running()
        if job is not None and job.params.get("run_id") == run_id:
            raise HTTPException(409, "За тази справка в момента се пише жалба. Изчакайте да свърши.")
        to_trash(runs_dir, "runs", run_id)
        return RedirectResponse("/reports?deleted=1", status_code=303)

    @app.post("/traces/{trace_id}/delete")
    def trace_delete(request: Request, trace_id: str):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_trace(traces_dir, trace_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        to_trash(traces_dir, "traces", trace_id)
        return RedirectResponse("/reports?deleted=1", status_code=303)

    @app.post("/runs/{run_id}/status")
    async def run_status(request: Request, run_id: str):
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        return await save_status(request, runs_dir, run_id, f"/runs/{run_id}")

    @app.post("/traces/{trace_id}/status")
    async def trace_status(request: Request, trace_id: str):
        if load_trace(traces_dir, trace_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        return await save_status(request, traces_dir, trace_id, f"/traces/{trace_id}")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_report(request: Request, run_id: str, error: str = Query("", max_length=200)):
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        refresh(run, runs_dir / run_id)
        by_q: dict[str, list] = {}
        for a in run["assessments"]:
            if a["relevant"] and a["stance"] != "неотносимо":
                by_q.setdefault(a["question_id"], []).append(a)
        from legal_ai.ai.pricing import cost_usd
        return render(request, "report.html", {"run": run, "by_q": by_q, "run_id": run_id, "form_error": error,
                                               "run_cost": cost_usd(run["usage"].get("by_model") or {}),
                                               **case_context(runs_dir, run_id, run["appellate"]["label"], run)})

    @app.get("/runs/{run_id}/admission", response_class=HTMLResponse)
    def run_admission(request: Request, run_id: str):
        from legal_ai.cassation import admission, casefile
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        chosen = casefile.load_case(runs_dir / run_id).get("questions") or None
        items, total, error = [], 0, ""
        try:
            with connect(settings.database_url) as conn:
                total = admission.rulings_count(conn)
                if total:
                    items = admission.chances(conn, run, chosen)
        except psycopg.Error:
            error = "Базата не е достъпна в момента. Опитайте след малко."
        return render(request, "admission.html", {"run": run, "run_id": run_id, "items": items,
                                                  "total": total, "error": error, "chosen": chosen})

    @app.get("/runs/{run_id}/judge", response_class=HTMLResponse)
    def run_judge(request: Request, run_id: str):
        import json as _json
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        try:
            result = _json.loads((runs_dir / run_id / "judge.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
        from legal_ai.ai.pricing import TYPICAL_JUDGE_USD, cost_usd
        cost = cost_usd({result["model"]: [result["usage"]["input_tokens"], result["usage"]["output_tokens"]]}) \
            if result and result.get("usage") else None
        has_appeal = (runs_dir / run_id / "appeal.json").exists() or (runs_dir / run_id / "edit-appeal.json").exists()
        return render(request, "judge.html", {"run": run, "run_id": run_id, "result": result, "cost": cost,
                                              "judge_price": TYPICAL_JUDGE_USD, "has_appeal": has_appeal,
                                              "busy": runner.busy()})

    @app.post("/runs/{run_id}/judge")
    def run_judge_start(request: Request, run_id: str):
        from legal_ai.cassation.draft import to_text
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        _, _, draft = draft_blocks(run_id)
        _, _, appeal, appeal_b = appeal_blocks(run_id)
        notes = ""
        try:
            from legal_ai.cassation import admission
            with connect(settings.database_url) as conn:
                if admission.rulings_count(conn):
                    notes = "\n".join(
                        f"{c.question_id}: {c.found} сходни определения, допуснати {c.admitted}, недопуснати {c.refused}"
                        + (f"; чести мотиви за отказ: {', '.join(r for r, _ in c.reasons)}" if c.reasons else "")
                        for c in admission.chances(conn, load_run(runs_dir, run_id)) if c.found)
        except psycopg.Error:
            notes = ""
        job = runner.start({"mode": "judge", "run_id": run_id, "statement": to_text(draft),
                            "appeal": to_text(appeal_b) if appeal_b else "", "admission": notes})
        if job is None:
            return RedirectResponse(f"/runs/{run_id}/judge?busy=1", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    def filing_blocks(run_id: str, kind: str):
        import json as _json

        from legal_ai.cassation.filings import FILINGS, build_filing
        if kind not in FILINGS:
            raise HTTPException(404, "Няма такъв документ.")
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        try:
            filing = _json.loads((runs_dir / run_id / f"filing-{kind}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            filing = None
        ctx = case_context(runs_dir, run_id, run["appellate"]["label"], run)
        blocks = build_filing(kind, run, filing, ctx["case"], ctx["act_number"]) if filing else []
        return run, ctx, filing, blocks

    @app.get("/runs/{run_id}/filing/{kind}", response_class=HTMLResponse)
    def run_filing(request: Request, run_id: str, kind: str):
        from legal_ai.ai.pricing import TYPICAL_APPEAL_USD, cost_usd
        from legal_ai.cassation.filings import FILINGS
        run, ctx, filing, blocks = filing_blocks(run_id, kind)
        cost = cost_usd({filing["model"]: [filing["usage"]["input_tokens"], filing["usage"]["output_tokens"]]}) \
            if filing and filing.get("usage") else None
        return render(request, "filing.html", {"run": run, "run_id": run_id, "kind": kind, "spec": FILINGS[kind],
                                               "filings": FILINGS, "filing": filing, "blocks": blocks, "cost": cost,
                                               "price_range": TYPICAL_APPEAL_USD, "busy": runner.busy(), **ctx})

    @app.post("/runs/{run_id}/filing/{kind}")
    def run_filing_start(request: Request, run_id: str, kind: str):
        from legal_ai.cassation.filings import FILINGS
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if kind not in FILINGS or load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        job = runner.start({"mode": "filing", "run_id": run_id, "kind": kind})
        if job is None:
            return RedirectResponse(f"/runs/{run_id}/filing/{kind}?busy=1", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/runs/{run_id}/filing-docx/{kind}")
    def run_filing_docx(run_id: str, kind: str):
        from fastapi.responses import Response

        from legal_ai.cassation.draft import to_docx
        _, _, filing, blocks = filing_blocks(run_id, kind)
        if not filing:
            raise HTTPException(404, "Още няма чернова.")
        return Response(to_docx(blocks),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{kind}-{run_id}.docx"'})

    @app.get("/judges", response_class=HTMLResponse)
    def judges_page(request: Request, q: str = Query("", max_length=80)):
        from legal_ai.judges import list_judges
        rows, error = [], ""
        try:
            with connect(settings.database_url) as conn:
                rows = list_judges(conn, q)
        except psycopg.Error:
            error = "Базата не е достъпна в момента."
        return render(request, "judges.html", {"rows": rows, "q": q, "error": error})

    @app.get("/judges/{name}", response_class=HTMLResponse)
    def judge_page(request: Request, name: str, w: str = Query("", max_length=120)):
        from legal_ai.judges import judge_detail
        try:
            with connect(settings.database_url) as conn:
                data = judge_detail(conn, name[:80], w)
        except psycopg.Error:
            raise HTTPException(503, "Базата не е достъпна в момента.")
        if not data["totals"]["n"] and not data["totals"]["panel"]:
            raise HTTPException(404, "Няма такъв съдия в базата.")
        return render(request, "judge_profile.html", {**data, "w": w})

    @app.get("/benchmark", response_class=HTMLResponse)
    def benchmark_page(request: Request):
        import json as _json

        from legal_ai.ai.pricing import TYPICAL_REPORT_USD, cost_usd
        bdir = storage / "benchmark"
        runs = []
        for f in sorted(bdir.glob("*.json"), reverse=True)[:5] if bdir.is_dir() else []:
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            data["cost"] = cost_usd((data.get("usage") or {}).get("by_model") or {})
            runs.append(data)
        available = 0
        try:
            from legal_ai.cassation import admission
            with connect(settings.database_url) as conn:
                available = admission.rulings_count(conn)
        except psycopg.Error:
            pass
        from legal_ai.benchmark import WHY
        return render(request, "benchmark.html", {"runs": runs, "available": available, "why": WHY,
                                                  "price_one": TYPICAL_REPORT_USD, "busy": runner.busy()})

    @app.post("/benchmark")
    async def benchmark_start(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        form = await request.form()
        try:
            n = max(2, min(10, int(form.get("n", "3"))))
        except ValueError:
            n = 3
        job = runner.start({"mode": "benchmark", "n": n})
        if job is None:
            return RedirectResponse("/benchmark?busy=1", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/runs/{run_id}/case")
    async def run_case(request: Request, run_id: str):
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        return await save_case_form(request, runs_dir, run_id, run, f"/runs/{run_id}")

    def draft_blocks(run_id: str):
        from legal_ai.cassation.draft import build_draft
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        refresh(run, runs_dir / run_id)
        ctx = case_context(runs_dir, run_id, run["appellate"]["label"], run)
        case = {**ctx["case"], "questions": ctx["chosen"]}
        from legal_ai.cassation.edits import load_edit
        edit = load_edit(runs_dir / run_id, "draft")
        ctx["edited"] = edit["saved_at"] if edit else ""
        return run, ctx, edit["blocks"] if edit else build_draft(run, case, ctx["act_number"])

    @app.get("/runs/{run_id}/draft", response_class=HTMLResponse)
    def run_draft(request: Request, run_id: str):
        run, ctx, blocks = draft_blocks(run_id)
        return render(request, "draft.html", {"run": run, "run_id": run_id, "blocks": blocks, **ctx})

    @app.get("/runs/{run_id}/draft.docx")
    def run_draft_docx(run_id: str):
        from fastapi.responses import Response

        from legal_ai.cassation.draft import to_docx
        _, _, blocks = draft_blocks(run_id)
        return Response(to_docx(blocks),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="izlozhenie-{run_id}.docx"'})

    @app.get("/runs/{run_id}/attachments.zip")
    def run_attachments(run_id: str):
        from fastapi.responses import Response

        from legal_ai.cassation.attachments import build_zip
        from legal_ai.cassation.draft import attached_labels
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        refresh(run, runs_dir / run_id)
        ctx = case_context(runs_dir, run_id, run["appellate"]["label"], run)
        items = attached_labels(run, {"questions": ctx["chosen"]})
        if runner.busy():   # the court sites get one client at a time; use only the own database then
            fetch = None
        else:
            fetch = fetch_vks_text
        try:
            conn = connect(settings.database_url)
        except psycopg.OperationalError:
            conn = None
        try:
            body, _, _ = build_zip(items, conn, fetch)
        finally:
            if conn is not None:
                conn.close()
        return Response(body, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="prilozheniya-{run_id}.zip"'})

    def fetch_vks_text(source_id: str) -> str | None:
        from legal_ai.http import PoliteClient
        from legal_ai.sources.vks import HOST as VKS_HOST
        from legal_ai.sources.vks.parser import parse_act
        from legal_ai.sources.vks.urls import act_url
        ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
        with PoliteClient([VKS_HOST], 2.0, ua) as vks:
            return parse_act(vks.get(act_url(source_id)).body.decode("utf-8", errors="replace")).canonical_text

    def appeal_blocks(run_id: str):
        import json as _json

        from legal_ai.cassation.appeal import build_appeal
        from legal_ai.cassation.draft import attached_labels
        run = load_run(runs_dir, run_id)
        if run is None:
            raise HTTPException(404, "Няма такава справка.")
        try:
            appeal = _json.loads((runs_dir / run_id / "appeal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            appeal = None
        refresh(run, runs_dir / run_id, appeal)
        ctx = case_context(runs_dir, run_id, run["appellate"]["label"], run)
        blocks = build_appeal(run, appeal, ctx["case"], ctx["act_number"],
                              len(attached_labels(run, {"questions": ctx["chosen"]}))) if appeal else []
        from legal_ai.cassation.edits import load_edit
        edit = load_edit(runs_dir / run_id, "appeal")
        ctx["edited"] = edit["saved_at"] if edit else ""
        if edit:
            blocks = edit["blocks"]
        return run, ctx, appeal, blocks

    @app.get("/runs/{run_id}/appeal", response_class=HTMLResponse)
    def run_appeal(request: Request, run_id: str):
        run, ctx, appeal, blocks = appeal_blocks(run_id)
        from legal_ai.ai.pricing import cost_usd
        appeal_cost = cost_usd({appeal["model"]: [appeal["usage"]["input_tokens"], appeal["usage"]["output_tokens"]]}) \
            if appeal and appeal.get("usage") else None
        import json as _json

        from legal_ai.web.casedocs import case_docs, style_samples
        try:
            report_docs = [c["name"] for c in _json.loads((runs_dir / run_id / "context.json").read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError, TypeError):
            report_docs = []
        first = next((f"{i.get('court', '')}, дело {i.get('case', '')}" for i in run.get("path") or []
                      if i.get("level") == "първа" and any(a.get("type") == "Решение" and a.get("url")
                                                           for a in i.get("acts", []))), "")
        return render(request, "appeal.html", {"run": run, "run_id": run_id, "appeal": appeal, "blocks": blocks,
                                               "appeal_cost": appeal_cost, "report_docs": report_docs,
                                               "added_docs": case_docs(storage, run_id).items(),
                                               "first_instance": first,
                                               "style_docs": style_samples(storage).items(),
                                               "busy": runner.busy(), **ctx})

    async def add_files(request: Request, files_list, back: str) -> RedirectResponse:
        """Shared by the case documents and the style samples: quick check, then keep the file."""
        from legal_ai.upload import MAX_BYTES, UploadError, read_upload
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        form = await request.form()
        files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
        anchor = back.split("#", 1)
        sep = "&" if "?" in anchor[0] else "?"
        tail = ("#" + anchor[1]) if len(anchor) > 1 else ""
        if not files:
            return RedirectResponse(f"{anchor[0]}{sep}doc_error={quote('Изберете файл.')}{tail}", status_code=303)
        from legal_ai.web.casedocs import ZIP_MAX_TOTAL, expand
        skipped: list[str] = []
        for f in files:
            body = await f.read(ZIP_MAX_TOTAL + 1 if f.filename.lower().endswith(".zip") else MAX_BYTES + 1)
            try:
                parts, skip = expand(f.filename, body)
            except ValueError as exc:
                skipped.append(f"{os.path.basename(f.filename)[:80]} ({exc})")
                continue
            skipped += skip
            for name, part in parts:
                try:
                    text, _, _ = read_upload(name, part, ocr=False)
                    files_list.add(name, part, text)
                except (UploadError, ValueError) as exc:
                    skipped.append(f"{os.path.basename(name)[:80]} ({exc})")
        if skipped:
            msg = "Не са добавени: " + "; ".join(skipped[:8]) + \
                  (f" и още {len(skipped) - 8}" if len(skipped) > 8 else "")
            return RedirectResponse(f"{anchor[0]}{sep}doc_error={quote(msg)}{tail}", status_code=303)
        return RedirectResponse(back, status_code=303)

    @app.post("/runs/{run_id}/docs/{item_id}/toggle")
    def run_docs_toggle(request: Request, run_id: str, item_id: str):
        from legal_ai.web.casedocs import case_docs
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        docs = case_docs(storage, run_id)
        item = next((x for x in docs.items() if x.get("id") == item_id), None)
        if item is not None:
            docs.set_included(item_id, not item.get("included", True))
        return RedirectResponse(f"/runs/{run_id}/appeal#docs", status_code=303)

    @app.post("/runs/{run_id}/docs")
    async def run_docs_add(request: Request, run_id: str):
        from legal_ai.web.casedocs import case_docs
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        return await add_files(request, case_docs(storage, run_id), f"/runs/{run_id}/appeal#docs")

    @app.post("/runs/{run_id}/docs/{item_id}/delete")
    def run_docs_delete(request: Request, run_id: str, item_id: str):
        from legal_ai.web.casedocs import case_docs
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        case_docs(storage, run_id).remove(item_id)
        return RedirectResponse(f"/runs/{run_id}/appeal#docs", status_code=303)

    @app.post("/style")
    async def style_add(request: Request):
        from legal_ai.web.casedocs import style_samples
        return await add_files(request, style_samples(storage), "/documents#style")

    @app.post("/archive")
    async def archive_add(request: Request):
        from legal_ai.web.casedocs import archive
        return await add_files(request, archive(storage), "/documents#archive")

    @app.post("/archive/{item_id}/delete")
    def archive_delete(request: Request, item_id: str):
        from legal_ai.web.casedocs import archive
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        archive(storage).remove(item_id)
        return RedirectResponse("/documents#archive", status_code=303)

    @app.post("/style/{item_id}/delete")
    def style_delete(request: Request, item_id: str):
        from legal_ai.web.casedocs import style_samples
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        style_samples(storage).remove(item_id)
        return RedirectResponse("/documents#style", status_code=303)

    @app.post("/runs/{run_id}/appeal")
    def run_appeal_start(request: Request, run_id: str):
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        job = runner.start({"mode": "appeal", "run_id": run_id})
        if job is None:
            return RedirectResponse(f"/runs/{run_id}/appeal?busy=1", status_code=303)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/runs/{run_id}/appeal.docx")
    def run_appeal_docx(run_id: str):
        from fastapi.responses import Response

        from legal_ai.cassation.draft import to_docx
        _, _, appeal, blocks = appeal_blocks(run_id)
        if not appeal:
            raise HTTPException(404, "Още няма чернова на жалбата.")
        return Response(to_docx(blocks),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="zhalba-{run_id}.docx"'})

    def doc_blocks(run_id: str, doc: str):
        if doc == "draft":
            run, ctx, blocks = draft_blocks(run_id)
            return run, ctx, blocks
        if doc == "appeal":
            run, ctx, appeal, blocks = appeal_blocks(run_id)
            if not appeal and not ctx["edited"]:
                raise HTTPException(404, "Още няма чернова на жалбата.")
            return run, ctx, blocks
        raise HTTPException(404, "Няма такъв документ.")

    @app.get("/runs/{run_id}/{doc}/edit", response_class=HTMLResponse)
    def doc_edit(request: Request, run_id: str, doc: str):
        from legal_ai.cassation.edits import DOCS
        run, ctx, blocks = doc_blocks(run_id, doc)
        return render(request, "edit.html", {"run": run, "run_id": run_id, "doc": doc, "doc_title": DOCS[doc],
                                             "blocks": blocks, **ctx})

    @app.post("/runs/{run_id}/{doc}/edit")
    async def doc_edit_save(request: Request, run_id: str, doc: str):
        from legal_ai.cassation.edits import DOCS, clean_blocks, save_edit
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if doc not in DOCS or load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такъв документ.")
        try:
            body = await request.json()
            blocks = clean_blocks(body.get("blocks") if isinstance(body, dict) else None)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc) or "Невалиден текст."}, status_code=400)
        return JSONResponse({"ok": True, "saved_at": save_edit(runs_dir / run_id, doc, blocks)})

    @app.post("/runs/{run_id}/{doc}/reset")
    def doc_edit_reset(request: Request, run_id: str, doc: str):
        from legal_ai.cassation.edits import DOCS, reset_edit
        if not same_origin(request):
            raise HTTPException(403, "Заявката не идва от тази страница.")
        if doc not in DOCS or load_run(runs_dir, run_id) is None:
            raise HTTPException(404, "Няма такъв документ.")
        reset_edit(runs_dir / run_id, doc)
        return RedirectResponse(f"/runs/{run_id}/{doc}", status_code=303)

    @app.get("/documents", response_class=HTMLResponse)
    def documents_page(request: Request):
        from legal_ai.cassation.edits import load_edit
        docs = []
        for r in list_reports(runs_dir, traces_dir):
            if r["rtype"] != "run":
                continue
            d = runs_dir / r["id"]
            draft_edit, appeal_edit = load_edit(d, "draft"), load_edit(d, "appeal")
            docs.append({**r, "draft_edited": draft_edit["saved_at"][:10] if draft_edit else "",
                         "has_appeal": (d / "appeal.json").exists() or bool(appeal_edit),
                         "appeal_edited": appeal_edit["saved_at"][:10] if appeal_edit else ""})
        from legal_ai.web.casedocs import style_samples
        from legal_ai.web.casedocs import archive
        return render(request, "documents.html", {"docs": docs, "style_docs": style_samples(storage).items(),
                                                  "archive_docs": archive(storage).items()})

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    def trace_report(request: Request, trace_id: str, error: str = Query("", max_length=200)):
        t = load_trace(traces_dir, trace_id)
        if t is None:
            raise HTTPException(404, "Няма такава справка.")
        from legal_ai.cassation.labels import appellate_label
        t["appellate"]["label"] = appellate_label(t["appellate"]["label"], traces_dir / trace_id)
        return render(request, "trace.html", {"t": t, "trace_id": trace_id, "form_error": error,
                                              **case_context(traces_dir, trace_id, t["appellate"]["label"])})

    @app.post("/traces/{trace_id}/case")
    async def trace_case(request: Request, trace_id: str):
        if load_trace(traces_dir, trace_id) is None:
            raise HTTPException(404, "Няма такава справка.")
        return await save_case_form(request, traces_dir, trace_id, None, f"/traces/{trace_id}")

    return app
