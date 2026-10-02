"""Command line: migrate, crawl-vks, ingest, search, serve."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ym(value: str) -> tuple[int, int]:
    y, m = value.split("-")
    return int(y), int(m)


def cmd_migrate(_args) -> int:
    from alembic import command
    from alembic.config import Config

    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    root = next((c for c in candidates if (c / "alembic.ini").exists()), None)
    if root is None:
        print("alembic.ini не е намерен. Стартирайте от папката backend/.", file=sys.stderr)
        return 2
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    print("Миграциите са приложени.")
    return 0


def cmd_check_db(_args) -> int:
    from legal_ai.config import load_settings
    from legal_ai.db import connect, describe_target

    settings = load_settings()
    print(f"База: {describe_target(settings.database_url)}")
    try:
        conn = connect(settings.database_url)
    except Exception as exc:  # noqa: BLE001 - report any connection failure plainly
        print(f"НЕУСПЕШНА ВРЪЗКА: {type(exc).__name__}: {exc}")
        return 1
    with conn, conn.cursor() as cur:
        cur.execute("SELECT version() AS v, current_setting('ssl', true) AS ssl_on")
        row = cur.fetchone()
        cur.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
        ssl_row = cur.fetchone()
        print(f"PostgreSQL: {row['v'].split(',')[0]}")
        print(f"Криптирана връзка (SSL): {'да' if ssl_row and ssl_row['ssl'] else 'не'}")
        cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL AS ok")
        if cur.fetchone()["ok"]:
            cur.execute("SELECT version_num FROM alembic_version")
            print(f"Миграции: {cur.fetchone()['version_num']}")
        else:
            print("Миграции: не са приложени (пуснете legal-ai migrate)")
        cur.execute("""SELECT count(*) FILTER (WHERE relrowsecurity) AS locked, count(*) AS total
                       FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relkind = 'r'
                         AND c.relname <> 'alembic_version'""")
        r = cur.fetchone()
        print(f"Таблици със защита (RLS): {r['locked']}/{r['total']}")
        cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        print(f"pgvector наличен: {'да' if cur.fetchone() else 'не'}")
    return 0


def cmd_crawl(args) -> int:
    from legal_ai.config import load_settings
    from legal_ai.sources.vks.crawler import VksClient, crawl

    settings = load_settings()
    out = Path(args.out or settings.private_storage_path / "raw" / "vks")
    client = VksClient(settings.source_min_interval_seconds, settings.source_user_agent)
    try:
        report = crawl(client, out, _ym(args.start), _ym(args.end), words=args.words,
                       act_type=args.act_type, case_type=args.case_type)
    finally:
        client.close()
    ok = sum(1 for a in report.acts if a.get("ok"))
    print(f"Списъци: {len(report.lists)}; актове: {ok}/{len(report.acts)} успешно; папка: {out}")
    for name in report.truncated:
        print(f"ВНИМАНИЕ: {name} е отрязан на 249 резултата и не е пълен.")
    return 0 if ok == len(report.acts) else 1


def cmd_build_corpus(args) -> int:
    """Quarter by quarter: crawl lists + acts (resumable), then ingest that quarter."""
    import json
    import time

    from legal_ai.config import load_settings
    from legal_ai.db import connect
    from legal_ai.ingestion.vks_ingest import ingest_raw_dir
    from legal_ai.sources.vks.crawler import VksClient, crawl, quarters_between

    settings = load_settings()
    root = settings.private_storage_path.resolve()
    quarters = quarters_between(_ym(args.start), _ym(args.end))
    if args.newest_first:
        quarters = list(reversed(quarters))
    client = VksClient(settings.source_min_interval_seconds, settings.source_user_agent)
    log = root / "raw" / "vks-corpus" / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        for y, m1, m2 in quarters:
            for case_type in args.case_types.split(","):
                slug = "gr" if case_type == "гр." else "targ"
                out = root / "raw" / "vks-corpus" / slug / f"{y}-{m1:02d}-{m2:02d}"
                done_flag = out / ".ingested"
                if done_flag.exists():
                    continue
                t0 = time.time()
                report = crawl(client, out, (y, m1), (y, m2), act_type="15", case_type=case_type)
                with connect(settings.database_url) as conn:
                    stats = ingest_raw_dir(conn, out, root, "direct", f"ВКС, решения, {case_type}")
                ok = sum(1 for a in report.acts if a.get("ok"))
                entry = {"quarter": f"{y}-{m1:02d}..{m2:02d}", "case_type": case_type,
                         "acts_ok": ok, "acts": len(report.acts), "ingested": stats.acts_ingested,
                         "truncated": report.truncated, "seconds": round(time.time() - t0)}
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                print(json.dumps(entry, ensure_ascii=False), flush=True)
                if ok == len(report.acts):
                    done_flag.write_text(entry["quarter"], encoding="utf-8")
    finally:
        client.close()
    return 0


def cmd_fetch_tr(args) -> int:
    from legal_ai.config import load_settings
    from legal_ai.db import connect
    from legal_ai.http import PoliteClient
    from legal_ai.ingestion.tr_ingest import ingest_tr
    from legal_ai.sources.vks import HOST
    from legal_ai.sources.vks.interpretive import download_all

    settings = load_settings()
    root = settings.private_storage_path.resolve()
    out = root / "raw" / "vks-tr"
    with PoliteClient([HOST], settings.source_min_interval_seconds, settings.source_user_agent,
                      max_bytes=30 * 1024 * 1024) as client:
        files, log = download_all(client, out, range(args.from_year, args.to_year + 1))
    ok = 0
    with connect(settings.database_url) as conn:
        for f in files:
            done, warnings = ingest_tr(conn, f, str(f.path.relative_to(root)))
            ok += int(done)
            if warnings:
                print(f"Предупреждение {f.college} {f.number}/{f.year}: {', '.join(warnings)}")
    print(f"Тълкувателни решения: свалени/налични {len(files)}, заредени {ok}")
    for line in log:
        print(f"Грешка: {line}")
    return 0


def cmd_ingest(args) -> int:
    from legal_ai.config import load_settings
    from legal_ai.db import connect
    from legal_ai.ingestion.vks_ingest import ingest_raw_dir

    settings = load_settings()
    raw = Path(args.raw_dir).resolve()
    storage_root = settings.private_storage_path.resolve()
    with connect(settings.database_url) as conn:
        stats = ingest_raw_dir(conn, raw, storage_root, args.acquisition, args.scope)
    print(f"Списъци: {stats.lists}; актове: {stats.acts_ingested}/{stats.acts_seen}; "
          f"нови версии: {stats.new_versions}")
    for name in stats.truncated_lists:
        print(f"ВНИМАНИЕ: списък {name} е отрязан на 249 резултата.")
    for s in stats.skipped:
        print(f"Пропуснат: {s}")
    for sid, w in stats.warnings.items():
        print(f"Предупреждение {sid}: {', '.join(w)}")
    return 0


def cmd_search(args) -> int:
    from legal_ai.config import load_settings
    from legal_ai.db import connect
    from legal_ai.retrieval.lexical import search

    settings = load_settings()
    with connect(settings.database_url) as conn:
        result = search(conn, args.query, only_290=args.only_290, limit=args.limit)
    print(f"Корпус: {result.corpus_size} акта; намерени: {len(result.decisions)}")
    if result.note:
        print(result.note)
    for i, d in enumerate(result.decisions, 1):
        date = d.act_date.strftime("%d.%m.%Y") if d.act_date else "?"
        print(f"\n{i}. {d.act_type} №{d.act_number}/{date} по дело №{d.case_number}/{d.case_year}"
              f" · {d.chamber or '?'} · чл.{d.proceeding_article or '?'}"
              f" · думи {d.terms_matched}/{len(result.terms)} · score {d.score:.3f}")
        print(f"   {d.canonical_url}")
        for p in d.passages[:2]:
            print(f"   [абзац {p.paragraph_no + 1}] {p.text[:300]}")
    return 0


def cmd_analyze(args) -> int:
    import os
    from datetime import date, timedelta

    from legal_ai.ai import OpenAIProvider, load_ai_config
    from legal_ai.cassation.pipeline import fetch_appellate, load_local, run_analysis, save_run
    from legal_ai.http import PoliteClient
    from legal_ai.sources.courts import ALLOWED_HOSTS
    from legal_ai.sources.vks import HOST as VKS_HOST

    interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
    ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
    out = Path(args.out or Path(os.environ.get("PRIVATE_STORAGE_PATH", "data")) / "runs")
    ai = OpenAIProvider(load_ai_config())
    with PoliteClient(ALLOWED_HOSTS, interval, ua, max_bytes=20 * 1024 * 1024) as courts, \
            PoliteClient([VKS_HOST], interval, ua) as vks:
        if args.file:
            appellate = load_local(Path(args.file), args.label or "")
        else:
            _, appellate = fetch_appellate(courts, args.court, args.case, args.year, args.type)
        print(f"Въззивно решение: {appellate.label} ({appellate.fmt}, {len(appellate.text)} знака)")
        if args.until:
            y, m = (int(x) for x in args.until.split("-"))
            cutoff = date(y, m, 28)
        elif appellate.act_date:
            cutoff = appellate.act_date + timedelta(days=60)
        else:
            cutoff = date.today()
        conn = None
        if os.environ.get("DATABASE_URL"):
            from legal_ai.db import connect
            conn = connect(os.environ["DATABASE_URL"])
            print("Собствена база: включена")
        try:
            result = run_analysis(ai, vks, appellate, cutoff, conn=conn)
        finally:
            if conn is not None:
                conn.close()
    ai.close()
    run_dir = save_run(result, out)
    print(f"Готово: {run_dir / 'report.md'}")
    print(f"AI заявки: {result.usage['calls']}; токени {result.usage['input_tokens']}"
          f"/{result.usage['output_tokens']}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from legal_ai.web.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legal-ai")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="Приложи миграциите").set_defaults(func=cmd_migrate)
    sub.add_parser("check-db", help="Провери връзката с базата").set_defaults(func=cmd_check_db)

    c = sub.add_parser("crawl-vks", help="Свали списъци и актове от vks.bg (директно)")
    c.add_argument("--from", dest="start", required=True, help="ГГГГ-ММ")
    c.add_argument("--to", dest="end", required=True, help="ГГГГ-ММ")
    c.add_argument("--words", default="", help="Думи в съдържанието, напр. делба")
    c.add_argument("--act-type", default="15", help="15 решение, 17 определение, 40 разпореждане")
    c.add_argument("--case-type", default="гр.", choices=["гр.", "нак.", "търг."])
    c.add_argument("--out", help="Папка за суровите страници")
    c.set_defaults(func=cmd_crawl)

    b = sub.add_parser("build-corpus", help="Сваляне и зареждане на решения на ВКС по тримесечия")
    b.add_argument("--from", dest="start", required=True, help="ГГГГ-ММ")
    b.add_argument("--to", dest="end", required=True, help="ГГГГ-ММ")
    b.add_argument("--case-types", default="гр.,търг.")
    b.add_argument("--newest-first", action="store_true")
    b.set_defaults(func=cmd_build_corpus)

    tr = sub.add_parser("fetch-tr", help="Тълкувателни решения на ОСГТК/ОСГК/ОСТК (PDF)")
    tr.add_argument("--from-year", type=int, default=2008)
    tr.add_argument("--to-year", type=int, default=2026)
    tr.set_defaults(func=cmd_fetch_tr)

    i = sub.add_parser("ingest", help="Зареди свалени страници в базата")
    i.add_argument("raw_dir")
    i.add_argument("--acquisition", default="direct", choices=["direct", "firecrawl", "manual"])
    i.add_argument("--scope", default="ВКС, граждански дела")
    i.set_defaults(func=cmd_ingest)

    s = sub.add_parser("search", help="Търсене от командния ред")
    s.add_argument("query")
    s.add_argument("--only-290", action="store_true")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("analyze", help="Касационен анализ на въззивно решение")
    a.add_argument("--court", choices=["as-plovdiv", "os-plovdiv"], help="Съд (за сваляне по номер)")
    a.add_argument("--case", type=int, help="Номер на въззивното дело")
    a.add_argument("--year", type=int, help="Година на въззивното дело")
    a.add_argument("--type", default="", choices=["", "Гражданско", "Търговско"])
    a.add_argument("--file", help="Или: локален файл с решението (PDF/HTML/TXT)")
    a.add_argument("--label", help="Название при --file")
    a.add_argument("--until", help="Практика на ВКС до ГГГГ-ММ (по подразбиране: 2 месеца след решението)")
    a.add_argument("--out", help="Папка за резултатите (по подразбиране data/runs)")
    a.set_defaults(func=cmd_analyze)

    v = sub.add_parser("serve", help="Стартирай уеб интерфейса")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
