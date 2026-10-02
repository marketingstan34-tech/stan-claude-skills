# Legal AI Solo

Експеримент: инструмент за търсене на съдебна практика на ВКС за един адвокат. Работи само локално.

- Спецификация: [LEGAL_AI_SOLO_MASTER_SPEC.md](LEGAL_AI_SOLO_MASTER_SPEC.md)
- Правила за AI агентите: [AGENTS.md](AGENTS.md)
- Какво е проверено за източниците: [docs/source-discovery.md](docs/source-discovery.md)
- Отворени правни въпроси: [docs/data-rights.md](docs/data-rights.md)
- Тестови въпроси от адвоката: [evaluation/cases/README.md](evaluation/cases/README.md)

## Статус: етап 1 (база и търсене)

Работи:
- сваляне на списъци и решения от vks.bg месец по месец, с автоматично разделяне при отрязване на 249 резултата;
- разбор на решенията: вид, номер, дата, дело, отделение, производство по чл. 290 ГПК, основание за допускане по чл. 280, мотиви и диспозитив;
- база с непроменими версии на текста и пасажи с точни позиции;
- търсене по думи (с толерантност към окончанията) и филтър „само решения по чл. 290 ГПК“;
- уеб интерфейс: резултати с точни пасажи, отваряне в контекст, линк към оригинала.

Още няма: семантично търсене, AI анализ, касационен модул (етап 2), казуси и документи (етап 3).

**Правното качество не е валидирано.** Пълнотата на корпуса не е проверена.

## Стартиране с Docker (препоръчително)

Нужни: Docker Desktop.

```bash
cp .env.example .env        # сложете дълга произволна парола в POSTGRES_PASSWORD
docker compose up -d --build
```

Отворете http://127.0.0.1:8000. Приложението не е достъпно от други компютри.

Сваляне и зареждане на решения (пример: делба, 2025):

```bash
docker compose exec app legal-ai crawl-vks --from 2025-01 --to 2025-09 --words делба
docker compose exec app legal-ai ingest /data/raw/vks
```

Свалянето е бавно нарочно: една заявка на всеки 2 секунди, за да не натоварва сайта на ВКС.
Преди да се свали голям обем, вижте `docs/data-rights.md`.

## Стартиране без Docker (за разработка)

Нужни: Python 3.11+ и PostgreSQL 16.

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://legal_ai:ПАРОЛА@127.0.0.1:5432/legal_ai
export PRIVATE_STORAGE_PATH=../data
legal-ai migrate
legal-ai ingest ../data/raw/vks
legal-ai serve                     # http://127.0.0.1:8000
legal-ai search "възлагане на неподеляем имот чл. 349"
```

## Тестове

```bash
cd backend
pytest                                         # без база: интеграционните тестове се пропускат
TEST_DATABASE_URL=postgresql://... pytest      # с отделна тестова база, която може да се изтрива
```
