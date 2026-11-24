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


def cmd_db_size(_args) -> int:
    """Where the database space goes: tables, their indexes and TOAST, the text columns (estimated
    from a 2 % sample), old text versions, dead rows and WAL. Read only."""
    from legal_ai.config import load_settings
    from legal_ai.db import connect

    conn = connect(load_settings().database_url)
    mb = 1024 * 1024

    def q(sql, *params):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - one unavailable figure must not hide the others
            conn.rollback()
            print(f"db-size: {type(exc).__name__}: {str(exc).splitlines()[0]}")
            return []

    conn.execute("SET statement_timeout = '180s'")
    conn.commit()
    for r in q("SELECT pg_database_size(current_database()) AS b"):
        print(f"db-size: database {r['b'] / mb:.0f} MB")
    for r in q("""SELECT c.relname AS t, pg_total_relation_size(c.oid) AS total, pg_relation_size(c.oid) AS heap,
                         pg_indexes_size(c.oid) AS idx,
                         COALESCE(pg_total_relation_size(NULLIF(c.reltoastrelid, 0)), 0) AS toast,
                         s.n_live_tup AS live, s.n_dead_tup AS dead
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                  WHERE n.nspname = 'public' AND c.relkind = 'r'
                  ORDER BY pg_total_relation_size(c.oid) DESC"""):
        print(f"db-size: table {r['t']}: total {r['total'] / mb:.0f} MB = rows {r['heap'] / mb:.0f} + "
              f"long text (TOAST) {r['toast'] / mb:.0f} + indexes {r['idx'] / mb:.0f}; "
              f"live rows {r['live']}, dead rows {r['dead']}")
    for r in q("""SELECT i.relname AS i, pg_relation_size(i.oid) AS b
                  FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
                  JOIN pg_namespace n ON n.oid = t.relnamespace
                  WHERE n.nspname = 'public' ORDER BY 2 DESC LIMIT 12"""):
        print(f"db-size: index {r['i']}: {r['b'] / mb:.0f} MB")
    for r in q("""SELECT count(*) AS n, sum(pg_column_size(exact_text)) AS exact, sum(pg_column_size(search_text)) AS search,
                         sum(pg_column_size(tsv)) AS tsv FROM passages TABLESAMPLE SYSTEM (2)"""):
        if r["n"]:
            print(f"db-size: passages 2% sample ({r['n']} rows) x50: exact_text {r['exact'] * 50 / mb:.0f} MB, "
                  f"search_text {r['search'] * 50 / mb:.0f} MB, tsv {r['tsv'] * 50 / mb:.0f} MB")
    for r in q("""SELECT count(*) AS n, sum(pg_column_size(canonical_text)) AS b
                  FROM decision_versions TABLESAMPLE SYSTEM (2)"""):
        if r["n"]:
            print(f"db-size: decision_versions 2% sample ({r['n']} rows) x50: canonical_text {r['b'] * 50 / mb:.0f} MB")
    for r in q("""SELECT count(*) AS versions, count(*) FILTER (WHERE d.current_version_id IS DISTINCT FROM v.id) AS old
                  FROM decision_versions v LEFT JOIN decisions d ON d.id = v.decision_id"""):
        print(f"db-size: text versions {r['versions']}, of them not current {r['old']}")
    for r in q("""SELECT count(*) AS n, count(*) FILTER (WHERE proceeding_article = '290') AS a290,
                         count(*) FILTER (WHERE proceeding_article = '288') AS a288,
                         count(*) FILTER (WHERE proceeding_article = '274') AS a274 FROM decisions"""):
        print(f"db-size: decisions {r['n']} (art. 290: {r['a290']}, 288: {r['a288']}, 274: {r['a274']})")
    for r in q("SELECT proceeding_article AS a, count(*) AS n FROM decisions GROUP BY 1 ORDER BY 2 DESC LIMIT 8"):
        print(f"db-size: decisions by article {r['a']}: {r['n']}")
    for r in q("SELECT sum(size) AS b, count(*) AS n FROM pg_ls_waldir()"):
        print(f"db-size: WAL {(r['b'] or 0) / mb:.0f} MB in {r['n']} files")
    for r in q("""SELECT temp_files, temp_bytes FROM pg_stat_database WHERE datname = current_database()"""):
        print(f"db-size: temp files so far {r['temp_files']}, {r['temp_bytes'] / mb:.0f} MB")
    conn.close()
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
    from legal_ai.sources.vks.urls import ACT_TYPES, COMMERCIAL_CHAMBERS

    settings = load_settings()
    root = settings.private_storage_path.resolve()
    quarters = quarters_between(_ym(args.start), _ym(args.end))
    if args.newest_first:
        quarters = list(reversed(quarters))
    client = VksClient(settings.source_min_interval_seconds, settings.source_user_agent)
    log = root / "raw" / "vks-corpus" / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    pause = log.parent / "PAUSE"
    try:
        for y, m1, m2 in quarters:
            for case_type in args.case_types.split(","):
                while pause.exists():  # lets other jobs use vks.bg alone; checked between quarters
                    time.sleep(20)
                slug = ("gr" if case_type == "гр." else "targ") + ("-opr" if args.act_type == "определение" else "")
                # commercial decisions are listed per chamber (AktVidDelo=търг. misses
                # decisions before 2023; docs/source-discovery.md 1.9)
                query_type, chambers = ((case_type, None) if case_type == "гр." else
                                        ("empty", COMMERCIAL_CHAMBERS))
                out = root / "raw" / "vks-corpus" / slug / f"{y}-{m1:02d}-{m2:02d}"
                done_flag = out / ".ingested"
                if done_flag.exists():
                    continue
                t0 = time.time()
                # determinations: only those mentioning чл. 288 (admission to cassation)
                report = crawl(client, out, (y, m1), (y, m2), act_type=ACT_TYPES[args.act_type],
                               case_type=query_type, chambers=chambers,
                               words="288" if args.act_type == "определение" else "")
                with connect(settings.database_url) as conn:
                    stats = ingest_raw_dir(conn, out, root, "direct",
                                           f"ВКС, {args.act_type}, {case_type}")
                ok = sum(1 for a in report.acts if a.get("ok"))
                entry = {"quarter": f"{y}-{m1:02d}..{m2:02d}", "case_type": case_type, "act_type": args.act_type,
                         "acts_ok": ok, "acts": len(report.acts), "ingested": stats.acts_ingested,
                         "truncated": report.truncated, "seconds": round(time.time() - t0)}
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                print(json.dumps(entry, ensure_ascii=False), flush=True)
                # an empty or still truncated quarter is not marked done, so it is re-checked
                if report.acts and ok == len(report.acts) and not report.truncated:
                    done_flag.write_text(entry["quarter"], encoding="utf-8")
    finally:
        client.close()
    return 0


def cmd_backfill_judges(_args) -> int:
    """Panel and reporter for acts ingested before they were parsed (from the stored text)."""
    from psycopg.types.json import Jsonb

    from legal_ai.config import load_settings
    from legal_ai.db import connect
    from legal_ai.judges import parse_judges

    settings = load_settings()
    done = 0
    with connect(settings.database_url) as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute("""SELECT d.id, v.canonical_text FROM decisions d
                               JOIN decision_versions v ON v.id = d.current_version_id
                               WHERE d.source = 'vks' AND d.panel IS NULL LIMIT 500""")
                rows = cur.fetchall()
                if not rows:
                    break
                for r in rows:
                    panel, reporter = parse_judges(r["canonical_text"])
                    cur.execute("UPDATE decisions SET panel = %s, reporter = %s WHERE id = %s",
                                (Jsonb(panel), reporter, r["id"]))
            conn.commit()
            done += len(rows)
            print(f"Съдии: {done} акта…", flush=True)
    print(f"Съдии: попълнени {done} акта", flush=True)
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

    from legal_ai.ai import load_ai_config, make_provider
    from legal_ai.cassation.pipeline import fetch_appellate, load_local, run_analysis, save_run
    from legal_ai.http import PoliteClient
    from legal_ai.sources.courts import ALLOWED_HOSTS
    from legal_ai.sources.vks import HOST as VKS_HOST

    interval = max(2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2")))
    ua = os.environ.get("SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)")
    out = Path(args.out or Path(os.environ.get("PRIVATE_STORAGE_PATH", "data")) / "runs")
    ai = make_provider(load_ai_config())
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
        if args.court and not args.file:
            from legal_ai.tracing import as_dicts, trace
            result.path = as_dicts(trace(courts, vks, args.court, args.case, args.year,
                                         appellate.act_date, result.analysis.get("lower_instance")))
    ai.close()
    run_dir = save_run(result, out)
    print(f"Готово: {run_dir / 'report.md'}")
    print(f"AI заявки: {result.usage['calls']}; токени {result.usage['input_tokens']}"
          f"/{result.usage['output_tokens']}")
    return 0


def cmd_reassess(args) -> int:
    """Re-run the saved assessment prompts of a run with another model and compare stances."""
    import json
    import os
    from concurrent.futures import ThreadPoolExecutor

    from legal_ai.ai import AIConfig, OpenAIProvider, runtime_env
    from legal_ai.cassation import prompts as P

    runs = Path(args.runs or Path(os.environ.get("PRIVATE_STORAGE_PATH", "data")) / "runs")
    run_dir = runs / args.run_id
    items = [json.loads(line) for line in (run_dir / "assess_inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    base = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    before = {(a["question_id"], a["source_id"]): a for a in base["assessments"]}
    ai = OpenAIProvider(AIConfig(args.model, args.model, max_calls=len(items) + 1, reasoning_effort=args.effort,
                                 **runtime_env()))

    def one(it):
        try:
            return it, ai.structured(model=args.model, system=P.SYSTEM_BASE, user=it["prompt"],
                                     schema_name="vks_assessment", schema=P.ASSESS_SCHEMA, effort=args.effort)
        except Exception as exc:  # noqa: BLE001
            return it, {"error": str(exc)}

    with ThreadPoolExecutor(max_workers=ai.config.workers) as pool:
        results = list(pool.map(one, items))
    same = 0
    for it, new in results:
        old = before.get((it["question_id"], it["key"]), {})
        o = f"{old.get('relevant')}/{old.get('stance')}"
        n = f"{new.get('relevant')}/{new.get('stance')}" if "error" not in new else new["error"][:60]
        same += int(o == n)
        print(f"{it['question_id']:4} {it['label'][:55]:55} {o:22} -> {n}")
    print(f"Съвпадение: {same}/{len(results)} · {args.model} токени {ai.usage.input_tokens}/{ai.usage.output_tokens}")
    out = run_dir / f"reassess-{args.model}-{args.effort}.json"
    out.write_text(json.dumps([{"item": {k: v for k, v in it.items() if k != "prompt"}, "result": new}
                               for it, new in results], ensure_ascii=False, indent=1), encoding="utf-8")
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
    sub.add_parser("db-size", help="Колко място заема всяка част от базата (само чете)").set_defaults(func=cmd_db_size)

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
    b.add_argument("--act-type", default="решение", choices=["решение", "определение"],
                   help="определение = определенията по чл. 288 ГПК (допуска / не допуска)")
    b.set_defaults(func=cmd_build_corpus)

    bj = sub.add_parser("backfill-judges", help="Състав и докладчик за вече заредените актове")
    bj.set_defaults(func=cmd_backfill_judges)

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
    a.add_argument("--court", choices=["as-plovdiv", "os-plovdiv", "rs-plovdiv"],
                   help="Съд на въззивното решение (за сваляне по номер)")
    a.add_argument("--case", type=int, help="Номер на въззивното дело")
    a.add_argument("--year", type=int, help="Година на въззивното дело")
    a.add_argument("--type", default="", choices=["", "Гражданско", "Търговско"])
    a.add_argument("--file", help="Или: локален файл с решението (PDF/HTML/TXT)")
    a.add_argument("--label", help="Название при --file")
    a.add_argument("--until", help="Практика на ВКС до ГГГГ-ММ (по подразбиране: 2 месеца след решението)")
    a.add_argument("--out", help="Папка за резултатите (по подразбиране data/runs)")
    a.set_defaults(func=cmd_analyze)

    ra = sub.add_parser("reassess", help="Същите оценки с друг модел (сравнение)")
    ra.add_argument("run_id")
    ra.add_argument("--model", required=True)
    ra.add_argument("--effort", default="medium")
    ra.add_argument("--runs")
    ra.set_defaults(func=cmd_reassess)

    v = sub.add_parser("serve", help="Стартирай уеб интерфейса")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
