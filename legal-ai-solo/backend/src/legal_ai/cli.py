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


def cmd_serve(args) -> int:
    import uvicorn

    from legal_ai.web.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legal-ai")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="Приложи миграциите").set_defaults(func=cmd_migrate)

    c = sub.add_parser("crawl-vks", help="Свали списъци и актове от vks.bg (директно)")
    c.add_argument("--from", dest="start", required=True, help="ГГГГ-ММ")
    c.add_argument("--to", dest="end", required=True, help="ГГГГ-ММ")
    c.add_argument("--words", default="", help="Думи в съдържанието, напр. делба")
    c.add_argument("--act-type", default="15", help="15 решение, 17 определение, 40 разпореждане")
    c.add_argument("--case-type", default="гр.", choices=["гр.", "нак.", "търг."])
    c.add_argument("--out", help="Папка за суровите страници")
    c.set_defaults(func=cmd_crawl)

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

    v = sub.add_parser("serve", help="Стартирай уеб интерфейса")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
