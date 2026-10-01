# LEGAL AI SOLO — MASTER SPEC

Версия 2.2 • 01.10.2026 • България • Частно ползване от един адвокат

## 1. Решение и цел

Първо изграждаме Solo инструмент за един адвокат и проверяваме дали изобщо работи. Ако доказано намира полезна практика и спестява време, тогава планираме отделна SaaS версия.

Това е актуалното решение и заменя предишната SaaS спецификация. Няма организации, subscriptions, Stripe, Firm планове, memberships, RLS, публична регистрация или enterprise infrastructure в този проект.

Основна задача:

> Адвокатът задава реален правен въпрос или описва казус → системата търси в предварително изграден разрешен корпус → връща релевантни актове и точни пасажи → показва аргументите, различията и ограниченията → адвокатът проверява резултата.

Не оптимизирай за убедително звучащ AI отговор. Оптимизирай за намиране на важните актове, проверими източници и полезност за адвоката.

Този документ е задание за разработка и експеримент. Няма вече измерени резултати, потвърдени права за масово събиране или доказана юридическа надеждност.

## 2. Какво трябва да докажем

1. Можем ли да получим достатъчно подходящи актове по разрешен и устойчив начин?
2. Намира ли системата ключовата практика по реални адвокатски въпроси?
3. Дава ли полезни точни пасажи, без измислени актове или цитати?
4. Правилно ли разграничава релевантните факти, процесуалния етап и изводите на съда?
5. Спестява ли време спрямо сегашния процес на адвоката с Апис, Сиела или други инструменти?
6. Какви са действителният AI разход и времето за едно проучване?

Само работещ UI и генериран доклад не доказват успех. Основният резултат е адвокатски benchmark с реални въпроси.

## 3. Обхват на първия експеримент

### Включено

- Локално приложение за един адвокат, без публичен достъп.
- Правен въпрос, описание на казуса, представлявана позиция и процесуален етап.
- Ограничен предварително индексиран корпус от актове на ВКС по избрана област.
- Discovery на ВКС и legalacts.justice.bg, включително условия за събиране и повторно използване.
- Hybrid search: lexical + semantic, точни references и metadata filters.
- Класиране на актове по конкретния въпрос и факти.
- Пълен текст, оригинален URL, идентичност, версия и точни пасажи.
- Нормативни текстове и приложими редакции от разрешен източник или лиценз.
- Кратък research memo с източници, несигурност и покритие.
- Markdown export, feedback от адвоката и измерване на разходите.
- Ранен тест с 3–5 адвокати, по около 10 реални въпроса.

### След първия успешен search benchmark

- Запазени казуси и document upload: PDF с текстов слой, DOCX, TXT.
- Извличане на факти с provenance и човешки преглед.
- Прост анализ на подкрепяща, противоречаща и различима практика.
- Локален OCR за ограничени scan документи само ако е необходим за реалните тестове.
- ВАС, ако избраните казуси изискват административна практика.

### Извън Solo release

SaaS, accounts за множество потребители, organizations, subscriptions, Stripe, RLS, team permissions, mobile app, CRM, автоматично подаване в съд, автоматични срокове, процент за спечелване на дело, пълно генериране на процесуални документи, monitoring, knowledge graph, визуален Argument Map и Strategy Engine.

Не добавяй тези модули като „основа за бъдещето“. За бъдещ SaaS пазим нормални interfaces и ясни модели, без предварително изграждане на SaaS infrastructure.

## 4. Архитектура

Модулен монолит, една PostgreSQL база и един Python worker. Без Redis, Celery, n8n или микросървиси.

```text
Локален оператор → FastAPI + Jinja/HTMX → PostgreSQL + pgvector
                           │                        │
                           └→ private storage       └→ jobs → Python worker
```

| Компонент | Избор |
|---|---|
| UI | Jinja templates + HTMX, сервирани от FastAPI |
| API | FastAPI + Pydantic, същият origin като UI |
| Database | PostgreSQL + pgvector |
| Миграции | SQLAlchemy + Alembic |
| Jobs | PostgreSQL jobs table; worker със SKIP LOCKED |
| Storage | Частна локална директория чрез StorageAdapter |
| AI | Provider interface; начално OpenAI, configurable models |
| OCR | Опционален локален Tesseract bul+eng |
| Стартиране | Docker Compose: app, worker, postgres |

Phase A–C използват един Python application build. Няма Next.js, TypeScript build, отделен frontend server или cross-origin UI/API. HTMX се доставя локално със заключена версия. Евентуален нов frontend за SaaS се решава в отделното бъдещо задание.

Заключи проверени dependency versions и lockfiles при bootstrap. Моделът за разработка в Codex/Claude Code е отделен от API моделите вътре в приложението. Не приемай името на coding model за валиден API model ID.

## 5. Източници: първият gate

Входни точки за discovery:

- ВКС: https://www.vks.bg/search.html
- Тълкувателна практика на ВКС: разделите на съответните колегии през https://www.vks.bg/
- Централен портал за съдебни актове, който трябва да се провери: https://legalacts.justice.bg/
- ВАС за следващ етап: https://sac.justice.bg/spravki/

Наличието на публичен сайт не означава разрешение за масово сваляне, съхранение или повторно използване. В тази спецификация няма потвърден API, export endpoint или готово правно основание за конкретен corpus.

### 5.1 Задължителни discovery резултати

За всеки използван source запиши:

1. Реални endpoints, parameters, encoding, pagination и ограничения, установени с реални заявки.
2. Условия за достъп/повторно използване, robots указания и известни ограничения. Robots не е лиценз.
3. Наличен bulk export, разрешен API, договорен достъп или друг устойчив способ.
4. Действително покритие по съд, период, вид акт и област; няма предполагаема пълнота.
5. Реален flow: query/list → metadata → пълен акт.
6. Формати, charset, сканирани файлове, anonymization и parser failures.
7. Допустимото съхранение на raw/text artifacts и ограничения за изнасяне.
8. Решение: разрешен ingestion; ограничен manual import; или blocker.

Discovery docs трябва да различават технически факти от правна интерпретация. При неяснота, която пречи на планираното събиране, получи подходящ правен/договорен преглед преди bulk ingestion. Не прави категорично правно заключение само с LLM.

Не заобикаляй CAPTCHA, вход, ограничения или достъп с КЕП. Не автоматизирай Апис/Сиела без разрешение. Адвокатът може да сравнява резултатите през собствения си разрешен достъп.

### 5.2 Собствен индекс

Основният research flow работи върху предварително изграден локален индекс. Не изпълнявай 30 live търсения и download на всички candidates при всяко проучване.

Първоначална цел: няколко хиляди акта в избрана област, ако са достъпни при подходящи условия. Бройката не е критерий за достатъчно покритие: измери кои gold актове действително присъстват.

Избери една начална област с адвоката, например вещно право/съдебна делба. Не се опитвай да покриеш цялото българско право от първия ден.

Corpus manifest: sources, boundaries, court, date range, acquisition method, permissions notes, documents count, full-text count, parse failures, last update и known gaps.

Ако gold актове липсват, отчитай corpus coverage отделно от retrieval quality. Намерен акт извън корпуса може да се добави след разрешен fetch/manual import; това не поправя със задна дата първоначалния benchmark.

Live search е optional discovery/refresh fallback, означен отделно. Не маскирай source outage като липса на практика.

### 5.3 Ingestion contract

```python
class LegalSource(Protocol):
    async def capabilities(self) -> SourceCapabilities: ...
    async def list_records(self, scope, cursor=None) -> RecordPage: ...
    async def fetch(self, reference) -> RawArtifact: ...
```

list_records се реализира само когато реалният source позволява тази операция. Manual import adapter е валиден fallback, но се показва като manual.

Parser е отделен чист компонент. Пази оригинален URL, retrieved_at, MIME/encoding, bytes hash, parser_version, metadata и warnings. Пълен текст се различава от snippet/metadata_only.

Rate limits се определят от source constraints. Начално conservative concurrency=1 и minimum interval=2 секунди, само ако това е съвместимо с разрешения достъп. Retry за transient failure до 3 опита, Retry-After, backoff, size/timeout limits и checkpoints. Не прави retry loop за CAPTCHA/забрана.

## 6. Нормативна база

Текстът на закона и релевантната му редакция са част от първата използваема версия за избраната област, не бъдещ SaaS модул.

При discovery избери разрешен official source, licensed dataset или договорен достъп. Не предполагай, че има готов свободен API за консолидирани исторически редакции.

### Phase B — малък проверен нормативен набор

Правило за първия експеримент: текуща проверена редакция към изрично посочена дата на 2–3 закона за избраната област. За делба предложението е ГПК, ЗС и ЗН; необходимите разпоредби се избират с адвоката. Само разрешен източник/лиценз. Не изграждай автоматична история на измененията в Phase A–B.

Пази act identity, provision reference, exact text, source URL, hash, checked_at, edition_as_of, publication reference когато е установена, reviewer и verification status. Ръчната бележка за датата описва проверката; не доказва сама по себе си приложимост към минали факти.

За historical question показвай temporal_applicability=unknown, освен ако необходимата редакция е проверена отделно от адвоката. Не представяй текущ текст като приложим към старите факти. Memo остава incomplete за въпрос, който изисква непроверена историческа редакция.

### Phase C — исторически редакции само при доказана нужда

След search gate и според реалните задачи: добави проверени historical versions, effective_from/effective_to и amendment provenance чрез отделна миграция. Не смесвай publication date и effective date. Реконструирана редакция е отделно означена и адвокатски проверена. Пълна нормативна база не е обещана.

LLM паметта не е source на нормативен текст.

## 7. Repository

```text
legal-ai-solo/
  LEGAL_AI_SOLO_MASTER_SPEC.md
  README.md
  AGENTS.md
  .env.example
  docker-compose.yml
  backend/
    src/legal_ai/
      api/
      templates/
      static/
      db/
      sources/
      ingestion/
      legislation/
      retrieval/
      ai/providers/
      ai/schemas/
      ai/prompts/
      research/
      documents/
      jobs/
      storage/
    migrations/
    tests/
  evaluation/
    cases/
    corpus_manifests/
    results/
  docs/
    source-discovery.md
    data-rights.md
    evaluation.md
    decisions/
  data/                      # private, gitignored
```

API contracts идват от Pydantic/OpenAPI; UI използва server-rendered templates и HTMX partials. Няма TypeScript type generation или отделен frontend build.

## 8. Минимални модели

Всички persistent IDs са UUID. Timestamps са UTC, UI показва Europe/Sofia; юридическите дати са DATE. Versions са immutable.

### Phase A–B — създавай само тези таблици

| Таблица | Основни полета |
|---|---|
| corpus_snapshots | id, scope, manifest, created_at |
| source_artifacts | id, source, URL, retrieved_at, MIME, sha256, storage_key, parser_version, warnings |
| decisions | id, source_record_id nullable, court, chamber nullable, act_type, act_number nullable, act_date nullable, case_type/number/year nullable, canonical_url |
| decision_versions | id, decision_id, artifact_id, metadata, canonical_text_key, text_hash, parsed_at |
| passages | id, decision_version_id, section, paragraph_no, page_no nullable, offsets, exact_text, tsvector |
| corpus_members | corpus_snapshot_id, decision_version_id |
| embeddings | id, passage_id, model, dimension, content_hash, vector |
| legal_acts | id, title, identity, source |
| provision_versions | id, legal_act_id, provision_ref, exact_text, source_url, hash, publication_ref nullable, edition_as_of, checked_at, reviewer, verification_status |
| research_runs | id, query_input, corpus_snapshot_id, status, config, budget, coverage, costs, started_at, finished_at |
| research_issues | id, run_id FK, question, thesis nullable, keywords JSONB, references JSONB, assumptions JSONB |
| research_results | id, run_id, issue_id, decision_version_id, ranks, relevance_reason, stance, differences |
| citations | id, run_id, passage_id nullable, provision_version_id nullable, offsets, quote, text_status, interpretation_status; CHECK: точно едно от passage_id/provision_version_id |
| reports | id, run_id, version, structured_content, markdown_key, review_status |
| feedback | id, run_id, result_id nullable, rating, useful, missed_reference, notes, created_at |
| jobs | id, type, payload, status, attempts, available_at, lease_until, idempotency_key, error_code |
| ai_calls | id, run_id nullable, task, provider/model, prompt/schema_version, usage, cost_estimate, latency, status |

### Phase C — не създавай миграции преди search gate

След успешна Phase B добави следните таблици, nullable FK research_runs.case_revision_id и nullable FK citations.document_block_id; CHECK на citations се разширява до точно едно от passage_id/provision_version_id/document_block_id. Добави към provision_versions историческите effective dates/provenance само ако задачите го изискват. Phase A–B application code не трябва да зависи от тези несъздадени таблици.

| Таблица | Основни полета |
|---|---|
| cases | id, title, current_revision_id, created_at |
| case_revisions | id, case_id, revision_no, description, position, procedural_stage, relevant_dates, research_as_of |
| documents | id, case_id, original_name, MIME, size, sha256, storage_key, status |
| document_versions | id, document_id, extraction_version, text_hash, canonical_text_key, quality_flags |
| document_blocks | id, document_version_id, paragraph_no, page_no nullable, offsets, text |
| facts | id, case_revision_id, statement, epistemic_status, dispute_status, review_status |
| fact_evidence | fact_id, document_block_id nullable, input_field nullable, quote, offsets, relation |

research_results.issue_id е FK към research_issues.id. Issue трябва да принадлежи на същия run като result; enforce чрез composite FK/unique constraint. QueryAnalysisV1 се записва като research_issues преди ranking.

Constraints: unique revision numbers, hashes/version identities, idempotency keys и result per issue/decision version. Citation сочи точно един source type. Reference само по номер на решение не е уникална идентичност; използвай съд, вид, дата, дело и source identity.

Изтриването на казус премахва неговите private documents/results, но не общия публичен corpus. Query-only flow не изисква documents или предварително AI extraction.

Не добавяй users.password_hash, organization_id, tenants или memberships за локалния експеримент. Бъдещата SaaS миграция ще има отделен проектен gate.

## 9. Retrieval

### Candidate generation

1. Нормализирай въпроса, запази оригинала.
2. Извлечи до 3–8 съществени подвъпроса, keywords, exact references и фактически ограничения.
3. Генерирай lexical candidates и vector candidates от corpus snapshot.
4. Обедини ги чрез Reciprocal Rank Fusion; baseline k=60 е configurable.
5. Aggregate passage hits до distinct decisions; не запълвай top 10 с десет пасажа от един акт.
6. Rerank ограничен набор, например до 40 candidates, по конкретния въпрос/факти.
7. Покажи top 10–30 според резултатите; не добавяй нерелевантни актове за бройка.

Postgres FTS baseline: simple configuration, точни фрази/references и поддържан юридически lexicon. Не предполага наличен добър български stemmer. Embeddings използват еднакъв model/dimension за query и corpus; смяна изисква reindex.

В Phase B сравни поне два embedding модела върху еднакъв frozen corpus, chunking и development questions. Пази embeddings в отделни model/dimension пространства; никога не сравнявай vectors от различни модели. Избери кандидатите след проверка на наличност, условия за данните и цена. Сравни с lexical-only baseline по recall, critical misses, precision, latency, indexing cost и run cost. Избери конфигурацията само по development set; провери я веднъж върху holdout. Не избирай модел от впечатление за българския му език.

Филтри: court, chamber, act type, date range, case type, provision references, interpretative act. Source-supported metadata и inferred metadata се различават. Date cutoff не доказва temporal applicability.

### Rerank

Reason: сходен правен въпрос, материални факти, процесуален етап, действително разрешен въпрос и проверен пасаж. Ranking score не е вероятност за успех в съда.

Тествай lexical-only срещу hybrid срещу hybrid+rerank. По-сложният вариант се запазва само ако носи измеримо подобрение спрямо допълнителния разход.

## 10. Текстове, цитати и кратък анализ

Всеки показан акт има metadata, оригинален URL, immutable version и пълен текст или explicit metadata_only status.

Citation: source_version_id, passage/block/provision ID, offsets [start,end), exact_quote, source URL и verification status. Offsets са Unicode code points в canonical text. Highlight се рендерира server-side по offsets; тествай, че показаният фрагмент съвпада точно с exact_quote.

Deterministic verification:

- source version и hash съществуват;
- exact quote съвпада с посочения текст;
- identity и anchor са правилни;
- цитатът не идва от snippet;
- цитатът принадлежи към текущия run context.

Моделът няма право да поправя цитати, да измисля URLs, act numbers или text. При многоточия използвай отделни spans. Whitespace normalization е документирана; съществена разлика означава invalid.

Text verification не доказва правна интерпретация. В първия release няма отделен многoетапен Entailment Engine: краткият анализ има passage evidence, видими assumptions и адвокатски review. Отделна автоматизирана entailment проверка може да се добави само ако benchmark покаже нужда.

Разграничавай страна/предходна инстанция/разглеждан съд, admissibility/merits и факти/правни изводи.

Stance е спрямо конкретен issue и thesis: supporting, contrary, distinguishable, mixed, neutral, unknown. Не класифицирай акт завинаги като „за“ или „против“.

Не заявявай „трайна“, „задължителна“, „последна“ или „отменена“ практика без проверена опора. Цитиран друг акт остава unresolved, докато не се получи неговият текст.

## 11. AI договори и бюджет

```python
class AIProvider(Protocol):
    async def structured(self, task, messages, schema, limits) -> AIResult: ...
    async def embed(self, texts, model) -> EmbeddingResult: ...
```

Structured schemas:

- QueryAnalysisV1: issues, keywords, references, filters, assumptions.
- RerankV1: allowed decision IDs, rank, relevance_reason, differences.
- DecisionSummaryV1: issue_id, allowed decision_version_id, stance, claims, passage IDs, limitations.
- ResearchMemoV1: findings, allowed citation IDs, contrary_findings, missing_information, coverage, limitations.
- FactExtractionV1 за втория етап: facts, evidence anchors, contradictions, missing_information.

Pydantic schemas: additionalProperties=false, bounded fields, explicit unknowns. Валидирай referenced IDs срещу разрешения context, не само JSON shape. До два schema repair опита; после явна failure.

Prompt/schema versions се пазят. Provider/model се конфигурират с валидни API IDs; няма автоматично предположение кой модел е най-добър по българско право.

Private data provider fallback е изключен. Retrieved text е недоверено съдържание, не инструкции. Моделът няма неограничени инструменти/network/file access.

Измервай отделно еднократен corpus embedding разход и per-research разход. Всеки run има cap преди скъпи операции. Cost estimate използва versioned rates; unknown rate не е нулев разход. Няма обещан фиксиран cost/case преди реални тестове.

## 12. Документи и факти — след search gate

Първо query-only retrieval. Не строим сложен Document Engine, преди да знаем дали практиката се намира.

След успешен първи benchmark: PDF text layer, DOCX, TXT; configurable size/page limits и MIME/magic bytes checks. Не обработвай макроси, активен код или неограничени compressed documents.

Пази оригинала и immutable extracted version. DOCX няма надежден page number; използвай paragraph/table anchors. OCR е optional, локален и показва quality warnings. OCR quote е text-matched спрямо извлечения текст, не „проверен спрямо изображението“, без визуален review.

Facts:

- epistemic_status: asserted, inferred, unknown;
- dispute_status: disputed, undisputed, unknown;
- review_status: proposed, lawyer_confirmed, rejected.

Потвърдено извличане не означава установен от съда факт. Contradictions се пазят с evidence. Редакция създава case revision и старите runs остават към предишната версия.

## 13. Research memo

Кратка структура:

1. Въпрос, казус/позиция и дата.
2. Намерени ключови актове и защо са релевантни.
3. Точни пасажи с оригинални линкове.
4. Противна практика и съществени различия.
5. Проверени релевантни нормативни текстове и версии.
6. Липсващи факти, unresolved references и несигурни изводи.
7. Corpus coverage, filters, source failures, missing full text и budget limits.
8. Статус: draft / lawyer_reviewed.

AI произвежда validated JSON; backend render-ва Markdown детерминистично. Invalid citations не влизат като потвърдени findings. Empty corpus/source failure не се компенсира с отговор от паметта на модела.

## 14. Интерфейс и API

Първи UI: search field, optional case context, filters, results list, source viewer с highlight, кратък memo и feedback.

Втори UI: моите казуси, документи, факти и research history. Български labels, keyboard navigation, readable typography. Diagnostics и technical scores не затрупват основния workflow.

Състояния: няма резултати; corpus не покрива темата; източникът е unavailable; metadata_only; budget exhausted; нормативна редакция unknown; analysis failed. Те са различни.

API prefix /api/v1:

| Route | Действие |
|---|---|
| POST /search | Query-only research run; връща 202 + run ID |
| GET /runs/{id} | State, progress, coverage, cost |
| GET /runs/{id}/results | Ranked practice |
| GET /runs/{id}/memo | Validated memo |
| POST /runs/{id}/cancel | Cancel |
| POST /runs/{id}/feedback | Lawyer feedback |
| GET /decisions/{id} | Metadata/versions |
| GET /versions/{id}/text | Anchored full text |
| GET /citations/{id} | Quote/context/verification |
| GET /corpus | Manifest/coverage |
| GET /provisions/{id} | Verified provision version |
| GET/POST /cases | Втори етап: case list/create |
| POST /cases/{id}/documents | Втори етап: upload |
| POST /cases/{id}/facts | Втори етап: extraction |
| GET /runs/{id}/export | Markdown |
| GET /health | Service health без private content |

Errors имат code, message_bg, request_id и retryable. POST research приема idempotency key. Няма login/password/organization/billing endpoints в локалния Solo проект.

## 15. Jobs

PostgreSQL е единственият persistent queue backend. Worker claim-ва jobs чрез транзакция и SELECT FOR UPDATE SKIP LOCKED, задава lease и commit-ва claim преди дългата работа. Не държи row lock по време на source/AI request.

States: queued, running, completed, partial, failed, cancelled. Expired lease позволява retry; attempts са ограничени. Idempotency и checkpoints предотвратяват duplicate artifacts при at-least-once execution.

Worker проверява cancel между work units. При delete на казус блокирай нови writes и cancelled jobs. UI polling с backoff е достатъчен; няма нужда от отделна real-time infrastructure.

## 16. Минимална сигурност

Solo се изпълнява само на доверената локална машина. UI/API са same-origin на 127.0.0.1:8000; Compose публикува само този loopback порт. Postgres няма публикуван порт. Проверявай Host/Origin allowlist и CSRF за mutations, включително HTMX requests. Не излагай приложението чрез публичен tunnel като страничен ефект.

Файловете са извън public directory, random storage keys, restrictive permissions; секрети само в environment. Използвай защитен host/disk за клиентски документи. Няма private content, raw prompts, ЕГН, credentials или API keys в Git/logs.

SSRF защита за source fetching: allowlisted domains/redirects, забрана за private/loopback/metadata addresses. Sanitize source HTML; никакви scripts в preview.

Преди реални документи собственикът проверява privacy/retention условията на избрания AI provider. Публичните source queries са обезличени. Delete премахва private documents, text, embeddings, runs и exports; backup retention се описва честно.

Backup/restore за локалните DB и files се документира и проверява. Remote access, multi-user auth и tenant isolation изискват нов scope; не са скрито включени тук.

## 17. Evaluation: рано, не накрая

### 17.0 Как участват външните адвокати

Phase A–B evaluation е операторски, а не multi-user deployment. Адвокатите дават разрешени обезличени въпроси и gold references; локалният оператор ги изпълнява. Прегледът е присъствен или чрез разрешено споделяне на екрана само на съответните резултати. Не се дава URL, remote control, login, tunnel или достъп до corpus/private files. Export се споделя само при изрично разрешение и след проверка за поверителни данни; разработката не изпраща автоматично съобщения/файлове.

Запиши operator-assisted времето отделно от времето на baseline. Този тест измерва retrieval/usefulness, не self-service usability. Phase C проверява самостоятелно локално използване от един адвокат на доверената машина или изолиран локален setup с разрешени данни.

Не предполагай, че собственикът е адвокат. Преди lawyer benchmark трябва поне един адвокат-партньор за избор на област, петте development въпроса и gold review. Докато липсва, може да върви technical discovery, но юридическият gate остава непокрит.

### 17.1 Dataset

Започни с 5 development questions от един адвокат още при source discovery. След първия работещ search използвай 3–5 адвокати с около 10 реални въпроса всеки. Данните трябва да са разрешени и обезличени.

Отдели development и holdout questions. Не настройвай retrieval спрямо holdout. За всеки въпрос:

- case context, issue, expected corpus area;
- lawyer-known relevant decisions и critical decisions;
- gold passages и relevance 0–3;
- baseline search time/process/results;
- source/corpus availability;
- reviewer/date и uncertainty.

Gold set е непълен. Нов намерен полезен акт се оценява от адвокат, не автоматично като false positive. Synthetic fixtures тестват behavior, не юридическа полезност.

### 17.2 Сравнение

Адвокатът изпълнява същата задача с текущия си разрешен инструмент и с Solo. Записвай:

- ключови намерени/пропуснати актове;
- точност и полезност на top results;
- време до първи полезен акт;
- общо време за research + проверка;
- правни интерпретации, които е поправил;
- дали би използвал продукта отново и за какъв тип задача.

Където е възможно, blind review на result relevance и разменен ред на инструментите намаляват bias. Не твърди превъзходство над Апис/Сиела без това сравнение.

### 17.3 Метрики

- Corpus coverage: налични gold decisions / известни gold decisions.
- Corpus-conditional Recall@30: намерени relevant gold / gold available in snapshot.
- End-to-end Recall@30: намерени gold / всички известни gold, включително извън corpus.
- Critical misses: count и анализ на всеки.
- Precision@10 с явно посочен denominator при кратки lists.
- Exact quote validity, fabricated identity count и interpretation errors.
- Search/verification time, cost/run, latency, source failures.

Разделяй source/corpus miss от ranking miss. Покажи per-case counts и примери, не само среден процент.

### 17.4 Gates

Няма предварително обещан Recall ≥ 0.85. Първо установяваме baseline; адвокатът задава праг за следващата итерация преди holdout оценката.

Твърди минимални gates:

- нула fabricated acts/URLs в confirmed findings;
- нула invalid exact quotes, представени като verified;
- всички source/corpus/budget limitations са видими;
- полезността и интерпретацията са прегледани от адвокат;
- критичните misses са описани и не се скриват.

Продължаваме към по-пълен Solo, ако резултатите по избраната област показват полезен retrieval и нетно спестено време за реалните адвокати. Ако не — поправяме data coverage/retrieval или спираме разширението. Няма автоматично преминаване към SaaS.

Статус legal quality unvalidated остава, докато липсва този review.

## 18. Тестове

Unit: parser, normalization, act identity, dedup, offsets, citation validator, schemas, fusion и budget.

Integration: migrations, pgvector, PostgreSQL queue claim/retry/cancel, storage, delete и API contracts.

End-to-end: query → ranked acts → original passage → memo → export; отделно empty corpus, source failure и invalid quote.

Adversarial: fake act number, wrong court/date, snippet-only, conflicting source context, instructions in document, OCR error, unknown normative version, admissibility misrepresented as merits.

CI ползва public-source fixtures, когато съхранението им е допустимо, и deterministic AI fixtures. Live source/API tests са opt-in с cap и ограничения. Mock success не доказва live success.

## 19. Environment и старт

Compose: app (FastAPI UI/API), worker, postgres. Local private volumes. Без Redis/Celery и отделен web container.

Задължителна port конфигурация:

```yaml
services:
  app:
    ports:
      - "127.0.0.1:8000:8000"
  worker:
    # no published ports
  postgres:
    # no published ports; only internal Compose network
```

Това е port fragment, не пълен Compose файл. Не използвай "8000:8000" или публикуван Postgres port. FastAPI може да слуша 0.0.0.0:8000 вътре в контейнера; host mapping остава loopback. Ако някога бъде отделен frontend, неговият единствен допустим local mapping е "127.0.0.1:3000:3000"; той не се създава в този scope. Smoke check потвърждава реалните host bindings и липсата на DB publication.

.env.example:

```dotenv
APP_ENV=development
APP_TIMEZONE=Europe/Sofia
APP_ORIGIN=http://127.0.0.1:8000
ALLOWED_HOSTS=127.0.0.1,localhost
DATABASE_URL=postgresql+psycopg://legal_ai:REPLACE_ME@postgres:5432/legal_ai
PRIVATE_STORAGE_PATH=/data/private
AI_PROVIDER=openai
AI_QUERY_MODEL=SET_VALID_API_MODEL_ID
AI_ANALYSIS_MODEL=SET_VALID_API_MODEL_ID
AI_EMBEDDING_MODEL=SET_VALID_API_MODEL_ID
AI_EMBEDDING_DIMENSION=SET_MATCHING_DIMENSION
OPENAI_API_KEY=
PRIVATE_PROVIDER_FALLBACK_ENABLED=false
SOURCE_INGESTION_ENABLED=false
SOURCE_MAX_CONCURRENCY=1
SOURCE_MIN_INTERVAL_SECONDS=2
RESEARCH_COST_CAP=SET_LOCAL_CAP
OCR_ENABLED=false
LOG_LEVEL=INFO
```

Placeholder config се валидира. Ingestion е изключен до discovery gate. Disabled integration показва unavailable, не dummy output. README документира setup, migrations, ingest, index, dev, test, evaluate, backup/restore и costs.

## 20. План

### Phase A — Проверка на данните и baseline

Цел: да отговорим дали имаме разрешен и полезен corpus.

- Потвърди адвокат-партньор и избери начална област с него.
- Discovery на ВКС/legalacts и нормативните източници.
- 5 реални въпроса и lawyer-known acts.
- Ограничен разрешен ingestion, corpus manifest и parser fixtures.
- Lexical baseline върху frozen snapshot.

Gate: corpus може да се получава/ползва и съдържа достатъчно релевантна практика за тестовете. Ако не — документирай blocker; не строи останалото на фиктивни данни.

### Phase B — Search experiment

- Hybrid search, сравнение на поне два embedding модела с lexical baseline, ограничен rerank, source viewer и verified quotations.
- Кратък memo с проверени текущи нормативни excerpts, дата на редакцията и explicit historical limitations.
- Early lawyer evaluation; compare baseline и current workflow.

Планов ориентир за Phase A+B: 4–6 седмици за един човек с AI помощ при наличен адвокат-партньор и достижим data access. Това е допускане, не проверена оценка или срок. Правен/договорен преглед, разрешения и липсващи данни могат да го удължат. След Phase A направи нова оценка по реалните blockers.

Gate: реални counts, адвокатски feedback и measured usefulness; решение дали продължаваме.

### Phase C — Solo за ежедневна проба

Само след Phase B: cases, document input, facts/revisions, contrary practice, costs, delete/restore и usability. ВАС/OCR само според реалните задачи. Проверка на 20–50 въпроса/казуса според наличните reviewers.

Gate: един адвокат може да използва продукта самостоятелно за избрания workflow и да провери всичко съществено.

### Phase D — Решение за SaaS

Не имплементирай SaaS в този scope. Подготви отделен readiness report: качество, corpus rights, usage costs, lawyer demand, blockers и вероятна SaaS миграция. Само след изрично ново решение се планират organizations/auth/tenancy/billing/deployment.

## 21. Acceptance case: съдебна делба

Synthetic behavior fixture: двама сънаследници с твърдени квоти по 1/2; единият обитава жилището и иска да проучи възлагане вместо публична продан; има спор по предпоставките и липсваща информация за поделяемостта/оценката/датите.

Това не е правен отговор. Системата трябва:

1. Да формулира подвъпросите и missing facts, без да предполага всички предпоставки.
2. Да използва чл. 349 ГПК като search reference и да покаже проверената необходима редакция, ако е налична.
3. Да намери реални актове от corpus и точни пасажи, включително неблагоприятни.
4. Да различи процесуалната фаза и материалните факти.
5. Да показва corpus gaps и липсваща нормативна версия.
6. Да не обещава възлагане, срок или процент за успех.

Реален quality acceptance изисква разрешен обезличен казус и gold decisions/passages от адвокат. Не вписвай измислени номера на решения в теста.

## 22. Definition of Done за Solo

- [ ] Source/data-rights discovery има реални доказателства и known limitations.
- [ ] Разрешен corpus и manifest, immutable source versions и parser checks.
- [ ] Нормативни текстове/версии за избрания workflow или explicit incomplete-research blocker.
- [ ] Lexical/hybrid/rerank сравнение върху frozen corpus.
- [ ] Query-only flow работи end-to-end с реални актове.
- [ ] Verified quotations отварят правилния оригинален context.
- [ ] Memo не съдържа fabricated confirmed claims.
- [ ] Source/corpus failures и budgets са видими.
- [ ] Измерени cost, latency и lawyer verification time.
- [ ] Early lawyer benchmark, holdout и per-case misses са документирани.
- [ ] Local private storage, safe logs, delete и restore са проверени.
- [ ] Няма SaaS инфраструктура в Solo scope.
- [ ] Operator-assisted evaluation е описан и не отваря remote достъп.
- [ ] Issues имат persisted model и run-consistent FK.
- [ ] Phase C таблиците не присъстват в Phase A–B migrations.
- [ ] Compose публикува само 127.0.0.1:8000:8000; Postgres е internal.
- [ ] Два embedding модела са сравнени без tuning върху holdout.
- [ ] Readiness status различава functional prototype и lawyer-validated workflow.

## 23. Инструкция за Codex / Claude Code

Прочети тази спецификация и приложимите AGENTS.md. Започни с Phase A: източници, права/условия за ползване, нормативни данни и 5 реални тестови въпроса. Не започвай с красив dashboard или SaaS infrastructure.

Не измисляй APIs, legal sources, актове, законови текстове или benchmarks. Не използвай production mocks. При недостъпен source покажи blocker и честен manual fallback.

След Phase A изгради минималния query-only search experiment от Phase B и го оцени с адвокат. Пази interfaces прости; не добавяй Redis/Celery, organizations, login, Stripe, RLS, Argument Map или Strategy Engine.

След всеки етап отчети: какво работи с реални данни, как е проверено, колко струва, какво липсва и дали има основание за следващата фаза.

Крайният въпрос е: **работи ли полезно за един адвокат?** Ако отговорът е доказано положителен, SaaS ще бъде отделното следващо задание.

## 24. Бюджет и нерешени зависимости

Обсъжданите €1.5–5 хил. за Phase A+B и €1–3 хил. за Phase C са външни ориентири за планиране, не проверени оферти или обещана крайна цена. Юридически преглед, адвокатско време, лицензирани данни, AI потребление и продължителност се оценяват отделно по реалния обхват. Не приемай, че безплатен достъп заменя съгласуваното участие на адвокат.

Преди разходи събери конкретни условия/оферти при необходимост и запиши budget caps. Локалният deployment намалява риска и инфраструктурата, но не премахва поверителността или нуждата от преглед при открит проблем. Отделен security consultant не е автоматичен Phase A–B разход; remote/SaaS deployment ще изисква нова оценка.

Потвърдено от собственика: има адвокат-партньор за Phase A. Предстои да предостави петте обезличени development въпроса, известните му релевантни актове и да съгласува началната област.

Непотвърдени в момента: конкретна начална област, corpus permissions, нормативен source/license, embedding candidates и реални разходи. Не попълвай тези неизвестни с измислени решения.
