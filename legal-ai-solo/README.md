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

## Вариант: база данни в Supabase

Приложението работи на вашия компютър, а базата е в Supabase.

1. Създайте проект в Supabase с регион в ЕС (напр. Frankfurt).
2. От **Connect** вземете connection string за **Session pooler** (работи по IPv4).
   Не ползвайте Transaction pooler (порт 6543).
3. В `.env` добавете ред (с `postgresql+psycopg://` в началото):
   `DATABASE_URL=postgresql+psycopg://postgres.<проект>:<парола>@<хост>:5432/postgres`
4. Стартирайте: `docker compose -f docker-compose.supabase.yml up -d --build`

Миграциите включват защита (RLS) на всички таблици. Така данните не са достъпни през
публичния API на Supabase, а само през директната връзка на приложението.

Внимание: докато в базата са само публични решения на ВКС, рискът е малък. Преди да се качват
документи на клиенти (етап 3), трябва договор за обработка на данни (DPA) със Supabase и
съгласие от адвоката.

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

## Касационен анализ (етап 2)

Нужни: `OPENAI_API_KEY` и `AI_*` от `.env.example`, достъп до www.vks.bg и *.justice.bg.

```bash
cd backend
# по номер на дело (сваля решението от сайта на съда):
legal-ai analyze --court as-plovdiv --case 899 --year 2021 --type Търговско
# или от файл (PDF/HTML/TXT):
legal-ai analyze --file ~/Downloads/reshenie.pdf --label "АС Пловдив, в.т.д. ..."
```

Резултатът (`report.md`, `run.json`) е в `data/runs/<дата>/` — частна папка, не е в Git.
Всеки цитат е проверен дословно спрямо източника (✅) или е маркиран (⚠️).
Ограничения: търсенето е в сайта на ВКС по точни думи; тълкувателните решения още не са включени.

## Тестове

```bash
cd backend
pytest                                         # без база: интеграционните тестове се пропускат
TEST_DATABASE_URL=postgresql://... pytest      # с отделна тестова база, която може да се изтрива
```
