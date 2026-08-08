# Нормативное техническое задание на софт для Soft Hub 0.6.4

Статус документа: обязательный контракт для всех новых модулей и всех новых major/minor-релизов существующих модулей, встраиваемых в Soft Hub 0.6.4.

Версия контракта: `SH-SOFTWARE-0.6/2`.

## 1. Нормативные термины и приоритет источников

В этом документе используются следующие термины:

- **ОБЯЗАН / MUST** — требование обязательно. Нарушение блокирует приёмку или выпуск.
- **ЗАПРЕЩЕНО / MUST NOT** — действие недопустимо. Нарушение блокирует приёмку или выпуск.
- **СЛЕДУЕТ / SHOULD** — требование выполняется по умолчанию. Отступление допускается только с записанным техническим обоснованием, тестом и одобрением владельца Hub.
- **МОЖЕТ / MAY** — допустимый вариант, не являющийся обязательным.

Новый пакет ОБЯЗАН объявить `"contract_version": "SH-SOFTWARE-0.6/2"`. Только отсутствие `contract_version` включает legacy admission для уже выпущенных пакетов. Legacy admission не является авторским контрактом и не разрешает выпускать новый пакет по правилам `/1`.

Если этот документ расходится с фактическим исполняемым контрактом Hub, выпуск останавливается: схема, валидатор, SDK, тесты и этот документ должны быть приведены к одному состоянию. Нельзя обходить расхождение кодом плагина.

Для нового пакета порядок источников истины таков: этот документ `/2`, `schemas/plugin.schema.json`, `validate_manifest()`, SDK/runtime и acceptance tests. Runtime-совместимость со старым манифестом является только механизмом запуска истории, а не послаблением `/2`.

Исполняемые источники истины для Soft Hub 0.6.4:

- `soft_hub/plugins.py` — проверка манифеста, архива, presentation assets и установка;
- `schemas/plugin.schema.json` — авторская JSON Schema;
- `soft_hub/sdk.py` — публичный Python SDK;
- `soft_hub/runtime/bootstrap.py` — запуск entrypoint и JSONL-протокол;
- `soft_hub/runner.py` — preflight, lifecycle, выдача секретов, остановка, redaction, results и журнал;
- `scripts/build_plugin.py` — единственный штатный сборщик `.softhub.zip`.

Импорт внутренних модулей Hub, кроме `soft_hub.sdk`, ЗАПРЕЩЁН.

## 2. Цель и модель интеграции

Софт для Hub — это **полный, самодостаточный, неизменяемый пакет одной версии**, а не diff поверх предыдущей версии и не скрипт, который пользователь запускает вручную.

Пакет ОБЯЗАН позволять Hub:

1. показать название, полное описание, иконку и обложку;
2. заранее показать действия, опции, риск и необходимые ресурсы;
3. до запуска сообщить пользователю, каких данных или настроек не хватает;
4. запустить Python entrypoint без терминала и интерактивного ввода;
5. показать прогресс и итог отдельно по каждому выбранному аккаунту;
6. безопасно остановить работу либо честно потребовать внешнюю сверку;
7. сохранить структурированные результаты и скачать очищенный технический журнал;
8. обновить модуль новой полной версией без изменения контракта его постоянного `id`.

Плагин ЗАПРЕЩЕНО проектировать так, чтобы Hub разбирал произвольный текст терминала, угадывал поля формы, искал картинку по имени файла или анализировал исходный код. Всё, что требуется интерфейсу и preflight, ОБЯЗАНО быть объявлено в `hub.plugin.json`.

## 3. Граница доверия

### 3.1. Что Hub обеспечивает

Hub:

- хранит account/global secrets в зашифрованном Vault at rest;
- передаёт subprocess только secret kinds, разрешённые выбранному action;
- запускает плагин без shell, в отдельной process group и с очищенным набором environment variables;
- использует отдельное Python-окружение версии плагина при наличии зависимостей;
- проверяет безопасную распаковку и SHA-256 каждого payload-файла;
- проверяет выбранные `account_id` в событиях;
- ограничивает протокол, объём вывода и размер скачиваемого журнала;
- редактирует события при сохранении и повторно при экспорте журнала;
- применяет risk gates и account leases для write-действий.

### 3.2. Чего Hub не обеспечивает

Плагин выполняется с правами текущего OS-пользователя. В Soft Hub 0.6.4 нет OS/filesystem/network sandbox. Поэтому:

- `permissions.network`, `permissions.browser` и `permissions.local_services` являются проверяемыми декларациями, но не firewall;
- отдельная `.venv` изолирует зависимости, но не полномочия процесса;
- checksum внутри неподписанного архива подтверждает целостность архива относительно самого себя, но не личность издателя;
- redaction — defense-in-depth, а не DLP и не разрешение сначала записать секрет;
- Hub не доказывает, что заявленный `risk` соответствует реальному коду;
- Hub не может откатить уже принятый HTTP-запрос, browser click, ордер или blockchain transaction.

Автор ОБЯЗАН считать плагин и все зависимости частью доверенной вычислительной базы. Владелец Hub должен устанавливать пакет только из проверенного источника.

## 4. Обязательная структура исходного пакета и архива

### 4.1. Каноническая структура

Новый софт ОБЯЗАН собираться из каталога следующего вида:

```text
my-soft/
├── hub.plugin.json
├── requirements.txt
├── assets/
│   ├── icon.png
│   └── image.webp
└── plugin/
    ├── __init__.py
    ├── main.py
    └── ...
```

После штатной сборки корень ZIP ОБЯЗАН иметь следующий вид, без внешней директории `my-soft/`:

```text
hub.plugin.json
hub.checksums.json
requirements.txt
assets/icon.png
assets/image.webp
plugin/__init__.py
plugin/main.py
plugin/...
```

Обязательные элементы:

- `hub.plugin.json` — UTF-8 JSON без комментариев;
- `hub.checksums.json` — генерируется только штатным builder;
- `requirements.txt` — присутствует всегда, даже если пуст и содержит только комментарий;
- `assets/icon.<ext>` — непустая raster-иконка;
- `assets/image.<ext>` — непустая raster-обложка;
- `plugin/__init__.py`;
- Python-модуль, указанный в `runtime.entrypoint`.

Допустимые необязательные элементы:

- дополнительные Python-модули внутри `plugin/`;
- статические ABI, шаблоны и публичные справочники внутри `data/`;
- `README.md`, `LICENSE`, `NOTICE` в корне.

Тесты, dev-конфигурация и build scripts СЛЕДУЕТ держать вне каталога, передаваемого builder: builder включает все обычные файлы рекурсивно, кроме явно игнорируемых служебных каталогов.

### 4.2. Presentation assets

Для **каждого нового софта по `/2`** поля `presentation` и оба файла assets ОБЯЗАТЕЛЬНЫ. Они не могут быть `null`, пустой строкой, data URI, URL или SVG.

Текущий валидатор допускает отсутствие `presentation` только для установки legacy-пакетов. Legacy fallback на `name`, `description` и `ui.monogram` **не является разрешением** опускать icon/image в новом пакете.

Нормативный визуальный профиль:

- icon: статический квадратный PNG или WebP, до `2 MiB`; СЛЕДУЕТ использовать `512×512` и не опускаться ниже `256×256`;
- image: статический PNG, JPEG или WebP, до `16 MiB`; СЛЕДУЕТ использовать пропорции `16:9` и размер около `1600×900`;
- оба файла ОБЯЗАНЫ иметь реальный raster payload, соответствующий расширению;
- прозрачность допустима у icon; image ОБЯЗАНА иметь осмысленный фон;
- важный текст, номера кошельков, секреты и QR-коды на изображениях ЗАПРЕЩЕНЫ;
- EXIF/XMP и иные необязательные metadata ОБЯЗАНЫ быть удалены; в assets не должно быть GPS, локальных путей, account labels или иных пользовательских данных;
- критичная информация не должна находиться у краёв image, поскольку UI может кадрировать её через `object-fit: cover`;
- анимированные GIF/AVIF, ICO и иные форматы, хотя часть из них принимается legacy-валидатором, для нового софта ЗАПРЕЩЕНЫ.

Пути ОБЯЗАНЫ начинаться с `assets/`, быть относительными POSIX paths, не содержать скрытых сегментов, `..`, backslash и внешних URL.

Автоматически сейчас проверяются путь, расширение, сигнатура payload, наличие файла и byte limit. Installer всё ещё принимает GIF/AVIF/ICO ради legacy admission и не декодирует полное изображение. Геометрия, frame count, pixel count, отсутствие metadata, статичность и качество контента являются обязанностью автора и acceptance review; успешная установка сама по себе не доказывает соответствие assets контракту `/2`.

### 4.3. Запрещённое содержимое

В исходном каталоге и архиве ЗАПРЕЩЕНЫ:

- private keys, seed phrases, mnemonic, пароли, API keys, proxy credentials и реальные аккаунты;
- `.env`, HAR, cookies, session storage, browser profiles, screenshots реальных сессий;
- CSV/XLSX/TXT/JSON с расходниками;
- локальные БД, key stores, certificates с private key;
- `.venv`, `node_modules`, `.git`, кэши, build/release каталоги;
- изменяемое production-состояние;
- symlink, special files, абсолютные пути и платформенные device names;
- исполняемый self-updater или код, подменяющий файлы установленного пакета.

Штатный builder и installer блокируют многие известные имена и private-key payloads, но автор ОБЯЗАН провести отдельный secret scan. Переименование `secrets.json` в нейтральное имя не делает payload допустимым.

### 4.4. Ограничения архива

Архив ОБЯЗАН укладываться в фактические лимиты Hub:

- не более `256 MiB` в сжатом виде;
- не более `512 MiB` после распаковки;
- не более `4000` ZIP entries;
- `hub.plugin.json` и `hub.checksums.json` — не более `2 MiB` каждый;
- файл больше `1 MiB` не может иметь подозрительное compression ratio свыше `100:1`.

Пути ОБЯЗАНЫ быть NFC-normalized, canonical, уникальными также без учёта регистра и переносимыми между поддерживаемыми ОС.

## 5. `hub.plugin.json`: обязательный контракт

### 5.1. Верхний уровень

Для нового софта по `/2` ОБЯЗАТЕЛЬНЫ поля:

- `schema_version` — строго `1`;
- `contract_version` — строго `SH-SOFTWARE-0.6/2`;
- `id` — постоянный ID модуля;
- `name` — короткое техническое имя;
- `version` — SemVer;
- `description` — короткое описание до 500 символов;
- `author`;
- `presentation`;
- `compatibility`;
- `runtime`;
- `permissions`;
- `actions`.

`ui` — необязательная legacy-метаинформация (`accent`, `monogram`) и не заменяет `presentation`. `author` — обязательная непустая подпись автора для отображения, но она не является криптографическим подтверждением издателя.

`$schema` СЛЕДУЕТ указывать для редактора, но runtime не загружает её по сети.

Неизвестные поля ЗАПРЕЩЕНЫ.

Правила идентичности:

- `id` соответствует `^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$`, максимум 96 символов;
- `id` никогда не меняется между версиями одного продукта;
- `version` имеет вид `MAJOR.MINOR.PATCH` с допустимым prerelease и без build metadata;
- любое изменение кода, assets, manifest или requirements требует увеличения версии;
- уже опубликованный asset под существующей версией ЗАПРЕЩЕНО заменять.

### 5.2. Presentation

Обязательный объект:

```json
"presentation": {
  "display_name": "Browser Rewards",
  "description": "Полное описание назначения, ограничений, результата и ожидаемых внешних эффектов.",
  "assets": {
    "icon": "assets/icon.png",
    "image": "assets/image.webp"
  }
}
```

Правила:

- `display_name`: 1–120 символов, пользовательское название без версии;
- `description`: 1–4000 символов, полное пользовательское объяснение;
- `assets` содержит ровно `icon` и `image`;
- короткое `description` верхнего уровня и полное `presentation.description` не должны противоречить друг другу;
- маркетинговые обещания, которые код не гарантирует, ЗАПРЕЩЕНЫ.

### 5.3. Compatibility и runtime

Новый пакет ОБЯЗАН объявлять:

```json
"compatibility": {
  "hub": ">=0.6.3",
  "python": ">=3.12,<3.13",
  "os": ["darwin"]
}
```

`compatibility.hub` для `/2` ОБЯЗАН быть не ниже `>=0.6.3`; сейчас поддерживается только форма `>=x.y.z`. `compatibility.python` и `compatibility.os` являются обязательной авторской декларацией. Installer проверяет минимальную версию Hub и форму полей, но пока не сопоставляет `os` с текущей системой и не исполняет Python constraint. Поля архитектуры в manifest нет. Поэтому автор ОБЯЗАН указывать только реально протестированные release OS/architecture и выполнить собственный fail-closed runtime check до первого side effect.

Основной поставляемый desktop artifact 0.6.4 — macOS arm64. В репозитории есть Windows x64 build target, но его наличие не доказывает, что конкретный плагин и все native wheels протестированы на Windows. Packaged Linux target отсутствует. Копировать `darwin`, `win32`, `linux` в manifest «на всякий случай» ЗАПРЕЩЕНО.

Объект `runtime`:

- `type`: строго `python`;
- `entrypoint`: `package.module:function`;
- `protocol`: строго `soft-hub-jsonl/1`;
- `state_model`: `stateless`, `resumable` или `externally_reconciled`;
- `requirements`: строго `requirements.txt` для стандартного layout;
- `safe_stop`: обязательный boolean;
- `heartbeat_seconds`: integer `5..300`, рекомендуемое значение `15`.

`heartbeat_seconds` проходит проверку manifest, но runner 0.6.4 не использует его как watchdog, не убивает зависший процесс и не реализует resume. Плагин ОБЯЗАН самостоятельно ставить bounded timeout на каждую сеть/браузер/дочерний процесс и общий deadline на action/account. Heartbeat показывает активность, но не доказывает прогресс и не заменяет timeout.

`safe_stop: true` разрешено только если плагин регулярно проверяет отмену, прекращает создание новой внешней работы и выполняет bounded cleanup. Это обещание автора, а не автоматически доказанная гарантия.

На macOS Hub даёт cooperative process-group `SIGTERM` и через 10 секунд усиливает остановку. На Windows текущий `process.terminate()` не гарантирует доставку Python cancellation flag и cleanup даже при `safe_stop: true`. Cross-platform write-софт ОБЯЗАН учитывать force termination и reconciliation; нельзя описывать Windows stop как гарантированно мягкий.

### 5.4. Permissions

Top-level `permissions.secrets` — точное объединение secret kinds всех actions. Допустимы только:

- `evm_private_key`;
- `proxy`;
- `email`;
- `email_password`;
- `twitter`;
- `adspower_profile`;
- `capsolver_api_key`;
- `adspower_api_key`;
- `referral_code`;
- `referrer_code`.

Каждый action нового софта ОБЯЗАН иметь собственный `permissions.secrets`. Нельзя пользоваться legacy fallback на top-level права. Каждый action получает минимально необходимый набор, а union наборов всех actions ОБЯЗАН точно совпадать с top-level набором.

Остальные поля:

- `network` — точный список host names без схемы, path, credentials и wildcard;
- `chains` — все положительные chain IDs, на которых возможен chain write;
- `financial_risk` — максимальный риск actions: `none`, `testnet` или `mainnet`;
- `browser` — `true` только если код автоматизирует браузер;
- `local_services` — точный список внешних локальных сервисов.

Если запрошен `adspower_profile` или `adspower_api_key`, manifest ОБЯЗАН содержать:

```json
"browser": true,
"local_services": ["adspower"]
```

Перечисление host/service не создаёт sandbox. Код ОБЯЗАН самостоятельно запретить неожиданный endpoint и прямое подключение в обход требуемого proxy/profile.

### 5.5. Actions

Каждый элемент `actions` ОБЯЗАН содержать:

- `id` по regex `^[a-z][a-z0-9_-]*$`;
- `name` — понятный глагол + объект, до 80 символов;
- `description` — явное описание внешнего эффекта, до 300 символов;
- `risk`;
- `account_mode`;
- `permissions.secrets`;
- `resources.account` и `resources.settings`;
- `options`.

Допустимые risks:

| `risk` | Реальное поведение |
|---|---|
| `read` | Только чтение; нет POST/PUT/PATCH/DELETE, подписи, click/submit, изменения browser/profile state. |
| `external_write` | Нефинансовая мутация: регистрация, claim, отправка формы, изменение waitlist/profile, browser click с внешним эффектом. |
| `testnet_write` | Любая подпись или отправка testnet transaction. |
| `mainnet_write` | Любая mainnet transaction, off-chain order/signature с финансовым эффектом или управление активами. |

Любой write ОБЯЗАН использовать `account_mode: one_or_more`. `mainnet_write` дополнительно ОБЯЗАН иметь уникальную понятную `confirmation_phrase`; у других risks это поле ЗАПРЕЩЕНО. Testnet gate принадлежит Hub и требует `TESTNET`; плагин не должен рисовать второй checkbox.

Если browser automation только открывает страницу и читает DOM, action МОЖЕТ быть `read`. Если он нажимает кнопку, логинится с созданием сессии, отправляет форму, делает claim или меняет внешний профиль, он ОБЯЗАН быть как минимум `external_write`.

### 5.6. Resources и немедленный preflight

`actions[].resources` — пользовательская декларация того, что должно быть настроено **до создания subprocess**. `permissions.secrets` отвечает за выдачу значения коду; `resources` отвечает за понятное предупреждение пользователю. Для нового софта ОБЯЗАТЕЛЬНЫ оба механизма.

Допустимые account resources:

| `resources.account` | Соответствующее secret permission | Значение |
|---|---|---|
| `private_key` | `evm_private_key` | EVM private key выбранного аккаунта. |
| `proxy` | `proxy` | HTTP proxy выбранного аккаунта. |
| `email` | `email` | Email выбранного аккаунта. |
| `email_password` | `email_password` | Пароль email выбранного аккаунта. |
| `twitter` | `twitter` | Twitter/X credential выбранного аккаунта. |
| `adspower_profile` | `adspower_profile` | AdsPower profile ID выбранного аккаунта. |
| `referral_code` | `referral_code` | Собственный код выбранного аккаунта, который получают его рефералы. |
| `referrer` | `referrer_code` | Эффективный входящий код: внешний либо собственный код выбранного в Hub аккаунта-реферера. |

Допустимые global settings resources:

| `resources.settings` | Соответствующее secret permission | Значение |
|---|---|---|
| `capsolver` | `capsolver_api_key` | Один общий Capsolver API key. |
| `adspower_api` | `adspower_api_key` | Один общий AdsPower API key. |

Глобальные Capsolver/AdsPower API keys должны содержать минимум 4 символа: более короткое значение Hub отклоняет, потому что exact-value redactor намеренно не заменяет строки короче четырёх символов.

Инварианты нового софта:

1. `resources` содержит ровно `account` и `settings`, даже если один список пуст.
2. Каждый resource имеет соответствующий permission выбранного action.
3. Каждый permission из таблиц имеет соответствующий resource. Для `email` и `email_password` используются два независимых resource kinds; требовать один из них «заодно» без фактического чтения ЗАПРЕЩЕНО.
4. Action, которому нужны и адрес, и пароль email, ОБЯЗАН объявить оба permissions и оба account resources.
5. Action с account resources ОБЯЗАН иметь `account_mode: one_or_more`.
6. Global settings выдаются отдельно через `context.settings`; они никогда не являются account resource. Action только с global settings МОЖЕТ иметь `account_mode: none`, если его предметная логика не относится к аккаунтам и risk допускает такой режим.
7. Новый код ОБЯЗАН читать Capsolver как `context.settings.secret("capsolver")`, а AdsPower API key как `context.settings.secret("adspower_api")`.
8. Секреты ЗАПРЕЩЕНО запрашивать через `options` или `input()`.

Referral-aware action дополнительно ОБЯЗАН соблюдать точное соответствие:

- `resources.account: ["referral_code"]` ↔ `permissions.secrets: ["referral_code"]` ↔ `account.secret("referral_code")`;
- `resources.account: ["referrer"]` ↔ `permissions.secrets: ["referrer_code"]` ↔ `account.secret("referrer_code")`.

Наличие хотя бы одного referral grant/resource требует `compatibility.hub: ">=0.6.4"`. Обычный action контракта `/2`, не использующий реферальную сеть, сохраняет общий минимум `>=0.6.3`. Подменять `referrer` собственным кодом аккаунта, передавать ID родителя в плагин либо запрашивать оба кода «на всякий случай» ЗАПРЕЩЕНО.

Hub хранит собственный код, ссылку на аккаунт-реферер либо внешний входящий код внутри зашифрованного account payload. Связь с Hub-аккаунтом и внешний код взаимоисключающие. Hub атомарно проверяет неизвестные ID, self-link и циклы по всему графу; API/UI получают только configured-флаги, вид связи и безопасные labels, но никогда не значения кодов. Длина любого сохраняемого реферального кода — `4..2048` символов без control characters. Re-import обязан сохранять граф, удаление родителя снимает прямые связи детей, а plaintext account export граф и коды не включает.

Для разового публичного кода, который намеренно не связан с графом аккаунтов, допускается отдельный action option с однозначным именем вроде `manual_referral_code`, `type: string`, `minLength: 4`, `maxLength: 2048`. Это узкое исключение допустимо только для несекретного одноразового значения: action НЕ объявляет resource `referrer`, entrypoint не сохраняет и не возвращает значение, а UI/лог/results не используются как хранилище. Если код должен переживать запуск, скрываться как credential или определять связь аккаунтов, option ЗАПРЕЩЁН — используется только Vault-ресурс `referrer`.

Host 0.6.4 выполняет первичный admission-preflight до постановки run в очередь: проверяет состояние Vault, выбранные аккаунты и non-secret configured-флаги каждого объявленного account/global resource, а UI сразу показывает конкретный недостающий тип. При существующем закрытом Vault одиночный start и batch start возвращают `423 Locked` до создания или replay-проекции run. После получения execution slot runner повторно расшифровывает и проверяет обязательные значения до создания subprocess. Независимо от обоих host checks entrypoint ОБЯЗАН повторить fail-closed проверку выданных значений до первого network/browser/write side effect.

`actions[].resources` и `presentation` пока остаются optional в runtime-валидаторе только для legacy-пакетов. Приёмка нового пакета без них ЗАПРЕЩЕНА.

### 5.7. Options

`actions[].options` по `/2` ОБЯЗАН присутствовать даже для действия без параметров и использовать закрытый плоский subset JSON Schema. Корневой объект содержит **ровно** четыре поля:

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

Нормативные правила:

- option key ОБЯЗАН быть `snake_case` по `^[a-z][a-z0-9_]{0,63}$`; всего допускается не более 40 полей;
- допустимы только primitive types `boolean`, `string`, `integer`, `number`; arrays, nested objects, `null`, `$ref`, conditional schema и произвольные дополнительные keywords ЗАПРЕЩЕНЫ;
- каждое поле, включая зарезервированный compatibility-флаг, ОБЯЗАНО иметь непустые `type`, `title`, `description` и `x-ui`; renderer может скрыть служебный флаг, но это не ослабляет strict manifest;
- `enum` допускается только как непустой уникальный список строк и ОБЯЗАН иметь полный mapping `x-ui.enum_labels` для всех значений;
- свободная строка ОБЯЗАНА иметь `maxLength` не более 16 000; `minLength` МОЖЕТ уточнять нижнюю границу; `pattern` в `/2` ЗАПРЕЩЁН, поскольку Hub его не исполняет — предметный формат повторно проверяет entrypoint;
- `integer` и `number` ОБЯЗАНЫ иметь конечные `minimum` и `maximum`; положительный `multipleOf` задаётся, когда нужен фиксированный шаг;
- необязательное поле ОБЯЗАНО иметь безопасный `default` правильного типа; required-поле МОЖЕТ не иметь default, только если без явного выбора пользователя действительно нельзя безопасно продолжать;
- неизвестные option values, missing required, типы, enum, numeric bounds/step и string lengths backend проверяет до доступа к Vault; entrypoint повторяет предметную проверку до side effect;
- secrets, адреса доверенных contracts/RPC, chain ID для подписи и иные security-critical policy values через options ЗАПРЕЩЕНЫ.

`x-ui` — обязательная presentation-метаинформация каждого поля:

| Поле | Правило |
|---|---|
| `group` | Короткое понятное имя смысловой группы, обязательно. |
| `order` | Integer `0..1000`, стабильно задающий порядок внутри формы; значение ОБЯЗАНО быть уникальным среди всех options action. |
| `control` | `input` либо `textarea`; `textarea` допустим только для свободной строки. |
| `placeholder` | Не пример секрета и не замена description; максимум 160 символов. |
| `unit` | Короткая единица измерения до 24 символов, например `сек` или `попыток`; разрешена только `integer`/`number`. |
| `advanced` | `true` убирает редко меняемый параметр в раскрываемую группу дополнительных настроек. Одна `group` не может смешивать основные и advanced-поля. |
| `enum_labels` | Обязательный для enum полный mapping каждого string value в непустую понятную подпись до 120 символов. |

На основном уровне формы ЗАПРЕЩЕНО иметь более 7 параметров (`advanced` отсутствует или равен `false`); для содержательной формы СЛЕДУЕТ целиться в 5–7 основных параметров. Более редкие параметры ОБЯЗАНЫ быть сгруппированы и отмечены `advanced: true`; если действие всё равно требует длинной конфигурации, его СЛЕДУЕТ разделить на несколько понятных actions. `title` и `description` отвечают, что изменится, в каких единицах, каков безопасный default и какой внешний риск связан с выбором.

Options существуют только для **одного запуска**: Hub не обещает сохранять их как постоянные настройки софта. One-click batch использует manifest default для необязательного поля; required boolean может получить `false`, а required string enum — первое объявленное значение. Любое другое required-поле без default делает action непригодным для запуска пачкой и требует отдельной формы. Поэтому безопасный batch-путь ОБЯЗАН иметь полностью определённые defaults и не зависеть от параметров предыдущего run.

## 6. Минимальный манифест нового browser-софта

Следующий пример соответствует полям schema 1 и обязательствам `/2`. Реальные host names, действия и тексты должны отражать фактический код.

```json
{
  "$schema": "https://soft-hub.local/schemas/plugin-v1.json",
  "schema_version": 1,
  "contract_version": "SH-SOFTWARE-0.6/2",
  "id": "io.sprintray.browser-rewards",
  "name": "Browser Rewards",
  "version": "1.0.0",
  "description": "Автоматизирует получение browser-награды через отдельные профили AdsPower.",
  "author": "sprintray",
  "presentation": {
    "display_name": "Browser Rewards",
    "description": "Открывает привязанный к каждому аккаунту профиль AdsPower, проверяет готовность ресурсов и выполняет один заявленный сценарий. Результат и статус отображаются отдельно по каждому аккаунту.",
    "assets": {
      "icon": "assets/icon.png",
      "image": "assets/image.webp"
    }
  },
  "compatibility": {
    "hub": ">=0.6.3",
    "python": ">=3.12,<3.13",
    "os": ["darwin"]
  },
  "runtime": {
    "type": "python",
    "entrypoint": "plugin.main:run",
    "protocol": "soft-hub-jsonl/1",
    "state_model": "externally_reconciled",
    "requirements": "requirements.txt",
    "safe_stop": true,
    "heartbeat_seconds": 15
  },
  "permissions": {
    "secrets": [
      "email",
      "adspower_profile",
      "capsolver_api_key",
      "adspower_api_key"
    ],
    "network": [
      "local.adspower.net",
      "api.capsolver.com",
      "rewards.example.com"
    ],
    "chains": [],
    "financial_risk": "none",
    "browser": true,
    "local_services": ["adspower"]
  },
  "actions": [
    {
      "id": "claim_reward",
      "name": "Получить награду",
      "description": "Открывает AdsPower-профиль и отправляет одну внешнюю заявку на награду.",
      "risk": "external_write",
      "account_mode": "one_or_more",
      "permissions": {
        "secrets": [
          "email",
          "adspower_profile",
          "capsolver_api_key",
          "adspower_api_key"
        ]
      },
      "resources": {
        "account": ["email", "adspower_profile"],
        "settings": ["capsolver", "adspower_api"]
      },
      "options": {
        "type": "object",
        "properties": {
          "max_attempts": {
            "type": "integer",
            "title": "Максимум попыток",
            "description": "Не более трёх попыток на аккаунт; безопасное значение — 1.",
            "minimum": 1,
            "maximum": 3,
            "default": 1,
            "x-ui": {
              "group": "Выполнение",
              "order": 10,
              "unit": "попыток",
              "advanced": false
            }
          }
        },
        "required": [],
        "additionalProperties": false
      }
    }
  ],
  "ui": {
    "accent": "#B9654B",
    "monogram": "BR"
  }
}
```

## 7. Python entrypoint и JSONL-протокол

### 7.1. Форма entrypoint

Entrypoint ОБЯЗАН быть sync- или async-функцией с одним аргументом `HubContext`:

```python
from soft_hub.sdk import HubContext


def run(context: HubContext) -> dict:
    ...
```

Он ОБЯЗАН:

- проверить `context.action_id` по явному allowlist;
- повторно проверить types/ranges всех options;
- использовать только `context.accounts`, выданные выбранному run;
- возвращать небольшой JSON-serializable `dict` с агрегатной summary;
- завершаться без `input()`, GUI prompt и чтения дополнительных строк stdin.

Если entrypoint возвращает не `dict`, Hub сохранит пустую summary. Несериализуемый объект приводит к failed run.

### 7.2. Правила stdout/stderr

Stdout зарезервирован под JSONL frames `soft-hub-jsonl/1`. Автор ЗАПРЕЩЕНО:

- печатать в stdout;
- заменять protocol emitter;
- самостоятельно формировать terminal frames;
- запускать дочерний процесс, наследующий protocol stdout;
- смешивать progress bar, Rich, tqdm или logging console handler с stdout.

Bootstrap после импорта перенаправляет обычный `print()` в stderr, чтобы случайный print не ломал протокол. Это аварийная защита, а не API логирования: stderr сохраняется как warning и может попасть в скачиваемый журнал.

Для телеметрии ОБЯЗАТЕЛЬНО использовать:

- `context.log(...)`;
- `context.progress(...)`;
- `context.account_state(...)`;
- `context.result(...)`.

Три malformed protocol frames приводят к принудительному завершению. Одна строка ограничена `65 536` декодированными Unicode-символами; её UTF-8-представление может быть больше `64 KiB`. Весь run ограничен `50 000` строками. Софт ОБЯЗАН агрегировать повторяющиеся события и не логировать каждый network poll.

### 7.3. HubContext и секреты

Публичные поля `HubAccount`: `id`, `label`, `evm_address`. По нормативному контракту нового софта account secret ОБЯЗАН читаться только через:

```python
value = account.secret("adspower_profile")
own_referral_code = account.secret("referral_code")
incoming_referrer_code = account.secret("referrer_code")
```

Global setting читается отдельно через `HubSettings`:

```python
adspower_api_key = context.settings.secret("adspower_api")
capsolver_api_key = context.settings.secret("capsolver")
```

Имена в `context.settings.secret()` совпадают с `actions[].resources.settings`, а не с внутренними permission names. Если permission не выдан или значение отсутствует, `secret()` поднимает `KeyError`. Хотя `HubAccount` и `HubSettings` технически реализуют `Mapping` для legacy-совместимости, доступ к секретам через `[]`, iteration и `dict(...)` для нового софта ЗАПРЕЩЁН. Нельзя сериализовать эти объекты или передавать их целиком сторонней библиотеке. Их `repr` скрыт только как дополнительная защита.

`plugin_root` предназначен для чтения immutable payload. Запись туда ЗАПРЕЩЕНА. `scratch_dir` уникален для run и одновременно является cwd, но в текущей реализации может сохраниться после завершения. Поэтому secrets, cookies, HAR, screenshots, browser profiles и raw responses в scratch ЗАПРЕЩЕНЫ. Любой допустимый временный файл ОБЯЗАН иметь случайное имя без account/secret values и удаляться в bounded `finally`.

## 8. Per-account lifecycle и прогресс

### 8.1. Обязательная последовательность

Для action с `account_mode: one_or_more` Hub создаёт строки аккаунтов в состоянии `queued`. Для каждого аккаунта, предметная обработка которого началась, плагин ОБЯЗАН отправить:

1. один `account_state(status="running", ...)` перед предметной работой;
2. ноль или больше промежуточных `log/progress`;
3. ровно один terminal `account_state`.

Допустимые terminal statuses:

| Status | Когда использовать |
|---|---|
| `succeeded` | Все обязательные шаги аккаунта подтверждены. Progress становится 1. |
| `partial` | Часть независимых шагов подтверждена, часть завершилась известной ошибкой; внешний итог не неоднозначен. |
| `failed` | До необратимого внешнего эффекта получена известная ошибка. |
| `skipped` | Аккаунт сознательно не обрабатывался по безопасному правилу. |
| `blocked` | Не выполнен предметный precondition: баланс, доступность сервиса, статус аккаунта и т. п. |
| `needs_attention` | Внешний эффект мог произойти, но подтверждение итога отсутствует, либо cleanup/reconciliation неоднозначны. |
| `cancelled` | Отмена произошла до неоднозначного внешнего эффекта, cleanup подтверждён. |

После terminal status второе terminal-событие и возврат к `running` ЗАПРЕЩЕНЫ и будут отклонены runner.

Если отмена произошла до начала обработки аккаунта, Hub сам переводит оставшийся `queued` state в `cancelled`; при неоднозначности host-level write — в `needs_attention`. При обычном завершении без terminal account state Hub намеренно покажет `unknown`. Текст `«успешно»` в log/result не меняет lifecycle.

Единственное legacy-исключение core 0.6.2 — точечный bridge для известных first-party Checkpoint/Sekai/Umia `1.0.0`: только их заранее перечисленные actions и ровно один типизированный account-scoped `account_summary` могут восстановить старую проекцию. Этот bridge НЕ является частью контракта нового софта, не применяется к сторонним ID/версиям и не отменяет требование terminal `account_state`.

`reviewed` и `reconciled` — не protocol/account statuses плагина, а состояния **всего run**, которые создаёт только Hub по действию оператора:

- `failed` означает известную локальную ошибку без неоднозначного внешнего write. Такой run не должен удерживать safety lease, его разрешено повторить. После чтения журнала оператор может отметить ошибку просмотренной: Hub меняет run на `reviewed`, убирает его из текущего alert, но сохраняет error, events, results и account states в истории. Review отклоняется, если у failed run вопреки инварианту остался lease;
- `needs_attention` означает, что внешний итог потенциального write не доказан. Run удерживает safety lease, и повтор того же account/scope блокируется. Только после реальной внешней проверки и точного подтверждения `RECONCILED` Hub меняет run на `reconciled` и снимает leases.

Плагин ЗАПРЕЩЕНО эмитить `reviewed` или `reconciled`, использовать их в `context.result(status=...)` или изображать review как reconciliation. Просмотр известной ошибки не является утверждением об успехе и не переписывает исторический failed account outcome.

### 8.2. Stage, message и progress

- `stage` — стабильный machine ID по `^[a-z][a-z0-9_.-]{0,63}$`;
- stage ЗАПРЕЩЕНО использовать как пользовательское предложение;
- `message` — короткий безопасный текст для пользователя без секретов и внутренних traceback;
- per-account `progress` — конечное JSON-число `0..1`, только монотонно; boolean, строка, NaN/infinity, выход за границы и регресс являются protocol error, Hub их не clamp-ит;
- для `account_mode: one_or_more` ЕДИНСТВЕННЫЙ расчётный источник — `account_state/progress` либо `context.progress(..., account_id=...)`; run-level event без `account_id` сохраняется как telemetry, но не влияет на процент запуска;
- Hub вычисляет run progress как `AVG(account.progress)` по всем выбранным аккаунтам, включая ещё не начатые строки `queued=0`; один быстрый worker поэтому не завышает общий результат;
- global `context.progress` без `account_id` разрешён как расчётный источник только для `account_mode: none`.

Рекомендуемые стабильные stages: `preflight`, `browser_starting`, `browser_attaching`, `auth_check`, `automation`, `submitting`, `confirming`, `cleanup`, `completed`, `cancelled`, `needs_reconciliation`.

Hub может локализовать известный stage или показать общий текст. Поэтому понятный `message` ОБЯЗАТЕЛЕН на каждом существенном переходе.

До реализации action автор ОБЯЗАН составить конечный progress plan из фактических единиц работы и весов. Нормативная шкала:

| Точка | Требование |
|---|---|
| Вход/preflight | Первое состояние `running`, `0 < progress <= 0.10`. |
| Промежуточная работа | Для workflow дольше 2 секунд либо с 3+ внешними/предметными шагами — минимум три различных milestone в `0.10..0.95`. |
| Повторяемая работа | `base + span * completed_weight / total_weight`; numerator увеличивается только после завершённой единицы, denominator фиксируется до её запуска. |
| Успех | Ровно `1.0` только после подтверждения обязательного результата и cleanup. |
| Известная ошибка/skipped/блокировка/отмена/неоднозначность | Progress не передаётся в terminal `account_state`; если legacy adapter всё же передаст его, Hub проигнорирует значение и сохранит последний подтверждённый процент. |
| Partial | `1.0` допустим только если весь заявленный план обработан и все частичные итоги известны; иначе сохраняется последний milestone. |

Milestone разрешено повышать только после проверяемого факта: валидирован preflight, browser действительно attached, HTTP/RPC response получен и проверен, bounded item обработан, receipt подтверждён, journal fsync завершён, cleanup подтверждён. Progress по elapsed time, бесконечная CSS/terminal-анимация, случайное приращение, повышение на каждом retry без bounded retry budget и вывод процента из текста log ЗАПРЕЩЕНЫ.

Если один внешний вызов долго не завершается, автор МОЖЕТ отправлять `heartbeat` или повторять тот же stage/message с тем же progress. Это показывает живость, не придумывая выполненную работу. Для параллельных workers progress одного аккаунта должен сериализоваться; события разных аккаунтов Hub агрегирует независимо.

### 8.3. Параллельность

Софт МОЖЕТ обрабатывать аккаунты параллельно, но ОБЯЗАН:

- иметь явный малый concurrency limit;
- не разделять mutable client/session между аккаунтами;
- не использовать один AdsPower profile одновременно для двух workers;
- сериализовать lifecycle одного аккаунта;
- немедленно прекратить создание новых workers после cancel;
- дождаться завершения/cleanup всех созданных workers перед возвратом;
- учитывать, что порядок событий между threads недетерминирован.

Runtime emitter сериализует SDK emit-вызовы внутренним lock, но не делает бизнес-логику thread-safe.

## 9. Structured results

`context.result()` используется для долговременного предметного результата, а не для каждого шага.

Нормативные поля:

- `title` — понятный итог до 300 символов;
- `kind` — стабильный machine ID по стилю `snake_case`, максимум 80 символов;
- `status` — один из `succeeded`, `partial`, `failed`, `skipped`, `blocked`, `needs_attention`;
- `account_id` — ОБЯЗАТЕЛЕН для результата конкретного аккаунта и должен принадлежать run;
- `data` — небольшой JSON object с публичными предметными значениями.

Стабильная семантика results:

- для каждого начатого аккаунта, у которого появился предметный итог, action ОБЯЗАН создать ровно один финальный account-scoped result; дополнительные предметные артефакты разрешены только с отдельными документированными `kind`, а не как копии каждого log;
- аккаунт, оставшийся `queued` и отменённый Hub до предметной обработки, МОЖЕТ не иметь result; action с `account_mode: none`, создающий долговременный предметный итог, ОБЯЗАН создать хотя бы один run-scoped result без `account_id`;
- `kind` — публичный стабильный идентификатор схемы результата. Его значение и смысл существующих keys в `data` нельзя менять внутри совместимой major-версии; добавления должны быть backward-compatible, а breaking change требует нового kind либо major version;
- result `status` ОБЯЗАН совпадать с terminal account outcome по смыслу. Result не управляет lifecycle и никогда не заменяет `account_state`;
- для отмены до появления предметного итога result обычно не создаётся: `cancelled` не входит в допустимые result statuses;
- payload ОБЯЗАН быть небольшим и предметным. Bulk-таблицы, raw responses, traceback, DOM/HAR и event history относятся к журналу или внешнему безопасному хранилищу, а не к `data`.

Hub сохраняет results независимо от последующего обновления или удаления модуля, поэтому renderer и экспорт истории должны уметь читать старые `kind/data`. Текущий SDK/runner проверяет JSON-serializability, account membership, size/redaction boundaries и сохраняет переданные `kind/status`, но не может доказать их предметный смысл, соответствие terminal state или backward compatibility. Эти инварианты проверяются кодом плагина и acceptance tests.

Допустимые примеры data: публичный EVM address, chain ID, transaction hash, публичный remote operation ID, числовые counters, boolean flags. Даже публичные идентификаторы СЛЕДУЕТ минимизировать, если они позволяют связать пользователя между сервисами.

В results и возвращаемой summary ЗАПРЕЩЕНЫ private key, proxy, email, email password, Twitter credential, Capsolver/AdsPower keys, AdsPower profile ID, cookies, access tokens, authorization headers, raw HTTP payload, DOM dump, screenshot, путь к secret-bearing файлу и текст исключения сторонней библиотеки.

Return summary ОБЯЗАНА быть агрегатной, например:

```json
{"total": 10, "succeeded": 8, "failed": 1, "needs_attention": 1}
```

Summary не заменяет terminal `account_state`.

## 10. Безопасное логирование и скачиваемый журнал

### 10.1. Абсолютное правило автора

Секрет ЗАПРЕЩЕНО передавать в любой наблюдаемый канал даже в расчёте на redaction. Запрет распространяется на:

- `message` и `data` всех SDK events;
- results и return summary;
- exception text и traceback;
- stdout, stderr и Python logging;
- URL, query string, headers и request/response dumps;
- имя/путь файла, имя thread/process и CLI arguments;
- scratch, HAR, screenshot, DOM/HTML dump и crash report;
- метрики, hashes, base64/hex, маскированные фрагменты и производные значения, если по ним можно проверить или восстановить secret.

Оба реферальных значения (`referral_code`, `referrer_code`), внешний одноразовый код и глобальные API keys считаются secret-bearing независимо от того, опубликован ли такой код внешним сервисом. Их ЗАПРЕЩЕНО включать в message/data/result/summary/exception даже частично.

Нельзя логировать `repr(context)`, `repr(account)`, HTTP client/request, browser capabilities, Selenium command payload или полный response object. Разрешены только заранее сформированные безопасные сообщения и allowlisted public fields.

Исключения сторонних SDK ОБЯЗАНЫ переводиться в стабильные безопасные коды, например `adspower_unavailable`, а не интерполироваться через `f"{error}"`. Raw exception разрешено использовать только в локальном тесте без реальных secrets.

Это требование включает внешнюю границу entrypoint. Bootstrap 0.6.4 при необработанном исключении формирует failed-message из имени и текста exception и печатает traceback в stderr; redactor после этого является лишь последней защитой. Поэтому production-entrypoint ОБЯЗАН перехватить ожидаемые ошибки клиентов/SDK, классифицировать их в заранее определённый safe code/message без raw exception и завершить lifecycle каждого уже начатого аккаунта. Неизвестное исключение нельзя интерполировать или помещать в `data`; его безопасно классифицируют общим кодом, а детальную диагностику воспроизводят без production secrets. `except Exception: pass` и ложный success ЗАПРЕЩЕНЫ.

### 10.2. Defense-in-depth Hub

Скачиваемый технический журнал 0.6.4:

- доступен через token-authenticated endpoint конкретного run и является одним общим журналом софта по всем выбранным аккаунтам, а не набором per-account файлов;
- сохраняет единый порядок событий run; account-scoped событие связывается с аккаунтом через `account_id` и безопасный snapshot `account_label`;
- отдаётся как bounded UTF-8 JSONL attachment;
- не включает manifest и options;
- повторно пропускает сохранённые events и run metadata через redactor;
- скрывает известные secret fields (включая referral/referrer codes), exact выданные значения, private-key patterns, proxy, email, Authorization, Cookie, JWT и high-entropy tokens;
- ограничен `16 MiB`, имеет footer с количеством пропущенных событий;
- возвращает `Cache-Control: no-store` и `X-Soft-Hub-Redacted: true`.

Эта защита не обнаруживает все кодировки, разбиение секрета по нескольким событиям, нестандартный формат, изображение или содержимое файла. Автор всё равно несёт ответственность за правило 10.1. Скачанный журнал следует считать чувствительным operational artifact и не публиковать без ручной проверки.

### 10.3. Правильные сообщения

Правильно:

```python
context.log(
    "AdsPower-профиль готов к подключению",
    account_id=account.id,
    data={"attempt": attempt, "service": "adspower"},
)
```

ЗАПРЕЩЕНО:

```python
context.log(f"profile={profile_id} api_key={api_key} email={email}")
context.log(f"request failed: {error}")
```

## 11. Канонический workflow AdsPower

Transport, авторизацию и актуальные поля ответа автор ОБЯЗАН сверять с первичной документацией AdsPower: [обзор Local API](https://localapi-doc-en.adspower.com/docs/Rdw7Iu), [получение профиля](https://localapi-doc-en.adspower.com/docs/u8m2Ie) и [официальные примеры](https://localapi-doc-en.adspower.com/docs/K4IsTq). Hub намеренно не фиксирует в своём SDK конкретный localhost endpoint или способ авторизации: они принадлежат интеграции плагина и могут меняться независимо от контракта Hub.

### 11.1. Декларация

Любой AdsPower action ОБЯЗАН одновременно объявить:

- `permissions.browser: true`;
- `permissions.local_services` с точным значением `adspower`;
- action permission `adspower_profile`;
- action permission `adspower_api_key`;
- `resources.account: [ ..., "adspower_profile" ]`;
- `resources.settings: [ ..., "adspower_api" ]`.

Если сценарий использует Capsolver, он дополнительно ОБЯЗАН объявить `capsolver_api_key` и resource `capsolver`. Email, Twitter, proxy и private key объявляются только если их реально читает выбранное действие.

### 11.2. Последовательность на каждый аккаунт

До создания workers entrypoint ОБЯЗАН один раз прочитать AdsPower profile ID всех выбранных аккаунтов в памяти, проверить непустоту и найти точные дубли. Один profile ID нельзя назначать двум выбранным Hub accounts: оба конфликтующих аккаунта завершаются `blocked` до вызова AdsPower, а само значение никуда не логируется. Vault хранит profile ID зашифрованно, но не гарантирует их уникальность между accounts; Hub leases привязаны к `account_id` и сами по себе этот конфликт не предотвращают.

Канонический flow:

1. Проверить `context.check_cancelled()`.
2. Получить profile ID через `account.secret("adspower_profile")`, а общий API key через `context.settings.secret("adspower_api")`; проверить непустоту и допустимую длину, не логируя значения.
3. Выполнить bounded read-only health/auth check локального AdsPower API.
4. Убедиться, что profile ID разрешается ровно в один профиль и профиль доступен. Ноль или несколько совпадений — `blocked` до side effect.
5. Зафиксировать, был ли профиль уже запущен до текущего run.
6. Запустить профиль через официальный локальный AdsPower API только при необходимости; сохранить returned connection endpoint только в памяти.
7. Подключиться к returned WebDriver/CDP endpoint. ЗАПРЕЩЕНО копировать browser profile directory или запускать отдельный Chrome с этими данными.
8. Выполнить сценарий с timeout на навигацию, ожидание DOM и каждый внешний запрос. Перед каждым submit/click и между retry проверить cancel.
9. Сразу после потенциального внешнего эффекта считать состояние `write_may_have_happened`, пока результат не подтверждён чтением/API/DOM.
10. В `finally` закрыть созданные run вкладки, detach driver, завершить свои workers и остановить профиль только если его запустил текущий run.
11. Не останавливать профиль, который был открыт до run, и не завершать чужой browser process.
12. Если stop/detach/confirmation не дали однозначного результата после возможного write, terminal status ОБЯЗАН быть `needs_attention`.

AdsPower API key является общей настройкой `context.settings`, а profile ID — отдельным секретом `HubAccount`. Capsolver также является общей настройкой `context.settings`. Их нельзя менять местами, хранить в options или кэшировать в plugin-owned файле.

Proxy для браузерного сценария СЛЕДУЕТ настраивать в самом AdsPower profile. Плагин ЗАПРЕЩЕНО незаметно менять постоянный proxy/fingerprint/profile configuration. Отдельное действие, которое делает такую мутацию, ОБЯЗАНО называться явно и иметь `risk: external_write`.

Hub не устанавливает AdsPower и не управляет его лицензией. Host preflight до subprocess проверяет только configured-флаги AdsPower API key/profile ID и не делает live request к локальному сервису. Поэтому entrypoint ОБЯЗАН до первого browser/write side effect выполнить bounded live preflight: доступность Local API, авторизацию, однозначное существование каждого profile и совместимость returned endpoint. Недоступный сервис, неверный API key, неизвестный/дублирующийся profile ID дают понятный `blocked`, а не просьбу искать причину в terminal log.

### 11.3. Cancellation

Browser waits ОБЯЗАНЫ быть короткими или разбитыми на polling с cancel checks. Нельзя зависать в одном WebDriver call дольше объявленного timeout.

Плагин ЗАПРЕЩЕНО оставлять detached driver, browser child process, thread или async task. На Windows cooperative signal может быть менее надёжным, поэтому cleanup и external reconciliation ОБЯЗАНЫ быть рассчитаны также на force termination.

## 12. Минимальный entrypoint: lifecycle, AdsPower и безопасная отмена

Пример показывает контракт orchestration. `AdsPowerClient` и `automate_claim` должны быть реализованы и протестированы внутри пакета; они не могут печатать secrets или запускать detached processes.

```python
from __future__ import annotations

from soft_hub.sdk import CancelledError, HubAccount, HubContext

from plugin.adspower import AdsPowerClient, automate_claim


def run(context: HubContext) -> dict:
    if context.action_id != "claim_reward":
        raise ValueError("unsupported_action")

    max_attempts = context.options.get("max_attempts", 1)
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError("invalid_max_attempts")

    counters = {
        "total": len(context.accounts),
        "succeeded": 0,
        "partial": 0,
        "failed": 0,
        "skipped": 0,
        "blocked": 0,
        "needs_attention": 0,
        "cancelled": 0,
    }
    for account in context.accounts:
        context.check_cancelled()
        status = _run_account(context, account, max_attempts)
        counters[status] = counters.get(status, 0) + 1
    return counters


def _run_account(context: HubContext, account: HubAccount, max_attempts: int) -> str:
    context.account_state(
        account.id,
        status="running",
        stage="preflight",
        progress=0.01,
        message="Проверяем browser-ресурсы",
    )

    try:
        profile_id = account.secret("adspower_profile")
        api_key = context.settings.secret("adspower_api")
        email = account.secret("email")
        capsolver_key = context.settings.secret("capsolver")
    except KeyError:
        context.account_state(
            account.id,
            status="blocked",
            stage="resource_missing",
            message="Не заполнен обязательный ресурс профиля",
        )
        return "blocked"

    client = AdsPowerClient(api_key=api_key, timeout_seconds=15)
    session = None
    write_may_have_happened = False
    cancelled: CancelledError | None = None
    terminal_status = "failed"
    terminal_stage = "automation_failed"
    terminal_message = "Сценарий завершился известной ошибкой"
    safe_result: dict = {}

    try:
        context.check_cancelled()
        client.require_ready_profile(profile_id)
        context.account_state(
            account.id,
            status="running",
            stage="browser_starting",
            progress=0.10,
            message="Запускаем изолированный browser-профиль",
        )
        session = client.start_or_attach(profile_id)
        context.check_cancelled()

        context.account_state(
            account.id,
            status="running",
            stage="automation",
            progress=0.30,
            message="Выполняем заявленный сценарий",
        )
        # Callback вызывается непосредственно перед первым submit/click.
        def mark_external_write() -> None:
            nonlocal write_may_have_happened
            context.check_cancelled()
            write_may_have_happened = True

        safe_result = automate_claim(
            session=session,
            email=email,
            capsolver_key=capsolver_key,
            max_attempts=max_attempts,
            before_submit=mark_external_write,
            check_cancelled=context.check_cancelled,
        )
        context.account_state(
            account.id,
            status="running",
            stage="result_confirmed",
            progress=0.85,
            message="Итог сценария подтверждён",
        )
        # Функция возвращается только после независимого подтверждения итога.
        write_may_have_happened = False
        terminal_status = "succeeded"
        terminal_stage = "completed"
        terminal_message = "Награда подтверждена"
    except CancelledError as error:
        cancelled = error
        terminal_status = "needs_attention" if write_may_have_happened else "cancelled"
        terminal_stage = "needs_reconciliation" if write_may_have_happened else "cancelled"
        terminal_message = (
            "Требуется проверить внешний итог"
            if write_may_have_happened
            else "Работа остановлена до внешнего действия"
        )
    except Exception:
        terminal_status = "needs_attention" if write_may_have_happened else "failed"
        terminal_stage = "needs_reconciliation" if write_may_have_happened else "automation_failed"
        terminal_message = (
            "Требуется проверить внешний итог"
            if write_may_have_happened
            else "Сценарий завершился известной ошибкой"
        )
    finally:
        try:
            if session is not None:
                client.detach_and_stop_if_owned(session)
        except Exception:
            terminal_status = "needs_attention"
            terminal_stage = "needs_reconciliation"
            terminal_message = "Не удалось подтвердить browser cleanup"

    if terminal_status == "succeeded":
        context.result(
            "Награда подтверждена",
            kind="reward_claim",
            status="succeeded",
            account_id=account.id,
            data={"confirmed": True, "attempts": safe_result.get("attempts", 1)},
        )

    context.account_state(
        account.id,
        status=terminal_status,
        stage=terminal_stage,
        progress=1.0 if terminal_status == "succeeded" else None,
        message=terminal_message,
    )
    if cancelled is not None:
        raise cancelled
    return "needs_attention" if terminal_status == "needs_attention" else (
        "succeeded" if terminal_status == "succeeded" else "failed"
    )
```

В production-коде generic `Exception` ЗАПРЕЩЕНО выводить в event. На внешней границе его нужно классифицировать в собственную ошибку с безопасным кодом; неоднозначный write — всегда `needs_attention`.

## 13. Риск, leases, идемпотентность и reconciliation

### 13.1. Leases

Для `testnet_write/mainnet_write` Hub резервирует выбранный account по каждому объявленному chain ID. Для `external_write` Hub резервирует отдельный per-account external-service scope. Это предотвращает часть параллельных конфликтов между Hub runs.

Lease не гарантирует:

- отсутствие работы тем же аккаунтом вне Hub;
- уникальность AdsPower profile ID между разными Hub accounts;
- правильный фактический chain ID;
- атомарность нескольких внешних систем;
- отсутствие detached child process.

Автор ОБЯЗАН иметь собственные fail-closed проверки identity, chain, remote state и повторного запуска.

### 13.2. Идемпотентность

Каждая внешняя мутация ОБЯЗАНА иметь явно описанную стратегию идемпотентности:

- использовать remote idempotency key, если API его поддерживает;
- не повторять submit после timeout, пока внешний state не проверен;
- перед повтором читать chain/API/DOM и доказывать, что операция отсутствует;
- сохранять и показывать безопасный public operation ID/transaction hash;
- ограничивать retry и применять backoff с jitter;
- отделять retry чтения от retry записи.

`run_id` пригоден для дедупликации повторной попытки внутри одного run, но новый run получает новый ID. Поэтому защита между runs ОБЯЗАНА опираться на бизнес-ключ операции или внешнюю сверку, а не только на `run_id`.

Host idempotency batch admission предотвращает повторное создание одной и той же пачки после неопределённого ответа API Hub. Он не делает идемпотентными внешние действия плагинов.

### 13.3. Остановка

Плагин ОБЯЗАН вызывать `context.check_cancelled()`:

- перед каждым аккаунтом;
- перед каждым внешним write;
- между retry;
- внутри длительного polling;
- до создания browser/child process;
- до перехода к следующему независимому шагу.

`CancelledError` нельзя поглощать через `except BaseException`. Cleanup выполняется в `finally`, после чего исключение пробрасывается. Любой cleanup имеет timeout.

Если write мог уйти наружу, cancel, force stop, crash или timeout ОБЯЗАНЫ привести к `needs_attention`, а не к ложному `cancelled`/`failed`. Runner удержит lease до ручной сверки. Оператор снимает его только после проверки внешнего состояния и явного `RECONCILED`.

### 13.4. Reconciliation

Софт с неоднозначными writes ОБЯЗАН:

- использовать `state_model: externally_reconciled` либо документированную реально работающую resumable-модель;
- иметь отдельное read-only действие `reconcile` или иной проверенный способ восстановить истину из внешнего API/chain;
- не отправлять новую мутацию во время reconcile;
- выдавать понятный result: `confirmed`, `not_found` или `unknown`;
- никогда не объявлять `not_found` по одному временно пустому ответу без достаточного подтверждения;
- документировать оператору, что именно проверить перед `RECONCILED`.

Scratch не является контрактным durable checkpoint storage. `state_model: resumable` разрешён только при наличии реального устойчивого journal/store с миграциями и тестом восстановления после kill/restart.

Новый reconcile run не получает через `HubContext` logs, results, summary, options, scratch или checkpoint предыдущего run; Hub также не вызывает action `reconcile` автоматически. Поэтому действие сверки ОБЯЗАНО восстанавливать истину из внешней системы по текущему account identity и заранее известному безопасному business key либо public operation ID, доступному оператору/внешней системе независимо от transient контекста прошлого запуска. Если без прежнего контекста доказать итог невозможно, action возвращает `unknown`; оператор не должен вводить `RECONCILED` до ручной проверки. Само значение `state_model: stateless` не доказывает идемпотентность повторного write, а `resumable` не создаёт storage.

## 14. Network и зависимости

### 14.1. Network

Софт ОБЯЗАН:

- обращаться только к hosts, перечисленным в `permissions.network`, и локальным сервисам из `local_services`;
- проверять TLS certificates; `verify=False` и глобальное отключение warnings ЗАПРЕЩЕНЫ;
- ставить connect/read/total timeout;
- иметь bounded retries;
- не делать silent direct fallback, если для аккаунта требуется proxy;
- не передавать credentials в URL, если API допускает header/body;
- не логировать URL, если внешний протокол всё же требует credential query parameter;
- проверять chain ID, contract addresses и environment до подписи;
- ограничивать response size и не сохранять raw response.

Wildcard allowlist и endpoint, управляемый недоверенной option, ЗАПРЕЩЕНЫ. Redirect на неожиданный host должен быть отклонён либо проверен по тому же allowlist.

### 14.2. Python dependencies

`requirements.txt` ОБЯЗАН:

- закреплять прямые и транзитивные версии воспроизводимым lock/constraints-подходом;
- не использовать VCS URL, mutable branch, local absolute path и untrusted extra index;
- по возможности использовать hashes;
- проходить dependency vulnerability audit и license review;
- иметь wheels для каждой заявленной ОС/архитектуры либо доказанную build-процедуру;
- не содержать package, скачивающий browser/runtime/model при import.

`.venv` в пакет включать ЗАПРЕЩЕНО. Плагин не должен полагаться на библиотеки core Hub. Bootstrap удаляет core path после загрузки SDK, а plugin `.venv` имеет приоритет для зависимостей.

Hub подготавливает только Python requirements. Он не устанавливает AdsPower, Chrome, Playwright browser bundle, Node.js, системную библиотеку или драйвер. Browser-софт ОБЯЗАН использовать установленный AdsPower и возвращённый им supported connection/driver, либо явно блокировать запуск понятным preflight.

Runtime self-update, `pip install` во время run, динамическая загрузка executable, `shell=True`, `eval/exec` недоверенного текста, pickle недоверенных данных и detached daemon ЗАПРЕЩЕНЫ.

### 14.3. EVM-подпись и транзакции

Action с `evm_private_key` ОБЯЗАН выполнять подпись локально в памяти. Private key, seed и raw signed transaction ЗАПРЕЩЕНО отправлять в RPC/API/browser, помещать в URL, файл, result, summary или журнал. До каждой подписи код fail-closed проверяет как минимум:

- фактический `chainId` RPC и ожидаемую сеть;
- sender, destination contract/EOA, selector/calldata и отсутствие неожиданного delegate/proxy target;
- `value`, token, spender, amount, decimals и лимит approval;
- nonce, gas/fee limits и допустимые max-cost bounds;
- соответствие операции выбранному action и явно подтверждённому risk.

Unlimited approval, permit, arbitrary signature и blind signing для нового софта ЗАПРЕЩЕНЫ, если это не отдельное явно названное `mainnet_write` действие с точным описанием и acceptance review. До broadcast СЛЕДУЕТ выполнить доступную simulation/`eth_call`; после broadcast сохраняется только public chain ID, transaction hash и значения в явно указанных единицах. Ошибка/отмена между подписью, broadcast и receipt считается неоднозначной границей write и приводит к `needs_attention`, пока chain truth не сверена. Никогда не считать локально созданный hash доказательством включения транзакции.

## 15. UX-контракт

Софт считается частью одного продукта и ОБЯЗАН говорить с пользователем через Hub, а не через терминал.

Требования:

- `presentation.display_name` — человеческое имя без технического суффикса `bot/script/patch`, если он не часть бренда;
- короткое описание объясняет одно основное назначение;
- полное описание сообщает ресурсы, внешний эффект, сети, ограничения и итог;
- action name начинается с понятного действия: «Проверить», «Запустить», «Получить», «Сверить»;
- action description прямо говорит, только ли это чтение и что изменится;
- option labels содержат единицы измерения и безопасный default;
- ошибки preflight сообщают недостающий ресурс до запуска;
- account message отвечает «что происходит сейчас», не показывает machine exception;
- result отвечает «что получилось»;
- `needs_attention` объясняет, какую внешнюю систему проверить;
- нормальная работа не требует открыть technical log;
- `input()`, terminal prompt, ручное редактирование config и просьба установить Python пользователю ЗАПРЕЩЕНЫ.

Иконка и обложка не должны имитировать системные кнопки Hub и не должны содержать status badge, который устареет.

## 16. Сборка, checksums и GitHub release

### 16.1. Сборка

Архив ОБЯЗАН собираться из корня репозитория Hub штатной командой, а output — находиться вне source-каталога:

```bash
python3 scripts/build_plugin.py path/to/my-soft dist/my-soft-1.0.0.softhub.zip
```

Builder:

- валидирует manifest тем же Python-валидатором;
- исключает `.git`, `.venv`, `__pycache__`, `.DS_Store`;
- запрещает symlink и известные secret material files;
- сортирует entries и ставит фиксированное ZIP timestamp;
- вычисляет SHA-256 каждого файла;
- генерирует `hub.checksums.json`;
- атомарно заменяет output.

Builder не импортирует entrypoint, не создаёт plugin `.venv`, не проверяет совместимость wheels/целевой платформы и не выполняет полный installer pass над уже созданным ZIP. В частности, проверка assets payload, archive size/ratio и полного checksum-набора завершается при реальной установке. Поэтому build success не является приёмкой: автор ОБЯЗАН установить точный финальный asset в чистый Hub и выполнить smoke из installed path.

`hub.checksums.json` ОБЯЗАН содержать ровно одну lowercase SHA-256 сумму каждого файла архива, кроме самого `hub.checksums.json`. Ручное редактирование checksums ЗАПРЕЩЕНО.

После сборки автор ОБЯЗАН установить и протестировать именно получившийся byte-for-byte asset. Проверка checksum не доказывает авторство; внешний SHA-256 release asset ОБЯЗАН публиковаться по доверенному каналу.

### 16.2. GitHub и Patch Radar

Для автоматического обнаружения:

1. Repository ОБЯЗАН быть public.
2. Имя repository ОБЯЗАНО оканчиваться точно на `.patch` без учёта регистра.
3. Release tag ОБЯЗАН соответствовать manifest version: `v1.2.3` для `1.2.3`.
4. Latest Release ОБЯЗАН содержать ровно один installable asset с суффиксом `.softhub` или `.softhub.zip` без учёта регистра.
5. Рекомендуемое имя asset: `<plugin-id>-<version>.softhub.zip`.
6. Обычный source archive GitHub не является пакетом Hub.
7. Asset не заменяется после публикации; исправление выпускается новой SemVer.
8. Release notes ОБЯЗАНЫ содержать SHA-256, список изменений, новые permissions/resources и migration/reconciliation notes.

Patch Radar просматривает не более первых 100 public repositories владельца и читает latest release metadata. Он не подтверждает личность автора и не подписывает пакет.

## 17. Что проверяет Hub, а что остаётся обязанностью автора

| Контроль | Автоматически в текущем коде 0.6.4 | Обязанность автора/приёмки |
|---|---|---|
| Manifest shape и неизвестные поля | Да; `/2` требует `contract_version` и strict-поля | Не использовать admission legacy-манифеста как послабление. |
| Presentation paths/payload/byte limits | Да; `/2` требует `presentation`, legacy может его не иметь | Геометрия, статичность, metadata и качество проверяются acceptance review. |
| Resources vocabulary и связь с permissions | Да; `/2` требует точное двустороннее соответствие resources ↔ action secrets | Объявлять только реально читаемые значения и тестировать каждый missing resource. |
| Referral graph и effective code | Да с 0.6.4: encrypted payload, atomic cycle validation, exact grants и configured preflight | Предметно проверить, какой из own/incoming codes использует action; не логировать и не дублировать граф. |
| AdsPower browser/local service declaration | Да для AdsPower secret permissions | Реальный endpoint allowlist, workflow, cleanup и отсутствие утечки. |
| Secret выдача по action permissions | Да | Запрос минимальных прав и отсутствие самостоятельного чтения файлов. |
| Наличие конкретных account/global values до spawn | Да в релизе 0.6.4 | Повторный fail-closed check в entrypoint; acceptance test каждого missing resource. |
| Network list | Проверяется форма | Фактический allowlist не sandboxed; код обязан соблюдать список. |
| Risk/financial risk/chains | Проверяется декларативная согласованность | Честная классификация фактического кода и chain checks. |
| Options | Installer/builder валидируют strict `/2` schema, включая root, primitive fields, bounds/defaults, string `maxLength`, полный enum labels, уникальный order и лимит 7 primary; runner до Vault проверяет unknown/required/type/enum/bounds/step/length | Предметный формат, security policy, безопасные defaults и backward compatibility повторно проверяются entrypoint/tests. |
| Account lifecycle/progress | Проверяются protocol, membership, диапазон, монотонность, terminal правила, AVG по аккаунтам и сохранение последнего milestone при ошибке | Смысл status/stage, честные веса и покрытие каждой ветки. |
| Result semantics | Проверяются JSON/bounds, redaction и принадлежность `account_id` | Стабильный `kind/data`, один финальный предметный итог и согласованность с lifecycle. |
| Log redaction/export bounds | Да, defense-in-depth | Никогда не эмитить secret; redactor не DLP. |
| Leases и force-stop ambiguity | Да по объявленному risk | Идемпотентность, durable external truth и reconciliation. |
| Python environment | Version-local `.venv` и marker | Pinning, supply-chain audit, platform wheels. |
| OS/network isolation | Нет | Устанавливать только доверенный код; не заявлять наличие sandbox. |

## 18. Обязательная программа испытаний

### 18.1. Package и manifest

- [ ] Builder завершается успешно без ручной правки ZIP.
- [ ] Архив устанавливается в чистый data directory.
- [ ] Manifest явно содержит `contract_version: SH-SOFTWARE-0.6/2`; отсутствие marker или `/1` не используется для нового релиза.
- [ ] `id` постоянен, version увеличена, compatibility.hub не ниже `>=0.6.3`.
- [ ] В ZIP нет wrapper directory.
- [ ] В ZIP присутствуют manifest, generated checksums, requirements, icon, image и entrypoint.
- [ ] Icon и image непустые и статические; icon квадратный, image корректно кадрируется, рекомендуемые `512×512` и `1600×900` соблюдены либо отступление визуально проверено.
- [ ] Secret scan source, archive и dependencies не находит credential material.
- [ ] Повреждение любого payload-файла приводит к отказу checksum.
- [ ] Пакет проходит size/path/Unicode/case-collision проверки.

### 18.2. Presentation и UX

- [ ] Название, короткое и полное описание отображаются без fallback.
- [ ] Icon и image загружаются после локальной и GitHub-установки.
- [ ] Assets читаемы в light/dark theme и при crop/responsive layout.
- [ ] Каждое действие и option понятны без technical log; у каждого option есть `title`, `description` и полный `x-ui` как минимум с `group/order`.
- [ ] `x-ui.order` уникальны, enum имеют полный `enum_labels`, а одна group не смешивает primary/advanced.
- [ ] На основном уровне не более 7 параметров (целевой диапазон содержательной формы — 5–7); редкие настройки находятся в понятной advanced-группе.
- [ ] Safe defaults позволяют batch launch без бессмысленного обязательного числа.
- [ ] Options нового run не наследуют значения предыдущего; batch получает только объявленные безопасные defaults/зарезервированное host-значение.
- [ ] Никакой пользовательский flow не требует терминала или `input()`.

### 18.3. Resources и least privilege

- [ ] У каждого action есть exact `permissions.secrets` и `resources`.
- [ ] Для каждого secret permission есть ровно соответствующий account/settings resource и наоборот.
- [ ] Top-level secret union точен.
- [ ] Read/self-check action не получает write secrets без необходимости.
- [ ] Locked Vault блокирует secret-bearing action до spawn.
- [ ] Отсутствующий private key/proxy/email/Twitter/AdsPower profile определяется до spawn.
- [ ] Отсутствующий Capsolver/AdsPower API key определяется до spawn.
- [ ] Отсутствующий `referral_code`/`referrer` определяется до spawn с указанием аккаунта и ресурса.
- [ ] Любой referral-aware action имеет `compatibility.hub >=0.6.4`, exact grant и ровно соответствующий resource.
- [ ] Закрытый Vault возвращает `423` для одиночного и batch start до создания/replay run IDs.
- [ ] Если ресурс отсутствует у одного из нескольких аккаунтов, UI называет тип проблемы до запуска всей пачки.
- [ ] Секрет не принимается через options, env, файл или URL.

### 18.4. Lifecycle и results

- [ ] Протестированы 1, 2 и максимальное поддерживаемое число аккаунтов.
- [ ] Каждый начатый аккаунт получает `running` и ровно один terminal status; отменённые до начала аккаунты корректно проецируются Hub из `queued` в `cancelled`.
- [ ] Протестированы `succeeded`, `partial`, `failed`, `blocked`, `skipped`, `cancelled`, `needs_attention`.
- [ ] Progress конечный, `0..1`, монотонный; первый `running` имеет `0 < progress <= 0.10`, а успешный итог — ровно `1.0`.
- [ ] Долгий action сохраняет в БД/UI минимум три различных фактических промежуточных значения между стартом и terminal; вариант только `0 → 100` проваливает приёмку.
- [ ] Для повторяемого этапа тест с разными объёмами доказывает формулу `base + span * completed_weight / total_weight` с denominator, зафиксированным до работы.
- [ ] На двух аккаунтах общий progress равен среднему их milestones; global event без `account_id` не завышает account-run.
- [ ] Известная ошибка, blocked/cancelled и неоднозначный write сохраняют последний подтверждённый процент, а не становятся косметическими `100%`.
- [ ] Долгое ожидание показывает heartbeat/stage с неизменным progress; тест подтверждает отсутствие таймерного «доползания».
- [ ] Неотправленный terminal event виден как `unknown`, а не ложный success.
- [ ] Каждый начатый аккаунт с предметным итогом имеет ровно один финальный account-scoped result; `kind/data` стабильны и status согласован с terminal account state.
- [ ] Summary содержит только агрегаты и совпадает с account states.

### 18.5. Логи и секреты

- [ ] В штатном и ошибочном пути нет `print`, raw traceback и verbose HTTP/browser logging.
- [ ] Canary secrets не появляются в events, results, summary, stderr, paths и scratch.
- [ ] Скачанный `.log` не содержит canary private key, proxy, email, passwords, API keys, referral/referrer codes, Authorization, Cookie и AdsPower profile ID.
- [ ] Manifest/options отсутствуют в скачанном журнале.
- [ ] Один скачанный журнал содержит события всех аккаунтов run в общем порядке и позволяет различить их по `account_id`/`account_label`.
- [ ] Большой журнал корректно завершается footer `truncated/omitted_events`.
- [ ] После run scratch не содержит secret-bearing файлов.

### 18.6. Stop, retry и reconciliation

- [ ] Cancel до spawn не читает secrets и не создаёт external effect.
- [ ] Cooperative cancel до write даёт `cancelled`.
- [ ] Cancel/timeout после возможного write даёт `needs_attention`.
- [ ] Force stop не объявляет write безопасно отменённым.
- [ ] Retry чтения bounded; write не повторяется без внешней проверки.
- [ ] Duplicate API response/reconnect не создаёт повторную операцию.
- [ ] Reconcile action только читает и различает `confirmed/not_found/unknown`.
- [ ] Известный `failed` run не имеет lease, после просмотра становится `reviewed`, исчезает из текущего alert, допускает rerun и сохраняет error/events/results/account states.
- [ ] `needs_attention` нельзя закрыть review-механикой: только внешняя проверка и точное `RECONCILED` дают `reconciled` и снимают lease.
- [ ] Reconcile работает без logs/options/scratch/summary предыдущего run либо честно возвращает `unknown`.
- [ ] После Hub restart неоднозначный run остаётся требующим сверки.

### 18.7. Реферальная сеть

- [ ] Настроены и отдельно проверены собственный код, внешний входящий код и связь ребёнок → Hub-аккаунт-реферер.
- [ ] Изменение собственного кода родителя меняет effective `referrer_code` ребёнка без копии кода в payload ребёнка.
- [ ] Missing own/incoming code блокирует только action, которому нужен соответствующий resource, до spawn.
- [ ] Self-link, неизвестный ID и цикл любой длины отклоняются атомарно; после ошибки ни одна связь/код не изменились.
- [ ] Re-import сохраняет коды/связи; удаление родителя снимает прямые связи детей; plaintext export не содержит граф.
- [ ] API, bootstrap, DOM, events, results, summary и скачанный журнал не содержат значения кодов.
- [ ] Плагин получает только exact `account.secret("referral_code")` и/или `account.secret("referrer_code")`, но не parent ID и не код неразрешённого вида.
- [ ] Разовый public option, если он есть, ограничен `4..2048`, не объявляет resource `referrer`, не сохраняется и не выводится.

### 18.8. AdsPower

- [ ] Протестированы: сервис выключен, API key неверен, profile ID отсутствует, два выбранных аккаунта имеют одинаковый profile ID, профиль уже открыт.
- [ ] Configured preflight Hub отличён от bounded live preflight Local API; все эти ошибки дают понятный `blocked` до browser/write side effect.
- [ ] Протестированы: start timeout, attach failure, navigation timeout, cancel во время ожидания, stop failure.
- [ ] Профиль, открытый до run, не останавливается плагином.
- [ ] Профиль, открытый run, корректно закрывается в штатном пути.
- [ ] После cancel/ошибки не остаются owned tabs, driver, threads и child processes.
- [ ] Один AdsPower profile не используется одновременно двумя workers.
- [ ] API key, profile ID, connection endpoint и capabilities не попадают в журнал.

### 18.9. Dependencies и платформы

- [ ] Prepare проходит в чистом managed Python 3.12 environment.
- [ ] Все зависимости pinned и прошли vulnerability/license review.
- [ ] Нет runtime download/self-update/post-install surprise.
- [ ] Протестирована каждая ОС из `compatibility.os`.
- [ ] Native wheels соответствуют OS и архитектуре.
- [ ] Плагин не зависит от system Python, shell PATH и библиотек core Hub.

### 18.10. Release

- [ ] Локально протестирован именно финальный `.softhub.zip`.
- [ ] SHA-256 asset зафиксирован после тестов.
- [ ] GitHub repository оканчивается на `.patch`.
- [ ] Latest Release содержит ровно один `.softhub[.zip]` asset.
- [ ] Tag/version/asset filename согласованы.
- [ ] Release notes перечисляют permissions/resources/risk changes.
- [ ] Опубликованный asset не перезаписывается.

## 19. Безусловные причины отклонения пакета

Пакет отклоняется без условного допуска, если выполнено хотя бы одно условие:

1. Нет `presentation`, raster icon или image либо используется legacy fallback.
2. Нет action-level `permissions.secrets` или `resources` у хотя бы одного действия.
3. Название, описание, action или risk не соответствуют фактическому поведению.
4. Секрет передаётся через options, env, файл, URL, log, result, summary, exception, path или scratch.
5. Архив содержит реальные accounts, cookies, HAR, browser profile, локальную БД, `.venv` или private-key payload.
6. Write замаскирован под `read`.
7. Browser action не объявляет AdsPower resources/permissions либо не имеет bounded cleanup.
8. Пользователь узнаёт об отсутствующем ресурсе только после запуска из technical log.
9. Entrypoint использует `input()`, raw stdout, shell, detached process или импортирует внутренние модули Hub.
10. Не обеспечен ровно один terminal account state на каждую обработанную ветку.
11. Возможный внешний write после cancel/error отмечается `failed` или `cancelled` вместо `needs_attention`.
12. Нет стратегии идемпотентности и внешней сверки для write.
13. `safe_stop: true` заявлен без cancellation/cleanup tests.
14. Есть незакреплённые или недоверенные зависимости, runtime download либо критичная известная уязвимость.
15. Нет теста чистой установки, missing-resource preflight, stop, log privacy и заявленных платформ.
16. Изменён payload без увеличения SemVer или перезаписан опубликованный release asset.
17. Пакет требует ручной установки Python/редактирования config/терминала для штатного сценария.
18. Автор заявляет OS/network sandbox, цифровую подпись издателя или безопасность, которых Hub фактически не обеспечивает.
19. Новый пакет не объявляет `contract_version: SH-SOFTWARE-0.6/2` либо пытается пройти как legacy.
20. Options не имеют закрытого strict root, primitive schema, bounds/`maxLength` или обязательного `x-ui`, либо форма содержит секрет/security-critical policy.
21. Не определена стабильная result schema или progress тестируется только переходом `0 → 100`.
22. Referral-aware action имеет Hub compatibility ниже `>=0.6.4`, несовпадающие grant/resource, принимает mapped code через option либо раскрывает код/структуру графа.

## 20. Definition of Done

Софт считается готовым к вшиванию в Soft Hub 0.6.4 только когда одновременно:

- выполнены все MUST/MUST NOT этого документа;
- пройден весь checklist раздела 18;
- финальный архив собран штатным builder и повторно установлен из файла release candidate;
- UI показывает presentation и preflight без fallback/technical log;
- безопасность логов проверена canary-тестами;
- write/stop/reconciliation semantics проверены аварийными тестами;
- владелец Hub одобрил permissions, risk, dependencies и release SHA-256.

Любое исключение из SHOULD оформляется отдельной записью с причиной, риском, компенсирующим контролем, тестом и сроком устранения. Исключений из MUST/MUST NOT нет: для них требуется новая версия этого контракта и согласованное изменение schema/runtime/tests.

## 21. Готовое задание для следующей сессии разработки

Если новый софт разрабатывает другой инженер или AI-ассистент, владелец Hub передаёт ему этот текст без сокращений:

```text
Адаптируй софт <SOURCE_PATH_OR_REPOSITORY> в новый пакет для Soft Hub.

Обязательный контракт: SH-SOFTWARE-0.6/2 из docs/SOFTWARE_SPEC_RU.md.
До изменения кода полностью прочитай этот документ, schemas/plugin.schema.json,
soft_hub/sdk.py и scripts/build_plugin.py. Совместимость legacy не является
разрешением опускать presentation, action permissions или resources.

Не запускай реальные аккаунты и не отправляй внешние write-операции без отдельного
явного разрешения. Не копируй секреты из исходного софта в пакет, fixtures, логи,
скриншоты или результаты.

Сначала выдай таблицу действий: action id, фактический внешний эффект, risk,
account_mode, exact secrets, account resources, global settings, network hosts,
chains, browser/local services, stop semantics и способ reconciliation.
Если фактическое поведение нельзя доказать из кода, останови соответствующее
действие fail-closed и укажи blocker; не имитируй успех.

Затем реализуй полный immutable package с hub.plugin.json, requirements.txt,
assets/icon, assets/image и plugin entrypoint. Manifest обязан явно содержать
contract_version SH-SOFTWARE-0.6/2. Все пользовательские параметры опиши закрытой
primitive options schema с безопасными defaults/bounds/maxLength и дружелюбным
x-ui; параметры действуют один run. Секреты получай только через HubAccount/HubSettings.
Для каждого выбранного аккаунта выдай понятный lifecycle, ровно один terminal status,
стабильный предметный result и честный weighted progress с промежуточными milestones.
Не используй input(), raw print/logging, shell, runtime install/update или detached process.

До сдачи собери пакет только scripts/build_plugin.py, установи финальный архив
в чистый data directory и пройди весь checklist раздела 18, включая missing-resource,
canary-secret, cancel/force-stop, ambiguous-write/reconciliation, concurrency,
failed-review без lease, чистую установку и заявленные OS. Проведи dependency audit.

В ответе приложи:
1. путь к финальному .softhub.zip и его SHA-256;
2. итоговую таблицу permissions/resources/risk;
3. список выполненных тестов и их результат;
4. все честные ограничения и непроверенные внешние эффекты;
5. подтверждение, что MUST/MUST NOT выполнены без исключений.

Пакет не готов, пока любой пункт SH-SOFTWARE-0.6/2 не выполнен.
```

Перед началом новой major/minor-версии Hub владелец ОБЯЗАН сначала проверить, не появился ли более новый контракт. Номер `SH-SOFTWARE-0.6/2` нельзя автоматически переносить на несовместимый runtime.
