"""Local web UI (read-only in this stage: no mutations, so no CSRF surface yet)."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.middleware.trustedhost import TrustedHostMiddleware

from legal_ai.config import load_settings
from legal_ai.db import connect
from legal_ai.retrieval.lexical import search
from legal_ai.retrieval.text import Term, normalize_for_search, parse_query, word_matches

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
            cur.execute("SELECT count(*) AS n FROM source_list_runs WHERE truncated")
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

    return app
