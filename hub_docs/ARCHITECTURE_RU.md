# Архитектура Soft Hub 0.6.4

Документ фиксирует текущую архитектуру, реальные границы доверия и порядок превращения шести найденных legacy-ботов в плагины. Формулировки «сейчас» относятся к реализованному коду Soft Hub 0.6.4; пункты «нужно добавить» не являются обещанием уже существующей функции.

## 1. Продуктовая модель

Soft Hub — локальное single-user desktop-приложение для Python-автоматизаций. Основной пользовательский путь — установить arm64 macOS DMG, перенести **Soft Hub.app** в Applications и запускать приложение кликом. Python, Node.js и терминал пользователю не нужны: release bundle содержит управляемый Python runtime и core Hub.

Продуктовая модель:

- один каталог данных;
- одна центральная SQLite БД для профилей, модулей, запусков, событий и результатов;
- один Vault для связки wallet/proxy/email/Twitter/AdsPower profile, реферального графа и отдельных глобальных Capsolver/AdsPower API keys;
- versioned plugins вместо копирования и ручного запуска папок;
- отдельный subprocess на run;
- структурированный журнал и results вместо разрозненных console logs/CSV;
- явные read/testnet/mainnet действия и подтверждения риска;
- нижний Operations Shelf как пульт, а не вторая навигация: batch launch, все active runs и `needs_attention`; Vault/import/patch остаются в профильных разделах.

Это не облачный multi-tenant сервис, не контейнерный оркестратор и не security sandbox для недоверенного кода. Плагин — локальный привилегированный код, которому пользователь осознанно даёт выбранные секреты.

CLI и локальный браузерный режим остаются интерфейсами разработчика и тестирования, а не инструкцией по установке пользовательского приложения.

Термин «патч» в UI означает установку полного пакета новой версии. Hub не накладывает файловый diff на старую версию и не изменяет исходные папки legacy-софтов.

## 2. Ключевые архитектурные решения

### 2.1. Core владеет общими данными, plugin — предметной логикой

Core владеет:

- идентичностью профиля;
- общей связкой EVM key / HTTP proxy / email / email password / Twitter / AdsPower profile, реферальным графом и глобальными Capsolver/AdsPower API keys;
- шифрованием и unlock/lock;
- атомарным импортом профилей и ограждённым plaintext export;
- каталогом модулей и версий;
- metadata discovery публичных GitHub-патчей через Patch Radar;
- созданием run и ограничением общей конкуренции;
- журналом, progress и results;
- risk acknowledgement и account leases.

Plugin владеет:

- API/RPC клиентом проекта;
- предметными действиями;
- проверками chain/contracts;
- retry и idempotency;
- интерпретацией ответа;
- безопасной остановкой и внешней reconciliation;
- plugin-specific durable state, когда для него появится корректное стабильное размещение.

Плагин не должен напрямую обращаться к `hub.sqlite3`. Изменение таблиц core без миграции ломает и безопасность, и rollback.

### 2.2. Полная неизменяемая версия вместо in-place patch

Каждая пара `plugin id + SemVer` устанавливается в новый каталог. Это даёт изолированную `.venv`, основу для воспроизводимого runtime и возможность переключить активную версию. Полная воспроизводимость дополнительно требует закреплённых dependency artifacts/hashes. Уже установленную пару переустановить нельзя.

Извлечённый каталог технически доступен на запись текущему пользователю и процессу плагина, поэтому «неизменяемый» — архитектурный инвариант, а не защита файловой системой. Ручное редактирование установленной версии запрещено процессом эксплуатации.

### 2.3. Subprocess + JSONL вместо импорта plugin-кода в сервер

Hub не импортирует предметный plugin-код в основной процесс. `runtime/bootstrap.py` запускается отдельным Python и только он импортирует entrypoint. Граница обмена:

- один JSON context через stdin;
- JSONL events через stdout;
- обычный stderr как предупреждения;
- exit code и terminal event как итог.

Так сбой или `sys.exit()` плагина не обязан уронить HTTP-сервер и Vault. Это изоляция жизненного цикла, но не OS security isolation.

### 2.4. Консервативный статус для внешних записей

Если read-run завершается неоднозначно, статус — `failed`. Если действие с риском `external_write`, `testnet_write` или `mainnet_write` завершается без доказанного success/cancel, статус — `needs_attention`. Hub не утверждает, что внешнего side effect не было.

Это важнее автоматического retry: повтор финансовой операции без reconciliation может удвоить транзакцию, ордер или расход.

Известный `failed` можно отдельно отметить как `reviewed`: это закрывает только живое уведомление и не переписывает исторический outcome, account states, results или events. Такой review не снимает leases и fail-closed запрещён, если lease неожиданно существует. Только `needs_attention → reconciled` после реальной внешней проверки снимает safety lease.

### 2.5. Shared Vault вместо шести зашифрованных файлов

Общие credentials импортируются один раз и выдаются плагину по declared secret kinds. Legacy `database.enc`, `vault.enc` и plaintext input не должны мигрировать внутрь пакета.

Отдельный operational journal, например Fairground FSM, не является копией общего Vault и не должен насильно помещаться в таблицу `accounts`: у него другой lifecycle и схема миграций.

### 2.6. Управляемый runtime внутри desktop-приложения

Release-сборка не зависит от Python, установленного у пользователя. Перед упаковкой `scripts/prepare_runtime.py`:

- загружает закреплённый архив CPython 3.12.13 для целевой OS/архитектуры;
- проверяет заранее заданный SHA-256 архива;
- устанавливает зависимости core из `requirements-runtime.lock`;
- копирует `soft_hub` в runtime и сохраняет закреплённый offline wheel `pip` для подготовки plugin `.venv`;
- записывает marker с идентификатором runtime и hash исходников, затем выполняет import/crypto self-check.

Electron Builder помещает результат в `Resources/python`. В packaged-режиме launcher рассматривает только этот interpreter, запускает его с `-I` и не использует `PATH`, системный Python или `SOFT_HUB_PYTHON` как fallback. Если managed runtime отсутствует или не проходит probe, приложение завершается с предложением переустановить его из DMG.

Запуск из исходников устроен отдельно: разработчик может выбрать Python через `SOFT_HUB_PYTHON` либо использовать локальный Python 3.12. Это dev/test surface и не часть пользовательского контракта.

## 3. Карта компонентов

```text
Soft Hub.app
├── Electron window
├── managed CPython 3.12 + core
└── schemas/docs
          │ HTTP 127.0.0.1 + X-Soft-Hub-Token
          ▼
    HubApplication / API
    ├── Database ───────────── external <data-dir>/hub.sqlite3
    ├── Vault ──────────────── AES-GCM account и global secrets
    ├── GitHubPatchFeed ────── public Patch Radar metadata
    ├── PluginManager ──────── install / prepare / rollback
    └── RunManager
          │ spawn version .venv или managed Python; stdin context
          ▼
    runtime/bootstrap.py
          │ import package.module:function
          ▼
    plugin entrypoint
          │ SDK events → JSONL stdout
          └ print/traceback → stderr
```

Файловая ответственность:

| Компонент | Файл | Ответственность |
|---|---|---|
| Desktop shell | `electron/main.cjs` | Запуск Python core, sandboxed renderer, навигация только на origin Hub. |
| Release runtime builder | `scripts/prepare_runtime.py`, `requirements-runtime.lock` | Закреплённый CPython/dependencies, проверка SHA-256, сборка и self-check managed runtime. |
| Core process host | `soft_hub/__main__.py` | Аргументы, loopback server, URL с token, lock Vault при штатном завершении; CLI-вход используется разработчиками. |
| Instance ownership | `soft_hub/instance_lock.py` | Межпроцессный exclusive lock одного data directory. |
| HTTP boundary | `soft_hub/api.py` | Локальные маршруты, token/Host/Origin checks, ограничения body, static UI. |
| Paths/config | `soft_hub/config.py` | Версии, лимиты, data directory. |
| SQLite | `soft_hub/database.py`, `migrations/` | Миграции и короткие соединения/транзакции. |
| Credentials | `soft_hub/vault.py` | Нормализация, шифрование account/global secrets, импорт 1:1, ограждённый export и выборочная выдача. |
| Patch discovery | `soft_hub/github_patches.py` | Сканирование metadata публичных GitHub `.patch` repositories и строгий выбор release asset. |
| Package manager | `soft_hub/plugins.py` | Manifest/ZIP validation, checksums, version install, venv, rollback. |
| Run host | `soft_hub/runner.py` | Очередь, leases, subprocess, protocol, redaction, run/account statuses и results. |
| Protocol adapter | `soft_hub/runtime/bootstrap.py` | Context decode, import/call, sync/async, signals, terminal events. |
| Author API | `soft_hub/sdk.py` | `HubContext`, `HubAccount`, log/progress/result/account_state/cancel. |
| Adapted catalog | `soft_hub/catalog/legacy.json`, `dist/plugins/` | Связывает legacy-исходники с проверяемыми встроенными `.softhub.zip`; установка остаётся явным действием оператора. |

## 4. Каталог данных

Packaged desktop получает системный пользовательский путь через Electron `app.getPath('userData')`. Для текущей macOS-сборки это по умолчанию:

```text
~/Library/Application Support/Soft Hub
```

Каталог данных находится вне `/Applications/Soft Hub.app`. Замена `.app` новой версией обновляет Electron, core и managed runtime, но не удаляет Vault, профили, установленные плагины, журнал и результаты. Удаление только `.app` также оставляет данные на месте.

`SOFT_HUB_DATA_DIR` и `--data-dir` считаются dev/test override, а не пользовательским способом установки. При запуске из исходников путь выбирается так:

- `SOFT_HUB_DATA_DIR`, если задан;
- macOS: `~/Library/Application Support/Soft Hub`;
- Windows: `%APPDATA%\Soft Hub`;
- Linux: `${XDG_DATA_HOME:-~/.local/share}/soft-hub`;
- `--data-dir` имеет явный приоритет при создании `HubApplication`.

Технически desktop launcher тоже принимает `SOFT_HUB_DATA_DIR`, чтобы изолировать dev/smoke-запуск. Это неподдерживаемая для обычного пользователя настройка: production UX всегда исходит из системного user-data каталога.

Структура:

```text
<data-dir>/
├── .soft-hub.lock
├── hub.sqlite3
├── hub.sqlite3-wal              # может существовать во время работы
├── hub.sqlite3-shm              # может существовать во время работы
├── plugins/
│   ├── .staging/
│   └── <plugin-id>/
│       └── <version>/
│           ├── hub.plugin.json
│           ├── hub.checksums.json
│           ├── plugin/...
│           └── .venv/...        # только после prepare
├── imports/                     # временный upload, удаляется после install
├── runs/
│   └── <run-id>/scratch/
└── logs/
```

Root и создаваемые каталоги получают `0700`, SQLite и загружаемый архив — `0600`, насколько это поддерживает ОС. Извлечённые каталоги создаются с `0700`, файлы — `0600`.

Scratch уникален для run, но сейчас не очищается. Он не является постоянным API результатов и не является стабильным plugin storage. Загруженный исходный ZIP после установки удаляется; БД хранит только его SHA-256, а установленный каталог — распакованное содержимое.

## 5. Центральная SQLite БД

`Database` открывает отдельное соединение на операцию/поток и включает:

- foreign keys;
- WAL;
- `synchronous=NORMAL`;
- busy timeout 15 секунд;
- `BEGIN IMMEDIATE` для явно сгруппированных изменений.

Версионированные SQL migrations применяются по имени `NNN_*.sql` и фиксируются в `schema_migrations`.

### 5.1. Группы таблиц

| Область | Таблицы | Что хранится |
|---|---|---|
| Vault | `vault_meta`, `accounts`, `account_secrets`, `vault_secrets` | KDF/verifier, plaintext control metadata labels/address/fingerprints, encrypted account bundle и глобальные secrets. |
| Plugins | `modules`, `module_versions` | Активная версия, manifest, health, enabled, paths, archive SHA. |
| Runs | `runs`, `run_events`, `results` | Статусы, progress, redacted события и структурированные результаты. |
| Concurrency | `account_leases` | Пара `chain_id + account_id` для chain write; внутренний service-scope + account для `external_write`. |
| Core | `settings`, `schema_migrations` | Настройки и применённая схема. |

`run_events`, `results`, module manifests, labels, адреса, masked email/proxy labels, `twitter_configured` и fingerprints не шифруются Vault. Account/global secret payload шифруется. Plaintext в SQLite не означает публичность в UI/API: locked boundary скрывает и эту metadata. Поэтому plugin output обязан быть очищен до отправки, а backup всей БД всё равно считается чувствительным.

Наличие `vault_meta` и успешный unlock не означают наличие account row. Vault — контейнер и состояние ключа, а импортированный профиль — отдельная запись `accounts` + `account_secrets`. Корректное начальное состояние после создания Vault — `vault.exists=true`, `vault.unlocked=true`, `accounts=0`; onboarding и run UI обязаны показывать импорт как отдельный незавершённый шаг.

### 5.2. Почему одна БД не означает одну схему для всего

`hub.sqlite3` — control-plane database. Добавлять туда таблицы каждого проекта напрямую нельзя:

- core migrations начнут зависеть от жизненного цикла плагина;
- rollback кода не сможет безопасно откатить state schema;
- удаление/отключение плагина затронет core;
- ошибочный SQL плагина получит доступ к Vault metadata и журналам.

Правильная будущая модель для Fairground-подобного FSM: стабильный `<data-dir>/plugin-data/<plugin-id>/`, отдельная SQLite БД плагина, versioned migrations внутри плагина и backup policy Hub. Такого `plugin_data_dir` в текущем `HubContext` пока нет.

## 6. Vault

### 6.1. Создание и unlock

Мастер-пароль должен иметь минимум 14 символов и хотя бы 6 разных символов. При создании:

1. Генерируется 16-byte salt.
2. Ключ длиной 32 bytes выводится через scrypt: `N=32768`, `r=8`, `p=1`.
3. AES-GCM шифрует verifier с отдельным 12-byte nonce и AAD `vault-meta-v1`.
4. Derived key остаётся в `bytearray` памяти процесса Hub.

При unlock verifier проверяется authenticated decryption. Пароль не записывается. При lock bytearray заполняется нулями и ссылка удаляется.

Create/unlock не создаёт профиль и не генерирует расходники. Эти операции лишь делают доступным ключ шифрования; профиль появляется только после отдельного успешного импорта в `accounts`/`account_secrets`.

Ограничение: Python, криптобиблиотеки и JSON создают дополнительные immutable bytes/strings, которые нельзя гарантированно обнулить. Нет OS keychain, аппаратного enclave, idle auto-lock или recovery key. Процесс того же пользователя с достаточными debug-правами, root/administrator, malware или скомпрометированная ОС находятся вне модели защиты.

### 6.2. Граница заблокированного Vault

Когда Vault существует, но не разблокирован, bootstrap не раскрывает даже косвенную account/run metadata: `accounts`, `runs` и `results` возвращаются пустыми, счётчики accounts/results — нулевыми, а признаки настройки Capsolver/AdsPower — `null`, а не `false`. Безопасные агрегаты активных и требующих внимания операций могут оставаться видимыми, но не содержат labels, account IDs или результатов.

Прямые account/run/result projections, включая детали, account states, events и выгрузку технического журнала, возвращают HTTP `423 Locked`. Тот же gate применяется к `POST /api/modules/<id>/run`, `POST /api/runs/batch`, stop/force-stop, review и reconciliation. Публичный API не допускает новый run с закрытым Vault; кроме того, любое внутреннее действие с выбранным account требует key даже при пустом наборе secret grants, потому что UUID, label и EVM address тоже считаются защищённой пользовательской metadata.

Renderer не является единственной защитой, но при lock дополнительно очищает accounts/runs/results, формы и открытые панели из памяти/DOM. Счётчик эпохи защищённых запросов делает ответы, отправленные до блокировки, устаревшими и не позволяет им повторно заполнить UI после lock.

### 6.3. Импорт профилей

Основной UI-контракт 0.6.4 — таблица ровно из пяти колонок:

```text
private_key,proxy,email,twitter,adspower_profile
```

Её можно вставить из буфера или загрузить как CSV/TSV/TXT. Разделитель определяется в порядке tab, `;`, `,`; канонический необязательный заголовок — `private_key,proxy,email,twitter,adspower_profile`. Первые три ячейки каждой строки обязательны; Twitter и AdsPower profile ID могут быть пустыми, но обе колонки присутствуют. Альтернативный ручной режим принимает отдельные списки EVM private keys, HTTP proxies и emails одинаковой ненулевой длины; Twitter и AdsPower profile ID необязательны, но заполненный список тоже должен совпадать по длине.

Email passwords и labels необязательны, но если переданы, их количество тоже должно совпадать. Private key нормализуется к `0x` + 64 lowercase hex, адрес получается через `eth-account`. Proxy нормализуется к `host:port:user:password`; HTTPS proxy отвергается.

Для key, proxy и email вычисляются SHA-256 fingerprints. В одном импорте и между аккаунтами они уникальны. Повтор того же key обновляет существующий профиль; proxy/email другого профиля присвоить нельзя.

На каждый account создаётся один JSON secret bundle с private key, полным proxy, email, optional email password, optional Twitter, optional AdsPower profile ID и реферальными полями. Он шифруется AES-GCM с новым nonce и AAD `account:<uuid>:v1`. При повторном импорте существующего private key реферальные поля сохраняются. В plaintext `accounts` остаются:

- UUID, label, EVM address;
- fingerprints;
- proxy endpoint без credentials;
- masked email;
- только признаки `email_password_configured`, `twitter_configured` и `adspower_configured`, но не сами значения;
- tags/status/timestamps.

Импорт атомарен: ошибка в одной строке не должна оставлять частично записанный batch.

### 6.4. Глобальные secrets и plaintext export

Capsolver и AdsPower API keys хранятся отдельно от account bundle в `vault_secrets`, каждый шифруется AES-GCM со своим nonce/AAD и представлен в публичном API только статусом настройки. Оба ключа должны содержать минимум 4 символа. AdsPower API key один на весь Hub и вводится в capability-карточке **AdsPower Local API** раздела **Аккаунты**; AdsPower profile ID остаётся отдельным полем каждого account. Значение выдаётся run только при exact action grant `capsolver_api_key` либо `adspower_api_key`; SDK предоставляет их через `context.settings`.

Plaintext account export — осознанно опасный перенос данных, а не backup. Backend требует одновременно уже разблокированный Vault, повторную проверку мастер-пароля и точное регистрозависимое подтверждение `EXPORT PLAINTEXT SECRETS`. Основной формат — минимальный XLSX без formulas/macros/external links, где пять колонок `private_key,proxy,email,twitter,adspower_profile` записаны как SpreadsheetML `inlineStr`; это сохраняет исходные значения и закрывает CSV formula injection при открытии в spreadsheet. Для совместимости endpoint без `format` по-прежнему возвращает lossless UTF-8 BOM raw CSV, а `format: "xlsx"` — безопасный для Excel вариант. Raw CSV предназначен только для автоматического round-trip и не должен открываться в Excel/Sheets. Email passwords, labels/tags, реферальные связи/коды и глобальные Capsolver/AdsPower API keys не экспортируются. Оба ответа имеют `Cache-Control: no-store`; после сохранения защита Vault к файлу больше не применяется.

### 6.5. Реферальный граф

Реферальная сеть хранится внутри зашифрованного account payload. У account может быть собственный код `referral_code` и ровно один источник приглашения: `referrer_account_id` другого локального account **или** `external_referrer_code`; одновременное заполнение запрещено. Коды нормализуются и должны содержать `4..2048` символов без control-символов.

`POST /api/accounts/referrals` применяет полный batch в одной транзакции: расшифровывает актуальный граф, проверяет account IDs, self-links и циклы по всему итоговому графу, затем заново шифрует изменённые payloads. При любой ошибке не сохраняется ни одна связь. `list_accounts()` отдаёт только безопасную metadata — тип/ID/label реферера, признаки наличия кодов и число прямых потомков — но никогда не возвращает сами коды. UI-редактор в разделе **Аккаунты** использует эти признаки и не подставляет сохранённые коды обратно в DOM.

При run значение `referrer_code` разрешается по согласованному snapshot: внешний код ребёнка имеет приоритет, иначе берётся текущий собственный код выбранного parent account. Поэтому смена кода родителя автоматически применяется к его детям без дублирования секрета. Повторный импорт сохраняет граф; удаление parent атомарно отсоединяет его прямых детей; plaintext export намеренно исключает весь граф.

Контракт плагина использует exact grants `referral_code` (собственный код account) и `referrer_code` (эффективный входящий код), которым точно соответствуют `action.resources.account` со значениями `referral_code` и `referrer`. Реферальные grants/resources требуют `compatibility.hub >=0.6.4`. Это расширение не меняет базовый strict-контракт `SH-SOFTWARE-0.6/2`: для пакетов без referral его минимальная совместимость остаётся `>=0.6.3`.

### 6.6. Выдача плагину

Перед run Vault строит bundle:

```text
id, label, evm_address + только permissions.secrets
```

На уровне построения bundle account-free action при пустом `permissions.secrets` не требует Vault key, хотя публичные start/batch endpoints всё равно закрыты общим unlock gate. Если выбраны accounts, key обязателен даже при пустом наборе grants; bundle тогда содержит только `id`, `label` и `evm_address`. При наличии grants Vault расшифровывает payload и добавляет только разрешённые поля. Это реальное least-data ограничение на уровне context. Затем JSON context записывается в stdin subprocess, после чего host очищает свои ссылки на временные account/context structures. В процессе плагина выданные секреты существуют в plaintext.

Vault не защищает от уже авторизованного plugin-кода: получив secret, тот может отправить его в сеть или записать на диск. Поэтому установка плагина — решение о доверии к коду и зависимостям.

## 7. Lifecycle пакета

### 7.1. Inspection

До распаковки `PluginManager.inspect_archive()` проверяет:

- размер архива, число entries и суммарный unpacked size;
- переносимость и безопасность каждого пути;
- отсутствие symlink/duplicates/casefold conflicts/zip-bomb признаков;
- наличие root `hub.plugin.json` и `hub.checksums.json`;
- manifest version/ID/runtime/permissions/actions;
- точное совпадение списка checksum paths со всеми файлами;
- SHA-256 каждого файла через constant-time compare;
- наличие requirements path, если он объявлен.

Manifest и checksums ограничены 2 MB каждый. Вычисляется SHA-256 всего архива; при распаковке installer повторно считает каждый payload hash и перед активацией проверяет, что сам архив не изменился.

### 7.2. Install и активация

```text
upload → imports/<uuid>.softhub.zip
       → inspect
       → plugins/.staging/<uuid>/
       → повторная manifest validation
       → atomic os.replace в plugins/<id>/<version>/
       → transaction modules/module_versions
       → удаление upload
```

Новая версия сразу становится active, все остальные версии этого module получают `active=0`. Новая запись имеет `trust_status=local_unsigned`. Для существующего module сохраняется его enabled-флаг; trust не повышается.

При exception staging удаляется. Если target уже перемещён, но версия не зафиксирована в БД, installer пытается удалить и его.

### 7.3. Health и prepare

- Нет requirements или файл пуст/с комментариями → `ready`.
- Есть реальные dependency lines и нет host-owned marker → `needs_setup`.
- Prepare перед установкой снимает прежний ready-marker и переводит модуль в `needs_setup`, затем создаёт `.venv`, запускает pip с timeout 900 секунд и только после code 0 атомарно пишет новый marker с requirements SHA-256.

Install запрещает готовую `.venv`, не импортирует entrypoint и не делает smoke test. Готовность требует `.venv` Python и `.soft-hub-ready.json`, совпадающий с текущим requirements; отдельной проверки целостности всех установленных packages всё ещё нет.

Managed Python приложения и `.venv` плагина — разные слои. Первый доставляет и запускает core без системного Python. Второй создаётся командой **Подготовить** внутри конкретной установленной версии плагина и содержит её зависимости. В packaged app базовым interpreter для такой `.venv` служит managed runtime; обновление runtime меняет его fingerprint и требует пересоздания несовместимого plugin environment.

### 7.4. Rollback

Rollback выбирает самую недавно установленную версию, отличную от активной, переключает флаги и пересчитывает health. При активном/queued run этого плагина операция отклоняется. Это переключение версии, не откат внешних транзакций, не восстановление Vault и не down-migration plugin state. Версии не удаляются.

### 7.5. Patch Radar

Patch Radar принимает GitHub username или точный HTTPS URL `https://github.com/<owner>`, нормализует owner и сохраняет его в settings. Он читает первую страницу — не более 100 public repositories — и оставляет только имена с точным case-insensitive суффиксом `.patch`. Для каждого кандидата читается только latest release.

Карточка готова к установке лишь при ровно одном asset с case-insensitive суффиксом `.softhub` или `.softhub.zip`; обычный `.zip` не подходит. Metadata должна дополнительно пройти ранние границы: `asset.size` — целое число от 1 byte до 256 MB, `browser_download_url` — не длиннее 2048 символов и безопасный GitHub release URL того же owner/repository. Сканирование не скачивает и не устанавливает пакет. После явной команды оператора downloader отдельно применяет собственный лимит 256 MB, а затем передаёт файл в обычный inspection/install pipeline.

Radar не отправляет GitHub token или Authorization, поэтому private repositories недоступны и действуют анонимные API rate limits. Обычная установка по GitHub URL также поддерживает только public releases; token-based доступ в 0.6.4 отсутствует.

## 8. Lifecycle запуска

### 8.1. Admission

До backend admission renderer строит run form из action schema. Для `account_mode: one_or_more` единственный существующий профиль выбирается автоматически; при нуле профилей вместо немого пустого списка показывается inline import CTA, а успешный импорт возвращает оператора к сохранённым module/action. При нескольких profiles выбор остаётся явным, доступен select-all, а смена action сбрасывает прежний multi-profile batch независимо от risk taxonomy. Если accounts существуют, но ни один не выбран, submit останавливается на клиенте с inline-ошибкой и переводом focus/scroll к account selector; `RunManager` независимо сохраняет серверную проверку минимума одного account.

Numeric options рендерятся с разной HTML step-семантикой: `integer` получает `step=1`, `number` — положительный конечный `multipleOf` либо `step=any`. Пустые необязательные numeric controls не сериализуются как `0`/`null`, а defaults не должны попадать в native `stepMismatch`. Boolean `acknowledge_testnet_transactions` не рендерится среди options именно у `testnet_write`.

Renderer не является trust boundary. Core независимо применяет закрытую action options schema: неизвестные keys, неверные JSON-типы, required/enum/range/multipleOf и malformed schema отклоняются до доступа к Vault и до INSERT run.

`RunManager.start()`:

1. Находит enabled/ready module.
2. Находит action в manifest.
3. Удаляет дубли account IDs с сохранением порядка.
4. Проверяет `account_mode`.
5. Для testnet проверяет единственный host acknowledgement `TESTNET`; для mainnet — action phrase.
6. После успешного testnet gate принудительно устанавливает `options.acknowledge_testnet_transactions=true`, если action schema объявляет это boolean-поле; значению клиента runner не доверяет.
7. Повторно валидирует, что активные версия/path/health модуля не изменились между preflight и admission.
8. Создаёт run со статусом `queued` и для write-action атомарно получает leases.
9. Запускает daemon thread выполнения; account bundles расшифровываются только после получения concurrency slot непосредственно перед spawn.

Пакетный endpoint принимает UUID idempotency key и canonical request hash. Повтор того же payload возвращает прежние runs, а reuse ключа для другого payload отклоняется. Mainnet в batch запрещён. Все элементы preflight-проверяются, а runs и write-leases создаются в одной DB-транзакции, поэтому admission действует по принципу «всё или ничего».

Глобальный semaphore по умолчанию пропускает четыре subprocess одновременно (`--max-concurrent` меняет предел). Ожидание слота прерываемое: worker проверяет cancellation через короткий timed acquire loop. Поэтому Stop у queued run завершается до расшифровки secrets, создания scratch и spawn; даже write-run в этой точке однозначно получает `cancelled`, а заранее зарезервированный lease освобождается. Semaphore освобождает только worker, который действительно получил slot. Внутренние threads/async tasks плагина Hub не контролирует.

### 8.2. Account leases

Для каждого выбранного account и каждого `permissions.chains` chain-write run создаёт строку с expiry 30 минут. `external_write` создаёт одну строку на account во внутреннем service-scope, не заставляя автора указывать фиктивную chain. Конфликт одинакового scope/account блокирует второй run. Event loop проверяет monotonic clock независимо от наличия вывода и продлевает lease раз в минуту.

Это защита от случайного параллельного write внутри одного экземпляра Hub, а не распределённый nonce manager:

- read-actions leases не берут;
- валидатор требует непустой `chains` только для `testnet_write`/`mainnet_write`, но ложную фактическую chain он обнаружить не может;
- второй Hub с тем же data directory блокируется exclusive lock, но отдельный legacy CLI leases не видит;
- фактическую chain плагина Hub не проверяет;
- при доказанном success/cancel leases удаляются;
- при `needs_attention`, forced stop или restart leases переводятся в бессрочную hold;
- снять hold можно только явным UI/API-подтверждением `RECONCILED` после внешней сверки;
- TTL и heartbeat не заменяют durable transaction journal.

### 8.3. Spawn

Для run создаётся новый `runs/<id>/scratch` с mode `0700`. Python выбирается из активной version `.venv`, иначе используется interpreter Hub. В packaged desktop это interpreter из `Soft Hub.app/Contents/Resources/python`; системный Python fallback отсутствует. В source/dev-режиме interpreter Hub задаёт окружение разработчика. Команда запускает core bootstrap и передаёт plugin root/entrypoint аргументами.

Subprocess получает:

- cwd = scratch;
- `shell=False`;
- новый process group/session;
- небольшой allow-list environment: системные PATH/temp/locale/certificate variables плюс Python hardening flags;
- stdin/stdout/stderr pipes UTF-8.

Из environment удаляются произвольные пользовательские variables, но это hygiene, не sandbox. Plugin может обращаться к filesystem и network с правами OS-пользователя.

Context сериализуется одной JSON-строкой и сразу закрывается stdin. Bootstrap ограничивает её 8 MiB. После decode он добавляет plugin root в `sys.path`, импортирует entrypoint и создаёт `started` event.

### 8.4. Protocol и сохранение

Bootstrap добавляет к каждому SDK event:

```json
{"protocol":"soft-hub-jsonl/1","seq":1,"type":"log",...}
```

Runner принимает только известные event types; account-scoped frame разрешён лишь для ID, сохранённого при admission текущего run. Stderr-строка становится warning event. После redaction:

- любой event попадает в `run_events`;
- для action без аккаунтов run-level `progress` монотонно обновляет `runs.progress`; значение вне `0..1` или регресс отклоняется;
- при наличии account projection run-level progress остаётся telemetry и не меняет итог: `runs.progress` монотонно получает `AVG(run_account_states.progress)` по всем выбранным аккаунтам, включая `queued=0`;
- `result` дополнительно создаёт строку `results`.
- `account_state` атомарно обновляет `run_account_states` со status/stage/progress/last_message.

`run_account_states` создаётся для всех выбранных ID в той же admission-транзакции, что и run/leases, и хранит snapshot label независимо от дальнейшего удаления аккаунта. Terminal account status может задать только `account_state`; обычный log/result обновляет activity, но не определяет успех. Read-only projection доступен через `GET /api/runs/{id}/accounts`. Operations Shelf параллельно читает bounded lanes `GET /api/run-accounts?scope=active` и `?scope=attention`: SQL-фильтр применяется до `LIMIT`, поэтому свежая история не вытесняет зависшую операцию, а response явно сообщает `truncated`.

Core не вычисляет проценты из времени, текста log или количества строк. Adapter задаёт фактические weighted milestones; runner проверяет диапазон и монотонность. `succeeded` account принудительно получает `1.0`; terminal `failed/skipped/blocked/needs_attention/cancelled` не повышает процент даже при переданном legacy-значении и сохраняет последнюю подтверждённую точку. Поэтому индикатор отвечает на вопрос «какая доля объявленной работы подтверждена», а lifecycle status отдельно отвечает на вопрос «чем она закончилась».

Compatibility-исключение ограничено точными first-party Checkpoint/Sekai/Umia `1.0.0` и заранее перечисленными actions. Если такой старый adapter не отправил terminal `account_state`, core принимает только ровно один account-scoped result `kind=account_summary` с allowlisted status; duplicate, third-party ID/version, произвольный log и историческая проекция не подходят. Миграция `007` применяет тот же allowlist к уже завершённым `unknown/unreported` строкам. Новые плагины на этот bridge рассчитывать не могут.

Bootstrap не вычисляет counters по короткому списку истории: `active_runs` и `needs_attention` считаются SQL-агрегацией по всей таблице. В payload входят active/`needs_attention` runs с active-first cap 500 и последние 30 остальных terminal runs; `runs_truncated` явно сообщает о переполнении operational lane. Поэтому старый живой процесс не исчезает из dock/poll после множества свежих завершений.

После трёх malformed stdout frames процесс принудительно завершается с protocol error. Одна строка ограничена 64 KB, весь run — 50 000 строками; превышение останавливает процесс. Bootstrap перенаправляет runtime `print()` в stderr, но import-time stdout остаётся опасным.

### 8.5. Terminal semantics

| Условие | Итоговый status |
|---|---|
| `completed` и exit code 0 без account projection | `succeeded`, progress 1.0. |
| `completed` и exit code 0 с аккаунтами | `succeeded`, progress остаётся `AVG(account.progress)`; нормальный exit не маскирует раннюю ошибку косметическими 100%. |
| `cancelled` или exit code 130 | `cancelled` |
| Любая другая неоднозначность у `read` | `failed` |
| Любая другая неоднозначность у write-action | `needs_attention` |

Summary берётся только из `completed.data.summary`; отдельные results существуют независимо.

Если Hub стартует и видит старые `queued`, `starting`, `running` или `cancelling`, он помечает их `needs_attention` с сообщением о restart и удерживает существующие write-leases до ручной сверки. Межпроцессный lock не даёт второму Hub выполнить recovery, пока первый владеет тем же data directory. Recovery внешнего состояния остаётся обязанностью предметного action.

### 8.6. Stop

Мягкий Stop API доступен только при `runtime.safe_stop === true`: Hub меняет status на `cancelling`, посылает процессу/process group terminate и после grace period усиливает сигнал. Отдельный force-stop требует backend acknowledgement `FORCE STOP`, не зависит от manifest и завершает POSIX process group либо Windows process tree через `taskkill /T /F`. Если subprocess ещё не создан и run только ждёт slot, оба пути завершают его как `cancelled` и снимают leases. Уже начавшийся write-run становится `needs_attention`; leases удерживаются до сверки.

На POSIX bootstrap на сигнал ставит cancellation event. Только cooperative polling плагина превращает его в корректный `cancelled`; на Windows эта семантика сейчас не гарантирована. Прерывание между подписью, broadcast и journal commit может оставить неопределённое внешнее состояние; один флаг manifest этого не исправляет.

При shutdown Hub перестаёт принимать runs, отменяет queued, сигналит всем активным процессам, ждёт ограниченный grace period и затем принудительно завершает оставшиеся. Write без доказанного terminal `cancelled`/success становится `needs_attention`, и его leases сохраняются. На POSIX runner дополнительно убивает descendants по PGID и ограничивает ожидание унаследованных stdout/stderr pipes; полноценный Windows Job Object пока не реализован.

## 9. HTTP и desktop boundary

Core слушает только `127.0.0.1` на выбранном порту. API требует случайный `X-Soft-Hub-Token`; UI получает token из URL fragment. Дополнительно:

- Host должен быть `127.0.0.1` или `localhost` и не может подменить порт сервера;
- mutating request с Origin разрешён только с тем же hostname и портом, что у текущего UI;
- JSON body ограничен 2 MB;
- upload ограничен 256 MB;
- security headers запрещают framing/object/external scripts;
- сервер не пишет request path/body в обычный terminal log.

Отсутствующий Origin принимается, поэтому token остаётся главным API credential. Любой local process, получивший token, может обращаться к API. Нет TLS, remote auth, users/roles и сетевого режима — server предназначен только для loopback.

Electron включает renderer sandbox, context isolation, отключает Node integration и внешнюю навигацию. Единственное узкое исключение — явный CTA Patch Radar может передать системному браузеру уже проверенный HTTPS URL вида `github.com/<owner>/<repo>`; само Electron-окно на внешний origin не переходит. Эта sandbox относится к UI renderer. Она не помещает Python plugin subprocess в sandbox.

Локальный preview для macOS собирается с ad-hoc подписью и не проходит Apple notarization. Это позволяет проверить локальный app bundle, но не подтверждает издателя и не является готовой схемой публичной доставки. Публичный релиз требует отдельного release pipeline с сертификатом Developer ID Application, hardened runtime, notarization и stapling; успешная локальная DMG-сборка сама по себе этот gate не проходит.

## 10. Честные границы доверия

| Граница | Что реализовано | Чего нет |
|---|---|---|
| Desktop runtime | Закреплённый CPython archive SHA-256, `requirements-runtime.lock`, core source hash, self-check и запуск с `-I`. | Криптографически воспроизводимая сборка всех artifacts; runtime не изолирует плагины от ОС. |
| Desktop distribution | Локальный arm64 DMG с ad-hoc подписью. | Developer ID identity, hardened runtime, Apple notarization/stapling для публичного релиза. |
| Архив | Safe paths, лимиты, SHA-256 всех файлов и всего архива. | Подписи издателя, certificate chain, transparency log, доверенный registry. |
| Patch Radar | Ограниченное чтение metadata public `.patch` repositories, строгий latest asset и отдельный download limit. | Private repositories, GitHub token и доказательство доверия к найденному автору/asset. |
| Идентичность плагина | `id/version`, archive hash, `local_unsigned`. | Доказательство автора. Любой может пересобрать checksums. |
| Secrets в context | Выдаются только declared secret kinds выбранных accounts. | Защита секрета после выдачи коду плагина. |
| Environment/cwd | Узкий env, отдельный scratch, subprocess. | OS/container sandbox, filesystem ACL profile, syscall restrictions. |
| Network | Плагин декларирует domains. | Firewall/DNS/proxy enforcement; allow-list пока не исполняется. |
| Chains | Declared chain IDs участвуют в leases. | Проверка RPC chain, contracts, calldata, суммы или nonce. |
| Browser/local service | Manifest валидирует AdsPower grants, `browser=true`, canonical local service и action resources; Vault хранит profile ID/API key, runner делает preflight. | Provisioning AdsPower/Chrome/driver, принудительный permission broker, endpoint/auth mediation, browser sandbox. |
| Output | Exact-secret и regex redaction, bounds/truncation. | Гарантия против encoding/fragmentation/custom token или записи в файл/сеть. |
| Dependencies | Отдельная `.venv`, captured pip output, timeout. | Подпись wheel, lock enforcement, запрет build hooks, malware scan. |
| Stop/restart | Process group signal, grace period, `needs_attention`. | Транзакционная отмена внешнего side effect, автоматический resume/reconcile. |
| Local UI | Loopback, random token, Host/Origin/CSP, Electron renderer sandbox. | Защита от malware того же OS-пользователя и remote multi-user deployment. |

Checksums отвечают на вопрос «файлы совпали с таблицей внутри этого архива?», но не «кто создал архив?». `permissions.network` отвечает на вопрос «что автор заявил?», но не «куда процесс способен подключиться?». Subprocess отвечает на вопрос «упадёт ли plugin прямо внутри core?», но не «может ли plugin прочитать доступные пользователю файлы?».

До появления signature verification и OS sandbox устанавливать следует только собственные или полностью проверенные плагины. Особенно опасен этап prepare: `pip` и build backend зависимостей выполняют код с теми же пользовательскими правами ещё до первого run.

## 11. Redaction и чувствительные данные

`Redactor` знает точные секреты текущего context и regex для EVM key, JWT и proxy. Он рекурсивно очищает event data, terminal summary и host error. Это снижает риск случайного логирования, но не является DLP.

События и results после redaction хранятся открыто в SQLite. Следовательно:

- plugin не отправляет email password, key, proxy credentials, cookie, access token, raw transaction;
- error payload содержит тип/код, а не полный request/response с headers;
- account связывается через UUID, не через secret;
- публичный transaction hash допустим, если продуктово нужен и не раскрывает нежелательную связь;
- backup БД и scratch всё равно чувствительны.

## 12. Карта шести legacy-ботов

Источник списка — `soft_hub/catalog/legacy.json`. В Soft Hub 0.6.4 каждый из шести Python-софтов по-прежнему связан с готовым историческим пакетом в `dist/plugins/`; эти архивы включаются в desktop release и устанавливаются из Patch Bay по ID, без передачи пути от renderer. Каталог `gigaverse/` остаётся UI/product reference и отдельным TypeScript/Electron product; он не входит в шесть Python entries.

### 12.1. Сводная карта

| Plugin ID | Исходник | Встроенный пакет адаптера | Исполняемая граница | Оставшийся blocker |
|---|---|---|---|---|
| `io.sprintray.skew-waitlist` | `skew_wl/` | `skew-waitlist-1.0.2.softhub.zip` | Profile validation и sequential waitlist POST с фактическими per-account milestones, timeout без auto-retry. | Adapter использует только email/proxy: Twitter уже есть в core, но в этот adapter не подключён; Telegram secret отсутствует. |
| `io.sprintray.checkpoint-testnet` | `Checkpoint_testnet/` | `checkpoint-testnet-1.0.2.softhub.zip` | Inspect, daily farm, deposit, full cycle, sell offer и weighted per-account milestones в Arbitrum Sepolia; default — один fill на аккаунт. | Нужен prefunded testnet ETH; CAPTCHA-dependent ветки не имитируются. |
| `io.sprintray.fairground-testnet` | `fairground/` | `fairground-testnet-1.0.2.softhub.zip` | External reconciliation с request/validation milestones; write gate явно завершается fail-closed. | Stable `plugin_data_dir`/checkpoint API для переноса durable FSM. |
| `io.sprintray.sekai-testnet` | `sekai_testnet_clean/` | `sekai-testnet-1.0.2.softhub.zip` | Preflight, prefunded on-chain cycle и progress по фактически обработанным workflow steps в HyperEVM testnet. | Новая версия адаптера должна подключить уже имеющиеся AdsPower resources к реальному browser/QuickNode CAPTCHA flow. |
| `io.sprintray.umia-testnet` | `umia-testnet-bot/` | `umia-testnet-1.0.2.softhub.zip` | Inspect, prefunded swap/auction bids и progress по подтверждённым операциям/bounded attempts в Base Sepolia. | Adapter не запрашивает/не интегрирует имеющийся в core Capsolver secret; CAPTCHA/Privy/faucet/auto-registration остаются fail-closed. |
| `io.sprintray.risex-guard` | `risex/` | `risex-guard-0.1.2.softhub.zip` | Capability/account/market/reconciliation reads, durable guarded intent и milestones после journal writes. | Отдельный signer secret, аттестованный Node bridge и exactly-once order broker. |

### 12.2. `skew-waitlist`

Текущий CLI читает четыре позиционных файла, собирает `Account`, запускает thread pool и пишет `results/registrations_*.csv`.

Первый plugin slice:

- action `register`, `risk: external_write`, `financial_risk: none`, `account_mode: one_or_more`;
- secrets `email`, `proxy`;
- network `skew.trade`;
- `state_model: stateless` только если повторная регистрация действительно идемпотентна или ответ API распознаёт duplicate;
- один `result(kind="waitlist_registration")` на профиль;
- summary `ok/fail/total`;
- отмена между submit/retry.

`register` честно объявлен как `external_write`: POST меняет внешний сервис без blockchain-транзакции, не требует financial acknowledgement, но получает per-account service-lease и неоднозначный force stop заканчивает в `needs_attention`. Core 0.6.4 умеет хранить и по разрешению выдавать Twitter, но `skew-waitlist-1.0.2` его не запрашивает; Telegram secret kind всё ещё отсутствует. Социальные данные нельзя обходным путём передавать через options.

### 12.3. `checkpoint-testnet`

Код уже разделяет режимы и имеет JSONL logger, поэтому adapter сравнительно прямой:

- `parse` → `read`;
- `daily`, `deposit`, `sell`, `full` → отдельные `testnet_write` actions;
- `chains: [421614]`, `financial_risk: testnet`;
- private key/proxy брать из `HubAccount`;
- Capsolver-dependent ветка исторического адаптера остаётся выключенной; новая версия должна явно запросить `capsolver_api_key` и реализовать интеграцию;
- заменить logger sink на SDK events, а итоговые wallet rows — на results;
- до write-релиза проверить повтор после timeout: SIWE/API response и tx receipt должны reconciliate по address/nonce/hash.

Не переносить `input/database.enc` и мастер-пароль legacy-бота. Параметры amounts/delays можно вынести в bounded options; RPC URL, chain ID и contracts лучше оставить проверяемыми constants.

### 12.4. `umia-testnet`

Umia уже имеет hard allow-list Base Sepolia `84532` и block-list mainnet IDs — эту проверку нужно сохранить внутри plugin, потому что Hub chain declaration не firewall.

Рекомендуемые actions:

- `portfolio` → read;
- `activities` → testnet_write;
- `faucet` → testnet_write с внешней регистрацией;
- `full` → testnet_write.

Первый релиз — portfolio, затем activities без auto-registration. Vault 0.6.4 умеет выдать глобальный Capsolver API key по явному разрешению, но `umia-testnet-1.0.2` его не запрашивает и не реализует CAPTCHA/Privy flow. Поэтому faucet/full и auto-registration остаются fail-closed; для доступных действий нужны prefunded Base Sepolia ETH и mUSDC. CSV portfolio и JSONL logger заменить per-account result/events. Перед retry проверять on-chain nonce, receipt, holdings/bids и состояние API.

### 12.5. `fairground-testnet`

Fairground отличается от остальных качественным persistent FSM: `databases/state.sqlite3` хранит cycles/actions, prepared/signed/broadcast data и recovery states. Его нельзя заменить одним `runs.status` — Hub не знает предметные переходы и chain exposure.

Адаптация:

1. Удалить только дублирующий `databases/vault.enc`; accounts приходят из Hub.
2. Сохранить FSM и его fail-closed transitions.
3. Сначала добавить в core стабильный `plugin_data_dir`, не зависящий от version/scratch.
4. Версионировать миграции FSM отдельно и тестировать forward compatibility с rollback кода.
5. Вывести actions `parse`, `run`, `recover`, `force_close`, отдельно faucet.
6. `run/recover/force_close` — `testnet_write`, chain 421614.
7. Подключить cancellation event к существующему safe-stop/fencing так, чтобы после сигнала не создавался новый increasing-risk write, а pending action оставался reconciliable.
8. AdsPower faucet выпускать только в новой версии адаптера, которая объявляет `adspower_profile`/`adspower_api_key`, resources и local service, а также реализует bounded browser cleanup.

Для этого плагина `state_model: resumable` будет честным только после сохранения FSM вне version package и smoke восстановления после kill/restart.

### 12.6. `sekai-testnet`

On-chain часть использует HyperEVM testnet chain 998; faucet работает через AdsPower/Playwright и локальный API. `prepare()` ставит только pip packages и не выполняет `playwright install chromium`, поэтому обычного requirements недостаточно.

Разбить минимум на:

- `portfolio` read — можно адаптировать первым;
- `activities` testnet_write — EVM/proxy, chain 998;
- `faucet_assist` — отдельная browser/local-service capability;
- `full` — только после обеих частей.

Core уже хранит per-account profile ID и global API key, выдаёт их по exact action grant и проверяет наличие через `resources`. Сам адаптер всё ещё обязан обеспечить установленный внешний AdsPower/browser runtime, актуальный endpoint/auth, bounded timeouts и cleanup session. Поля manifest `browser/local_services` остаются декларативными, поэтому их наличие само по себе не создаёт sandbox или рабочую автоматизацию.

### 12.7. `risex-mainnet`

RISEx-торговля остаётся заблокированной по безопасности, но в 0.6.4 есть исполняемый `io.sprintray.risex-guard`. Причины ограничения не косметические:

- формат account допускает основной key и отдельный signer key;
- Hub Vault имеет только один `evm_private_key` и не умеет lifecycle регистрации/отзыва signer;
- рабочий режим открывает несколько плечевых mainnet-позиций как корзину;
- Python вызывает Node bridge; prepare не делает `npm ci` и не гарантирует Node runtime;
- текущему Hub не хватает durable operation journal уровня «planned → signed → submitted → acknowledged → reconciled»;
- restart/force kill даёт только `needs_attention`, но не закрывает exposure.

Минимальный путь допуска:

1. Сначала read-only `stats` без выдачи signer, если API это позволяет безопасно.
2. Ввести отдельный secret kind `evm_signer_private_key` и UX регистрации/отзыва.
3. Зафиксировать typed-data domain/chain и fail-closed preflight.
4. Решить Node runtime/lockfile provisioning.
5. Реализовать durable basket journal и reconcile action.
6. Отдельно адаптировать `close_all` как mainnet recovery action; это тоже write и требует phrase.
7. Провести fault-injection на каждом промежутке между open legs, cancel и close.
8. Только затем выпускать `delta_neutral` с конкретной confirmation phrase и малыми hard limits.

Наличие экономических guards в legacy-коде полезно, но не заменяет durable recovery на границе Hub.

## 13. Исторический порядок адаптации

Эта очерёдность была выполнена к 0.2.0 и сохранена в 0.6.4; ниже оставлена логика, по которой адаптеры разделялись на исполняемые и guarded slices.

Приоритет строится по принципу «быстрый observability win → testnet writes → state/browser → mainnet».

1. **Skew Waitlist.** Самый маленький adapter, нет chain signer, сразу проверяет email/proxy mapping и per-account Results.
2. **Checkpoint.** Сначала parse, затем ограниченный testnet write; JSONL и режимы уже близки к контракту.
3. **Umia.** Сначала portfolio, затем activities без Capsolver; сохраняется жёсткая Base Sepolia защита.
4. **Fairground.** Перед адаптацией добавить стабильный plugin data dir; затем сохранить и подключить существующий FSM/recovery.
5. **Sekai.** После определения browser/local-service capability и AdsPower secret model.
6. **RISEx.** Последним, после signer Vault, Node provisioning и durable mainnet reconciliation.

Каждый бот выпускается вертикальными slices:

```text
import/adapter tests
→ self-check
→ read-only action
→ structured results
→ one-account testnet canary
→ multi-account testnet
→ recovery/fault injection
→ mainnet только для отдельно прошедшего safety gate
```

Не следует упаковывать все шесть папок «как есть» и запускать их CLI через subprocess: это сохранит шесть Vault, меню, локальные CSV и несовместимые stop semantics, то есть перенесёт проблему внутрь красивого UI.

## 14. Core gaps до следующего уровня

### Release gate — до публичного распространения

- Подписать `.app` сертификатом Developer ID Application с подходящими entitlements и hardened runtime.
- Отправить сборку на Apple notarization, проверить результат и выполнить stapling для распространяемого DMG/app.
- Публиковать SHA-256 и проверить установку на чистом arm64 Mac под действующим Gatekeeper.

Текущий ad-hoc signed/not notarized preview предназначен для локального тестирования и доверенной передачи, а не для публичного production-релиза.

### P0 — до stateful/mainnet плагинов

- Стабильный `plugin_data_dir` с quota, permissions, backup и versioned migration policy.
- Durable operation journal/reconciliation contract для финансовых writes.
- Windows Job Object для гарантированного завершения всего plugin process tree.
- Явный mainnet signer secret и lifecycle revoke/rotate.
- Проверка реального chain/domain внутри адаптеров; позднее — host policy.

### P1 — до browser и сторонних пакетов

- Интеграция уже существующих Twitter/Capsolver/AdsPower resources в новые версии нужных адаптеров; расширение schema остаётся нужным для Telegram/cookies и отдельного mainnet signer.
- Capability broker для browser/local services.
- Lifecycle hooks или заранее собранные runtimes для Playwright/Node, с lock/hash policy.
- Publisher signatures и allow-list доверенных ключей.
- OS-level sandbox/network egress policy на поддерживаемых платформах.

### P2 — эксплуатация

- Планировщик с timezone, jitter и лимитами, но только после idempotency/recovery.
- Зашифрованный backup/restore/rekey с проверкой целостности; реализованный plaintext account XLSX/raw CSV export его не заменяет.
- Очистка scratch и retention для events/results.
- Health check, который импортирует entrypoint в подготовленном runtime до активации.
- Compatibility enforcement по OS/Python.
- Отдельные activate/delete-version flows и pin canary version.

Эти gaps нельзя «решить манифестом»: поле `network`, `state_model` или `heartbeat_seconds` не превращает декларацию в enforcement.

## 15. Backup и восстановление

В UI 0.6.4 нет зашифрованного backup/restore. Ограждённый plaintext XLSX/raw CSV export переносит только `private_key,proxy,email,twitter,adspower_profile`, не сохраняет реферальный граф, историю, плагины и глобальные Capsolver/AdsPower API keys и не является backup. Для согласованной ручной копии безопаснее:

1. Остановить новые runs и дождаться завершения либо осознанно зафиксировать `needs_attention`.
2. Заблокировать Vault.
3. Полностью завершить Hub, чтобы WAL был checkpointed и ключ исчез из процесса.
4. Скопировать весь data directory, а не только `hub.sqlite3`: нужны plugin versions/venv и run artifacts; при live-copy также потребовались бы `-wal/-shm`.
5. Хранить backup зашифрованным и проверить restore на отдельном data directory.

Замена или удаление `/Applications/Soft Hub.app` не затрагивает этот каталог данных. Обратное тоже важно: копия одного `.app` не является backup пользовательских профилей, Vault, плагинов и истории.

Потеря мастер-пароля сейчас невосстановима. Backup SQLite не отменяет внешние транзакции и не доказывает состояние chain; для write-run нужны transaction hashes/journal и reconciliation.

`.venv` обычно лучше воспроизводить из закреплённых requirements, чем считать переносимым backup между ОС/архитектурами. Но исходные `.softhub.zip` Hub после install не сохраняет, поэтому release artifacts следует хранить отдельно вместе с их SHA-256.

## 16. Инварианты для code review

Любое изменение core или новый plugin должно сохранять следующие правила:

1. Secret не появляется в manifest, options, event, result, summary, path или exception text.
2. Plugin получает не больше secret kinds, чем объявлено.
3. Один `id/version` соответствует одному проверенному содержимому.
4. Read action не отправляет транзакции, ордера и финансовые подписи.
5. Write action перечисляет все chains и требует соответствующее подтверждение.
6. Неоднозначный write заканчивается `needs_attention`, а не ложным success.
7. Retry после возможного side effect начинается с reconciliation.
8. Safe stop означает проверенный recovery boundary, а не только обработчик SIGTERM.
9. Core DB меняется только migrations core; plugin state изолирован.
10. Legacy source directory не является runtime dependency установленного плагина.
11. Prepare/run не полагаются на cwd, user site packages или секретные env variables.
12. Checksums не называются подписью, subprocess не называется sandbox, declarations не называются enforcement.
13. Packaged app запускает core только из собственного managed runtime; системный Python не является скрытой runtime dependency пользователя.

Эта честность — часть продукта: Hub должен не только запускать много софтов, но и показывать, где операция завершена, где требует внимания и чему именно пользователь доверил ключи.
