# Практический контракт плагина Soft Hub 0.6.4

Этот документ — инструкция для автора нового софта под Soft Hub 0.6.4 и контракт `SH-SOFTWARE-0.6/2`. Он описывает фактически реализованный контракт, а не желаемую будущую платформу. Источники истины в репозитории:

- `soft_hub/plugins.py` — проверка манифеста, ZIP-пакета, установка, подготовка окружения и откат;
- `soft_hub/sdk.py` — объекты `HubContext`, `HubAccount` и методы событий;
- `soft_hub/runtime/bootstrap.py` — импорт entrypoint, JSONL-протокол и terminal events;
- `soft_hub/runner.py` — выдача секретов, запуск процесса, redaction, статусы, results и остановка;
- `schemas/plugin.schema.json` — строгая авторская схема манифеста;
- `examples/hello-soft/` — минимальный рабочий пример;
- `scripts/build_plugin.py` — штатная сборка `.softhub.zip`.

Важно: JSON Schema сейчас не исполняется как отдельный движок при установке. Установщик и штатный builder вызывают строгий `validate_manifest()` из `plugins.py`, а schema нужна редактору и авторским проверкам. Новый пакет обязан объявить `"contract_version": "SH-SOFTWARE-0.6/2"`; отсутствие поля означает только legacy admission уже выпущенного пакета. При расхождении `/2`, schema, validator, SDK или tests выпуск останавливается — legacy-совместимость не имеет приоритета над новым контрактом.

## 1. Модель плагина

Плагин — это полный ZIP-пакет одной неизменяемой версии Python-софта. Это не diff и не набор файлов, которые накладываются поверх предыдущей версии.

Hub делает следующее:

1. Проверяет структуру архива, манифест и SHA-256 каждого файла.
2. Распаковывает пакет в отдельный каталог `<plugin-id>/<version>`.
3. При необходимости создаёт внутри этой версии `.venv` и выполняет `pip install -r ...`.
4. Для каждого запуска создаёт отдельный subprocess и передаёт ему контекст одной JSON-строкой через stdin.
5. Принимает события плагина как JSONL через stdout; обычный `print()` во время работы перенаправляется в stderr.
6. Сохраняет журнал и структурированные results в центральную SQLite БД.

Плагин не должен импортировать БД Hub, читать таблицы Hub или самостоятельно расшифровывать Vault. Его публичная граница — только `soft_hub.sdk`.

## 2. Быстрый старт

Начните с копии `examples/hello-soft`, измените `id`, имя, версию, действия и реализацию:

```text
my-soft/
├── hub.plugin.json
├── requirements.txt
├── assets/
│   ├── icon.png
│   └── cover.webp
└── plugin/
    ├── __init__.py
    └── main.py
```

Соберите пакет из корня репозитория. Выходной файл держите вне исходного каталога плагина:

```bash
python3 scripts/build_plugin.py my-soft dist/my-soft-1.0.0.softhub.zip
```

Штатный builder:

- валидирует манифест тем же Python-валидатором, что и Hub;
- исключает `.git`, `.venv`, `__pycache__` и `.DS_Store` на любой глубине;
- запрещает symlink;
- сортирует файлы и ставит фиксированное время записи ZIP;
- вычисляет `hub.checksums.json`;
- атомарно заменяет выходной файл.

Builder валидирует manifest и source denylist, но не импортирует entrypoint, не готовит `.venv`/dependencies и не заменяет полный installer pass над готовым ZIP. Archive/payload limits, presentation magic и целостность финального набора окончательно проверяются при установке. Поэтому обязательно установите byte-for-byte финальный asset в чистый Hub и сделайте smoke из installed path; успешная команда сборки сама по себе не является приёмкой.

В Hub откройте раздел патчей, перетащите архив, затем:

1. Если модуль показывает `needs_setup`, нажмите «Подготовить» и дождитесь установки зависимостей.
2. Сначала запустите безопасное read/self-check действие.
3. Проверьте журнал, progress и карточки результатов.
4. Только после этого включайте testnet/mainnet действия.

Рекомендуемое имя пакета: `<id>-<version>.softhub.zip`. API принимает имя, заканчивающееся на `.zip` или `.softhub`; двойной суффикс удобен человеку и остаётся обычным ZIP.

### 2.1. Публикация патча в GitHub

Hub 0.6.4 не забирает source archive ветки и не собирает плагин на машине пользователя. Публикуйте ровно тот `.softhub.zip`, который собрал штатный builder:

1. Поднимите SemVer в `hub.plugin.json` и соберите новый архив.
2. Прогоните тесты и локальную установку именно этого файла.
3. Создайте GitHub Release с tag, соответствующим версии, например `v1.3.0`.
4. Прикрепите архив как **Release asset**, а не кладите его только в Git-дерево.
5. Опубликуйте SHA-256 asset в release notes и не заменяйте файл под уже опубликованным tag.

Для автовыбора release должен содержать ровно один asset с case-insensitive суффиксом `.softhub.zip` или `.softhub`. Если таких assets несколько, Hub попросит прямую ссылку. Если таких assets нет, обычная GitHub-установка может выбрать ровно один `.zip` fallback. Поддерживаются:

```text
https://github.com/owner/repository
https://github.com/owner/repository/releases/latest
https://github.com/owner/repository/releases/tag/v1.3.0
https://github.com/owner/repository/releases/download/v1.3.0/example-1.3.0.softhub.zip
```

В 0.6.4 repository и release должны быть public. Не передавайте GitHub token в URL, опциях плагина или архиве: private repositories и token-based GitHub access не поддерживаются. Публикация на GitHub не заменяет подпись издателя: Hub проверяет целостность пакета, но не личность GitHub-автора.

#### Автообнаружение через Patch Radar

Чтобы пакет появился в Patch Radar владельца:

1. Назовите public repository так, чтобы его имя точно заканчивалось на `.patch` без учёта регистра. `wallet.PATCH` подходит; `wallet.patch.zip` и `wallet.patch-old` не подходят.
2. Создайте GitHub latest release.
3. Прикрепите к нему ровно один asset, имя которого заканчивается на `.softhub` или `.softhub.zip` без учёта регистра. Обычный `.zip` fallback Patch Radar не принимает.

Radar сканирует только первую страницу из 100 public repositories владельца и не использует GitHub token. Он читает только metadata; ready asset должен заранее иметь целый `size` от 1 byte до 256 MB и `browser_download_url` не длиннее 2048 символов. Download начинается только после явной команды установки, независимо ограничивается downloader лимитом 256 MB и затем проходит обычную проверку пакета.

## 3. Содержимое пакета

В корне архива обязательно должны лежать:

- `hub.plugin.json`;
- `hub.checksums.json`.

`hub.checksums.json` генерирует builder. Не редактируйте его вручную. В нём должна быть ровно одна SHA-256 сумма на каждый файл архива, кроме самого `hub.checksums.json`; `hub.plugin.json` тоже входит в список.

Остальные файлы могут находиться в каталогах. Практическая структура:

```text
hub.plugin.json
hub.checksums.json
requirements.txt
plugin/
  __init__.py
  main.py
  client.py
  config.py
  abi/
    token.json
```

Не кладите в пакет:

- private keys, пароли, прокси с credentials, cookies, API keys;
- legacy `database.enc`, `.env`, входные `.txt`, реальные логи или CSV;
- готовую `.venv`;
- Git-репозиторий, кэши и временные файлы;
- изменяемое production-состояние, которое должно переживать обновление версии;
- symlink и платформенные абсолютные пути.

Архив ограничен 256 MB в сжатом виде, 512 MB после распаковки и 4000 ZIP entries, включая directory entries; manifest и checksums ограничены 2 MB каждый. Для файла больше 1 MB запрещено подозрительное отношение распакованного размера к сжатому больше 100:1. Установщик также отклоняет:

- абсолютные пути, `..`, backslash и NUL в имени;
- symlink, POSIX special files, готовую `.venv`, Unicode не в NFC и неканонические пути вроде `./plugin/main.py`;
- повторяющиеся пути и регистронезависимые конфликты;
- Windows device names (`CON`, `NUL`, `COM1` и подобные);
- сегменты пути с `:`, завершающей точкой или пробелом.

## 4. `hub.plugin.json`

### 4.1. Полный шаблон

```json
{
  "schema_version": 1,
  "contract_version": "SH-SOFTWARE-0.6/2",
  "id": "io.sprintray.example",
  "name": "Example Soft",
  "version": "1.0.0",
  "description": "Коротко: что делает софт и какой результат сохраняет.",
  "author": "sprintray",
  "presentation": {
    "display_name": "Example Soft",
    "description": "Полное понятное описание сценариев, ограничений и результата софта.",
    "assets": {
      "icon": "assets/icon.png",
      "image": "assets/cover.webp"
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
    "state_model": "stateless",
    "requirements": "requirements.txt",
    "safe_stop": true,
    "heartbeat_seconds": 15
  },
  "permissions": {
    "secrets": ["evm_private_key", "proxy"],
    "network": ["api.example.com", "rpc.example.com"],
    "chains": [421614],
    "financial_risk": "testnet",
    "browser": false,
    "local_services": []
  },
  "actions": [
    {
      "id": "inspect",
      "name": "Проверить аккаунты",
      "description": "Читает публичное состояние без транзакций.",
      "risk": "read",
      "account_mode": "one_or_more",
      "permissions": {"secrets": ["proxy"]},
      "resources": {"account": ["proxy"], "settings": []},
      "options": {
        "type": "object",
        "properties": {
          "timeout": {
            "type": "integer",
            "title": "Таймаут, секунд",
            "description": "Общий предел ожидания ответа на один аккаунт; безопасное значение — 30 секунд.",
            "minimum": 5,
            "maximum": 120,
            "default": 30,
            "x-ui": {
              "group": "Сеть",
              "order": 10,
              "unit": "сек",
              "advanced": false
            }
          }
        },
        "required": [],
        "additionalProperties": false
      }
    },
    {
      "id": "farm",
      "name": "Запустить testnet-цикл",
      "description": "Выполняет объявленные testnet-транзакции.",
      "risk": "testnet_write",
      "account_mode": "one_or_more",
      "permissions": {"secrets": ["evm_private_key", "proxy"]},
      "resources": {"account": ["private_key", "proxy"], "settings": []},
      "options": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": false
      }
    }
  ],
  "ui": {
    "accent": "#5A7467",
    "monogram": "EX"
  }
}
```

### 4.2. Верхний уровень

| Поле | Правило |
|---|---|
| `schema_version` | Обязательно, сейчас только число `1`. |
| `contract_version` | Для нового пакета обязательно и строго равно `SH-SOFTWARE-0.6/2`; отсутствие включает только admission старого legacy-пакета. |
| `id` | Обязательно; до 96 символов; regex `^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$`. ID постоянен для всех версий. |
| `name` | Обязательно; после trim от 1 до 80 символов. |
| `version` | Обязательно; `MAJOR.MINOR.PATCH` с необязательным prerelease, например `1.2.0-rc.1`. Build metadata `+...` не принимается. |
| `description` | Обязательно; строка до 500 символов. |
| `author` | Обязательно для `/2`; непустая подпись до 120 символов, но не криптографическое доказательство издателя. |
| `presentation` | Обязательно для `/2`; полный display name, описание и локальные `icon`/`image`. Отсутствие допускается runtime только для legacy-пакетов. |
| `compatibility` | Обязательно для `/2`, включая `hub`, `python`, `os`. |
| `runtime` | Обязательно. |
| `permissions` | Обязательно. |
| `actions` | Обязательно; минимум одно действие. |
| `ui` | Необязательная legacy-метаинформация (`accent`, `monogram`); не заменяет `presentation`. |

Нельзя повторно установить ту же пару `id + version`. Любое изменение кода, requirements или манифеста требует новой версии.

#### Presentation — обязательный контракт нового софта `/2`

Новый пакет обязан содержать объект ровно такой формы:

```json
{
  "presentation": {
    "display_name": "Человекочитаемое имя",
    "description": "Полное описание до 4000 символов",
    "assets": {
      "icon": "assets/icon.png",
      "image": "assets/cover.webp"
    }
  }
}
```

`icon` и `image` — непустые безопасные относительные пути внутри каталога `assets/`; `null`, SVG, absolute path, `..`, backslash и скрытые path segments запрещены. Для нового `/2` пакета icon — статический квадратный PNG/WebP до 2 MB, image — статический PNG/JPEG/WebP до 16 MB; metadata (включая EXIF/XMP/GPS/локальные пути) удаляется. Рекомендуемые размеры — `512×512` и `1600×900` (`16:9`). Установщик требует наличие файлов в checksums, сверяет magic bytes с расширением и byte limits, но ради legacy admission всё ещё принимает GIF/AVIF/ICO и не доказывает frame count, геометрию, pixel count или отсутствие metadata. Эти свойства проверяет acceptance review; успешная установка не делает legacy-формат допустимым для `/2`.

UI должен получать картинку только через host-owned authenticated endpoint по `module_id` и роли `icon|image`, а не принимать filesystem path от renderer. Сервер обязан брать путь только из активного проверенного manifest, повторно проверять containment внутри `active_path`, ставить точный image MIME, `X-Content-Type-Options: nosniff` и не разрешать directory listing.

### 4.3. Compatibility

- `compatibility.hub` сейчас поддерживает только форму `>=x.y.z`; для `/2` минимум — `>=0.6.3`. Legacy default `>=0.1.0` не разрешён новому пакету. Установка отклоняется, если текущий Hub ниже указанной версии.
- `compatibility.os` должен быть непустым списком значений из `darwin`, `win32`, `linux`.
- `compatibility.python` предусмотрен schema и полезен как документация.

Ограничения MVP: установщик проверяет допустимость значений `os`, но не сверяет список с текущей ОС. Поле `python` тоже не исполняется, а поля архитектуры нет. Основной поставляемый desktop artifact 0.6.4 — macOS arm64 с managed CPython 3.12.13 внутри `Soft Hub.app`; системный Python пользователя не участвует. В репозитории есть Windows x64 build target, но это не доказательство готовности конкретного плагина/native wheels; packaged Linux target отсутствует. В dev-режиме используется Python, которым запущен Hub. Указывайте только реально протестированные OS и делайте fail-closed runtime check до side effect.

### 4.4. Runtime

| Поле | Значение и реальное поведение |
|---|---|
| `type` | Только `python`. |
| `entrypoint` | `package.module:function`; обе части — Python identifiers с точками только в module path. |
| `protocol` | Только `soft-hub-jsonl/1`. Bootstrap формирует frames сам. |
| `state_model` | `stateless`, `resumable` или `externally_reconciled`. Сейчас это декларация; Hub не предоставляет checkpoint API. |
| `requirements` | Необязательный безопасный относительный путь к файлу внутри пакета. |
| `safe_stop` | Если строго `true`, UI/API разрешают остановку и посылают SIGTERM/terminate. Это обещание автора, а не автоматическая гарантия. |
| `heartbeat_seconds` | Допускается schema в диапазоне 5..300, но runner 0.6.4 не использует поле как watchdog, не убивает зависший процесс и не реализует resume. Heartbeat показывает активность, а не выполненную работу. |

Не объявляйте `safe_stop: true`, пока код не проверяет отмену, не прекращает создание новой работы и не оставляет внешнее состояние однозначным или восстанавливаемым.

### 4.5. Permissions

`permissions.secrets` может содержать только:

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

Повторы запрещены. Top-level `permissions.secrets` — полный union прав всего плагина для установки и обзора. В новом манифесте каждое действие дополнительно объявляет точный набор в `actions[].permissions.secrets`. Если action-level поле есть хотя бы у одного действия, оно обязательно у всех; каждый набор должен быть подмножеством top-level, а их union — точно совпадать с ним. Это не даёт спрятать право и не оставляет устаревший overgrant. Старые манифесты без action-level блока остаются совместимыми и получают top-level набор целиком.

Runner выдаёт выбранным аккаунтам только права выбранного action. В разблокированном runner bundle базовые `id`, `label`, `evm_address` присутствуют всегда; заблокированный Vault не раскрывает даже эти метаданные через account/run/results API. `twitter` и `adspower_profile` — опциональные account-поля и не попадают в bundle, если не настроены. `capsolver_api_key` и `adspower_api_key` — глобальные Vault secrets. Новый SDK выдаёт их через `context.settings` под логическими именами `capsolver` и `adspower_api`. Оба API key после trim должны содержать минимум 4 символа; храните и настраивайте их только в Vault. Для обратной совместимости legacy action с точным grant `capsolver_api_key` также продолжает получать то же значение в каждом account bundle; AdsPower API key в account bundle никогда не дублируется.

Реферальное расширение появилось в Hub 0.6.4. `referral_code` разрешает получить собственный код выбранного аккаунта через `account.secret("referral_code")`; `referrer_code` разрешает получить его эффективный входящий код через `account.secret("referrer_code")`. Любое упоминание `referral_code` или `referrer_code` в top-level/action grants либо соответствующих resources требует `compatibility.hub: ">=0.6.4"`. Обычный пакет `/2`, не использующий реферальное расширение, по-прежнему может объявлять базовый минимум `>=0.6.3`.

`permissions.network` — список непустых строк. По соглашению указывайте только host names без схемы и пути. `permissions.chains` — положительные integer chain IDs. `permissions.financial_risk` — `none`, `testnet` или `mainnet`.

`browser` и `local_services` предусмотрены строгой schema для описания capabilities. Любое право `adspower_profile` или `adspower_api_key` дополнительно требует строго `permissions.browser: true` и canonical service `"adspower"` в `permissions.local_services`. Hub не хардкодит AdsPower endpoint, transport или auth flow: плагин отвечает за совместимость с актуальным Local API.

Граница доверия MVP: `network`, `chains`, `browser`, `local_services` и `financial_risk` не создают OS/network sandbox. `chains` используются Hub для account leases chain-write действий, но runner не проверяет фактический RPC chain ID. Для `external_write` Hub берёт отдельный внутренний service-lease на выбранный аккаунт без фиктивного chain ID в манифесте. `permissions.secrets` ограничивает данные, переданные в контексте; это единственное реально исполняемое capability-ограничение из этого блока. Сам процесс плагина работает с правами текущего OS-пользователя.

Сохраняйте собственные fail-closed проверки chain ID, contract addresses и допустимых сетей внутри софта.

### 4.6. Actions и подтверждение риска

`actions` — непустой список. Для каждого действия задайте:

- уникальный `id` по regex `^[a-z][a-z0-9_-]*$`;
- непустой `name`;
- `description` (обязателен по строгой schema и показывается в UI);
- `risk`: `read`, `external_write`, `testnet_write` или `mainnet_write`;
- `account_mode`: `none` либо `one_or_more`;
- точный `permissions.secrets`;
- точный `resources` с массивами `account` и `settings` (они могут быть пустыми);
- обязательный для `/2` закрытый объект `options`, даже если `properties` пуст.

Для нового контракта `/2` `resources` означает «без этого значения действие заведомо не может стартовать», а permission означает «это значение разрешено передать». Доступные requirements:

| `resources.account` | Требуемый grant |
|---|---|
| `private_key` | `evm_private_key` |
| `proxy` | `proxy` |
| `email` | `email` |
| `email_password` | `email_password` |
| `twitter` | `twitter` |
| `adspower_profile` | `adspower_profile` |
| `referral_code` | `referral_code` |
| `referrer` | `referrer_code` |

| `resources.settings` | Требуемый grant |
|---|---|
| `capsolver` | `capsolver_api_key` |
| `adspower_api` | `adspower_api_key` |

Для `/2` соответствие двустороннее и точное: каждый requirement обязан иметь соответствующий grant выбранного action, и каждый secret grant из таблиц обязан иметь соответствующий account/settings requirement. В частности, `resources.account: ["referral_code"]` требует ровно grant `referral_code`, а `resources.account: ["referrer"]` — ровно grant `referrer_code`; нельзя запросить один реферальный secret под ресурсом другого. Account requirements допустимы только при `account_mode: one_or_more`; `account_mode: none` может использовать лишь global settings/secrets и не получает account secrets. До создания subprocess runner проверяет configured-флаги каждой настройки и каждого объявленного account resource, включая наличие собственного или эффективного входящего реферального кода. Ошибка называет ресурс и label аккаунта, поэтому не переносите эту проверку в поздний HTTP client. Отсутствие `resources` принимается только как legacy fallback «нет объявленных обязательных значений».

Подтверждения:

- `read` запускается без risk acknowledgement;
- `external_write` обозначает non-financial мутацию внешнего сервиса (регистрация, заявка, изменение remote-профиля): финансовый gate не нужен, но force stop считается неоднозначным write;
- `testnet_write` показывает в UI ровно один Hub-owned checkbox, а прямой API-вызов требует точное acknowledgement `TESTNET`;
- `mainnet_write` обязан иметь непустую `confirmation_phrase`, которую пользователь вводит без изменений.

`confirmation_phrase` разрешена только для `mainnet_write`. Для mainnet используйте конкретную фразу с названием проекта и операции, например `RISEX CLOSE ALL`, а не общее `CONFIRM`. Testnet всегда проходит единый host gate: renderer показывает один checkbox, backend проверяет `acknowledgement == "TESTNET"`, а затем, если action schema объявляет boolean `acknowledge_testnet_transactions`, принудительно записывает этому полю `true` в запускаемом context. `external_write` не использует financial acknowledgement, но должен быть явно помечен: не выдавайте мутирующий HTTP POST за `read`.

`acknowledge_testnet_transactions` — зарезервированное служебное option-поле совместимости с адаптерами. Renderer не строит для объявленного boolean второй checkbox, а значение, присланное клиентом, не может заменить Hub acknowledgement: после успешной проверки backend перезаписывает объявленное поле на `true`. Новый плагин не должен создавать собственное дублирующее подтверждение; если entrypoint использует этот флаг как дополнительный fail-closed guard, считайте его только результатом host gate.

Hub проверяет, что `financial_risk` равен максимальному финансовому риску actions, у каждого chain write есть минимум одна chain и у любого write стоит `account_mode: one_or_more`. `external_write` совместим с `financial_risk: none` и пустым `chains`, потому что меняет remote-состояние без блокчейн-транзакции. Автор всё равно обязан честно соблюдать инварианты:

- только чтение → `risk: read`;
- non-financial POST/PUT/PATCH/DELETE или другая remote-мутация → `external_write`;
- любая отправка testnet-транзакции → `testnet_write`;
- любая mainnet-подпись, ордер или транзакция → `mainnet_write`;
- `financial_risk` должен отражать максимальный риск всего плагина;
- перечислите все chain IDs, на которых write-действие может занять аккаунт.

`account_mode: one_or_more` означает минимум один, но не ограничивает максимум. Если алгоритму нужно ровно 1, ровно 2 или чётное количество аккаунтов, проверьте это в entrypoint до первого внешнего side effect.

Renderer автоматически отмечает только единственный профиль. Если profiles несколько, оператор выбирает строки явно либо нажимает **Выбрать все**; при смене action любой multi-profile выбор сбрасывается, чтобы batch одного сценария не переносился в другой action с иными внешними эффектами. Это только UX-поведение: backend по-прежнему гарантирует лишь минимум один account. Не стройте предметную логику на количестве профилей, выбранных UI по умолчанию.

Для запуска пачкой Hub предпочитает совместимый `read` action без секретов, затем другие совместимые действия. `external_write` и `testnet_write` разрешены и явно помечаются в preflight; `mainnet_write` в пачке запрещён. Required option без `default` (кроме boolean или непустого enum) делает action неподходящим для one-click batch: оператор должен запустить его отдельно и заполнить поле. Backend делает полный preflight пачки, атомарно создаёт runs/leases и использует persistent idempotency key, поэтому ошибка одного элемента не оставляет частично принятую пачку.

Vault обязан быть разблокирован до любого одиночного старта и запуска пачки, даже если выбранное действие не запрашивает secrets или аккаунты. При lock endpoints запуска и batch отвечают HTTP `423 Locked`, subprocess/run records не создаются. Плагин не должен пытаться обойти эту host-owned границу через options, env, локальные файлы или собственное хранилище аккаунтов.

### 4.7. Options

Для `/2` `actions[].options` обязателен и имеет ровно четыре root-поля:

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

UI и backend поддерживают только плоские primitive-поля `boolean`, `string`, `integer`, `number` и string `enum`. Option keys — `snake_case`, максимум 40 полей. Arrays, nested objects, `null`, `$ref`, conditional schemas и неизвестные keywords запрещены. Каждое поле обязано иметь `type`, понятные `title`/`description` и объект `x-ui` как минимум с:

- `group` — смысловая группа до 80 символов;
- `order` — стабильный integer `0..1000`.

`order` обязан быть уникальным среди всех options action. Дополнительные presentation hints: `control: input|textarea` (`textarea` только для свободной строки), `placeholder` до 160 символов, `unit` до 24 символов только для `integer`/`number`, boolean `advanced` и обязательный полный `enum_labels` с непустой подписью до 120 символов для каждого string enum value. Одна `group` не может смешивать primary и advanced-поля. `enum` другого типа запрещён. Свободная строка обязана иметь `maxLength` до 16 000; `minLength` необязателен. `pattern` по `/2` запрещён, потому что Hub его не исполняет: синтаксис доменного значения проверяет entrypoint. Numeric поле обязано иметь конечные `minimum` и `maximum`; положительный `multipleOf` задаёт шаг.

Необязательное поле обязано иметь безопасный `default`. Required-поле может не иметь default только когда явный выбор действительно необходим. Boolean `acknowledge_testnet_transactions` остаётся зарезервированным служебным полем: UI не строит второй checkbox, а backend выставляет значение только после Hub-owned testnet gate, однако его manifest schema всё равно обязана содержать `type/title/description/x-ui` и безопасный default по общим правилам `/2`.

Installer и builder валидируют strict `/2` schema. До доступа к Vault runner отклоняет неизвестные option values, missing required, неверные JSON-типы, enum, numeric bounds/`multipleOf` и string `minLength/maxLength`. Это исправляет старое legacy-поведение, при котором часть schema служила только renderer. Entrypoint всё равно обязан повторно проверить предметный формат и security policy до side effect; секреты, trusted contract/RPC addresses и иные критические policy values через options запрещены.

Strict validator запрещает более 7 primary-параметров (`advanced` отсутствует или `false`); для содержательной формы целитесь в 5–7. Редкие настройки группируйте и помечайте `advanced: true`; если форма остаётся длинной, разделите сценарий на actions. `title`/`description` должны объяснять эффект, единицы, безопасный default и риск, а не требовать от пользователя угадать техническое число.

Options принадлежат только одному run и не являются persistent config. One-click batch берёт manifest defaults; required boolean может получить `false`, required string enum — первое значение. Иное required-поле без default блокирует batch и требует отдельного запуска через форму. Никогда не рассчитывайте на параметры предыдущего run.

Реферальная цепь аккаунтов — persistent конфигурация Vault и настраивается в разделе аккаунтов Hub, а не в action options. Единственное исключение — разовый ручной referral code, который по смыслу не привязан к выбранному аккаунту и не является частью графа. Для такого отдельного action допустимо обычное string-option ровно с `minLength: 4` и `maxLength: 2048`; не объявляйте этому action ресурс `referrer` или grant `referrer_code`. Значение существует только в контексте текущего run: не логируйте, не включайте в result/summary/exception и не сохраняйте в файл, БД либо внешнюю телеметрию.

```json
"manual_referral_code": {
  "type": "string",
  "title": "Разовый реферальный код",
  "description": "Используется только этим запуском и не изменяет реферальную сеть Hub.",
  "minLength": 4,
  "maxLength": 2048,
  "x-ui": {"group": "Реферальный код", "order": 10, "advanced": false}
}
```

## 5. Entrypoint и SDK

Entrypoint — обычная sync- или async-функция с одним аргументом `HubContext`:

```python
from soft_hub.sdk import HubContext


def run(context: HubContext) -> dict:
    ...
```

```python
async def run(context: HubContext) -> dict:
    ...
```

Bootstrap вызывает sync-функцию напрямую, а awaitable выполняет через `asyncio.run()`. Возвращайте JSON-serializable `dict`; он станет `run.summary`. Любое другое возвращаемое значение даёт пустой summary. Возврат не создаёт карточку в разделе результатов — для неё нужен `context.result()`.

### 5.1. Поля `HubContext`

| Поле | Содержание |
|---|---|
| `run_id` | UUID текущего запуска. |
| `plugin_id` | ID активного плагина. |
| `plugin_version` | Версия, зафиксированная для запуска. |
| `action_id` | Выбранное действие из манифеста. |
| `options` | JSON-объект из UI/API; считать недоверенным вводом. |
| `accounts` | Неизменяемый tuple объектов `HubAccount`. |
| `settings` | Read-only `HubSettings` только с глобальными secrets, разрешёнными выбранному action. |
| `plugin_root` | Путь установленной версии пакета. |
| `scratch_dir` | Уникальный каталог `runs/<run-id>/scratch`; одновременно это cwd процесса. |
| `cancelled` | Флаг запроса остановки. |

Пути передаются строками. Для ресурсов пакета используйте `Path(context.plugin_root)`. Для временных артефактов одного запуска — `Path(context.scratch_dir)`. Не рассчитывайте на cwd исходного проекта и не делайте глобальный `os.chdir()`.

Текущий Hub не чистит scratch автоматически и не импортирует оттуда результаты. Не сохраняйте там plaintext secrets; нужный пользователю результат отправляйте событием. Стабильного `plugin_data_dir`, общего для версий, в SDK 0.6.4 нет.

### 5.2. `HubAccount`

Безопасный доступ:

```python
account.id
account.label
account.evm_address
account.secret("proxy")
account.secret("evm_private_key")
own_referral_code = account.secret("referral_code")
incoming_referral_code = account.secret("referrer_code")
```

`HubAccount` также реализует read-only `Mapping`. `repr(account)` скрывает secrets, но преобразование в `dict`, итерация или прямой доступ к значениям их не скрывает. Не логируйте весь account.

`account.secret(kind)` выбрасывает `KeyError`, если право не объявлено или значение пусто. Не делайте fallback на файл, env или options: исправьте манифест либо capability модели Hub.

`referral_code` — собственный код именно этого аккаунта. `referrer_code` — вычисленный Hub входящий код: сохранённый внешний код либо актуальный собственный код выбранного аккаунта-реферера. Плагин не получает сам граф, parent ID или коды других профилей и не должен пытаться восстановить цепь самостоятельно.

Hub 0.6.4 требует unlock до любого запуска и batch, в том числе при пустом `actions[].permissions.secrets`; locked start отвечает `423`. Legacy-манифест без action-level прав использует top-level `permissions.secrets`. При выборе аккаунтов metadata и secret bundles также формируются только из разблокированного Vault.

### 5.2.1. `HubSettings`

Глобальные значения не принадлежат конкретному аккаунту:

```python
capsolver_key = context.settings.secret("capsolver")
adspower_key = context.settings.secret("adspower_api")
```

`HubSettings` — read-only `Mapping`, его `repr` скрывает значения. `secret(kind)` выбрасывает `KeyError`, если exact grant отсутствует или значение не настроено. Новый `/2`-код обязан использовать эти логические имена. Не логируйте и не преобразуйте `context.settings` в `dict`: mapping-доступ возвращает plaintext.

### 5.3. События

Используйте методы SDK:

```python
context.log("Запрос начат", account_id=account.id)
# Только для action без аккаунтов (account_mode: none).
context.progress(0.5, message="Проверена половина общего каталога")
context.account_state(
    account.id,
    status="running",
    stage="preflight",
    progress=0.1,
    message="Проверяем доступность API",
)
context.result(
    "Профиль проверен",
    kind="profile",
    status="succeeded",
    account_id=account.id,
    data={"points": 42},
)
```

`progress()` принимает только конечное число `0..1`; boolean, строка, NaN, infinity и выход за границы считаются protocol error, а не обрезаются до ближайшей границы. Откат уже сохранённого процента также отклоняется.

Для `account_mode: one_or_more` расчётным источником является только `account_state(..., progress=...)` либо `context.progress(..., account_id=account.id)`. Run-level event без `account_id` останется в журнале, но не изменит процент такого запуска: иначе один worker мог бы показать 90% при нулевом прогрессе остальных. Hub хранит монотонный процент каждого выбранного аккаунта и показывает их арифметическое среднее. Для `account_mode: none` используется монотонный run-level `context.progress(...)`.

`account_state()` — единственный авторитетный lifecycle конкретного аккаунта. Для каждого выбранного аккаунта отправьте `running` до предметного preflight и ровно один итоговый status: `succeeded`, `partial`, `failed`, `skipped`, `blocked`, `needs_attention` или `cancelled`. `stage` — стабильный machine-readable идентификатор по regex `^[a-z][a-z0-9_.-]{0,63}$`; `progress`, если передан, должен быть конечным числом 0..1. Обычные logs/results обновляют activity и last message, но Hub намеренно не угадывает по ним terminal status. Если run завершился без итогового `account_state`, таблица покажет `unknown`, даже если последний log звучал успешно.

Процент строится из заранее определённого плана реальной работы, а не из времени. Первый `running/preflight` ОБЯЗАН иметь `0 < progress <= 0.10`; неатомарный workflow дольше двух секунд либо с тремя и более предметными/внешними шагами ОБЯЗАН иметь минимум три различных meaningful milestone в диапазоне `0.10..0.95`. Публикуйте milestone только после проверяемого факта: ответ получен и валидирован, browser подключён, bounded batch обработан, receipt подтверждён, cleanup закончен. Для повторяемых шагов используйте `base + span * completed_weight / total_weight`: denominator фиксируется до старта, numerator растёт только после завершённой единицы; retries входят в заранее ограниченный budget. Во время долгого ожидания можно повторить heartbeat/stage/message с тем же процентом, но нельзя «доползать» по таймеру.

Только `succeeded` завершается `progress=1.0`. При `failed`, `skipped`, `blocked`, `needs_attention` или `cancelled` не подставляйте terminal progress: Hub всё равно проигнорирует его и сохранит последний подтверждённый milestone. `partial` получает `1.0` лишь когда весь объявленный план действительно обработан и итог каждой части известен.

В core 0.6.2 есть узкий migration bridge только для известных встроенных Checkpoint/Sekai/Umia `1.0.0`: он принимает ровно один account-scoped `account_summary` с разрешённым terminal status. Это совместимость с уже созданной локальной историей, а не API для новых плагинов. Другой ID/version/action, отсутствующий, повторный, конфликтующий или некорректный summary остаётся `unknown`.

`account_id` разрешён только из `context.accounts` текущего run. Чужой, пустой или сконструированный ID считается нарушением protocol и не записывается. Никогда не используйте это поле для label, address или произвольных данных.

`result()` создаёт отдельную постоянную запись. Его структура в БД:

- `kind` — категория результата;
- `status` — ваш предметный статус;
- `title` — аргумент `title`;
- `account_id` — связь с профилем, если результат per-account;
- `data` — payload.

Для каждого начатого аккаунта с предметным итогом создавайте ровно один финальный account-scoped result; не копируйте туда каждый log. Аккаунт, отменённый Hub ещё в `queued`, может не иметь result. Action без аккаунтов, создающий долговременный итог, формирует run-scoped result без `account_id`. `kind` — стабильный `snake_case` идентификатор публичной result schema: внутри совместимой major-версии не меняйте смысл существующих keys в `data`; breaking change требует нового kind либо major version. `status` должен совпадать с terminal account outcome по смыслу, но result не управляет lifecycle и не заменяет `account_state`. Для отмены без предметного итога result не нужен: `cancelled` не является result status.

Results переживают обновление и удаление модуля, поэтому payload должен оставаться небольшим и читаемым будущим renderer: публичные IDs/counters/tx hash допустимы, raw response, DOM/HAR, traceback и bulk event history — нет. Runner проверяет serializability/bounds, redaction и принадлежность `account_id`, но не доказывает смысл `kind/status`, их согласованность с terminal state или backward compatibility — это проверяет автор и acceptance tests. `reviewed`/`reconciled` являются operator-owned run statuses и никогда не эмитятся плагином как result/account status.

Допустимые protocol event types:

| Event | Назначение |
|---|---|
| `started` | Bootstrap сообщает, что задача принята. |
| `log` | Обычный журнал. |
| `progress` | Прогресс запуска. |
| `metric` | Структурированная метрика; пока отображается как обычное событие. |
| `warning` | Предметное предупреждение. |
| `result` | Постоянная карточка результата. |
| `account_state` | Авторитетный status/stage/progress одного выбранного аккаунта. |
| `heartbeat` | Событие активности; watchdog по нему пока отсутствует. |
| `completed` | Terminal event bootstrap при нормальном возврате. |
| `failed` | Terminal event bootstrap при исключении. |
| `cancelled` | Terminal event bootstrap при `CancelledError`. |

Для `metric`, `warning` или `heartbeat` используйте общий метод:

```python
context.emit(
    "metric",
    message="Latency",
    data={"name": "request_latency_ms", "value": 184},
)
```

Уровни runner: `debug`, `info`, `success`, `warning`, `error`; неизвестный level будет заменён на `info`.

Не отправляйте `started`, `completed`, `failed` или `cancelled` самостоятельно. Terminal lifecycle принадлежит bootstrap. Все `data`, message и итоговый summary должны сериализоваться стандартным `json.dumps()`.

SDK emitter сериализует параллельные вызовы внутренним lock, поэтому worker threads могут вызывать `context.log/progress/result/account_state`. Порядок событий между workers при этом недетерминирован; lifecycle одного аккаунта обязан оставаться монотонным: `queued → running → terminal`, без возврата назад и без второго terminal state.

### 5.4. stdout, stderr и logging

После импорта entrypoint bootstrap перенаправляет `sys.stdout` в stderr, поэтому случайный `print()` во время функции не ломает JSONL, но попадёт в журнал как warning `stderr`. Используйте SDK для нормальной телеметрии.

Критическая деталь: модуль entrypoint импортируется до перенаправления stdout. Любой `print()` или stdout-handler на уровне импорта создаст некорректный protocol frame. Три malformed stdout-строки приводят к принудительному завершению процесса. Protocol/stderr line ограничена 64 KB, а общий вывод run — 50 000 строк; превышение тоже останавливает процесс. На import-time только объявляйте классы, функции и constants; не запускайте CLI, меню, сетевые запросы и логирование.

Entrypoint не должен возвращаться, пока background threads/tasks продолжают внешние операции или могут писать в stdout: перед terminal event bootstrap восстанавливает protocol stdout. Завершите и `join` всех workers/children, затем возвращайте summary.

### 5.5. Redaction — страховка, не API

Runner перед записью событий пытается скрыть:

- точные значения выданных секретов длиной от 4 символов;
- значения вложенных полей с именами вроде `password`, `private_key`, `api_key`, `token`, `authorization`, `cookie`, `proxy` и `email`;
- authorization/cookie headers и текстовые password/token/API-key assignments;
- email и строки, похожие на 64-hex private key, JWT, proxy endpoint/credentials или длинный credential.

Текст ограничивается 16 000 символами; вложенность data — 8 уровнями, dict — 200 элементами, list — 500 элементами. Скачиваемый технический `.log` не содержит manifest/options и повторно очищает сохранённые события, но это защита в глубину, а не разрешение логировать секреты. Base64, разбиение строки, новый формат токена или запись в файл/сеть могут обойти redactor. Никогда не помещайте secrets, raw signed transactions, cookies, authorization headers или пароли в message, `data`, summary и exception text.

Не оставляйте исключения стороннего SDK необработанными на внешней границе. Bootstrap 0.6.4 включает имя и текст необработанного exception в failed-event и печатает traceback в stderr, поэтому production-entrypoint должен перехватывать ожидаемые client/SDK errors, переводить их в заранее заданный safe code/message и завершать account lifecycle без `str(error)`, `repr(error)` или raw payload. Неизвестное исключение классифицируйте общим безопасным кодом и воспроизводите отдельно без production secrets; redactor здесь только последняя защита, а не безопасная exception boundary.

## 6. Рекомендуемый entrypoint

```python
from __future__ import annotations

from typing import Any

from soft_hub.sdk import CancelledError, HubContext


def _integer_option(options: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} должен быть integer")
    if not low <= value <= high:
        raise ValueError(f"{name} вне диапазона {low}..{high}")
    return value


def run(context: HubContext) -> dict[str, Any]:
    if context.action_id == "inspect":
        return inspect_accounts(context)
    if context.action_id == "farm":
        return farm(context)
    raise ValueError(f"Неизвестное действие: {context.action_id}")


def inspect_accounts(context: HubContext) -> dict[str, Any]:
    timeout = _integer_option(context.options, "timeout", 30, 5, 120)
    succeeded = 0
    failed = 0
    total = len(context.accounts)

    for index, account in enumerate(context.accounts, start=1):
        context.check_cancelled()
        context.account_state(
            account.id,
            status="running",
            stage="inspect",
            progress=0.05,
            message="Проверка начата",
        )
        try:
            # inspect_one — ваша предметная функция. Client должен получить только
            # необходимые значения, а не весь account.
            context.account_state(
                account.id,
                status="running",
                stage="request_ready",
                progress=0.20,
                message="Параметры безопасного запроса проверены",
            )
            public_data = inspect_one(
                address=account.evm_address,
                proxy=account.secret("proxy"),
                timeout=timeout,
            )
            context.account_state(
                account.id,
                status="running",
                stage="response_received",
                progress=0.70,
                message="Ответ проверки получен",
            )
            validate_inspection_result(public_data)
            context.account_state(
                account.id,
                status="running",
                stage="response_validated",
                progress=0.90,
                message="Ответ проверки получен и проверен",
            )
        except CancelledError:
            context.account_state(
                account.id,
                status="cancelled",
                stage="cancelled",
                message="Проверка остановлена",
            )
            raise
        except Exception:
            failed += 1
            context.result(
                f"{account.label}: проверка не выполнена",
                kind="account_inspection",
                status="failed",
                account_id=account.id,
                data={"error_code": "inspection_failed"},
            )
            context.account_state(
                account.id,
                status="failed",
                stage="failed",
                message="Проверка не выполнена",
            )
        else:
            succeeded += 1
            context.result(
                f"{account.label}: профиль проверен",
                kind="account_inspection",
                account_id=account.id,
                data=public_data,
            )
            context.account_state(
                account.id,
                status="succeeded",
                stage="completed",
                progress=1,
                message="Профиль проверен",
            )
        # Общий процент Hub уже пересчитает как AVG(progress) всех account_state.

    return {"total": total, "succeeded": succeeded, "failed": failed}
```

В примере ошибка одного профиля превращается в предметный result, а весь run может завершиться успешно с `failed > 0`. Для финансового write-действия выберите более консервативную семантику: после сохранения безопасного статуса и результатов поднимите исключение, если внешнее состояние нельзя однозначно считать завершённым. Hub пометит write-run как `needs_attention`.

## 7. Остановка и восстановление

На macOS/Linux при остановке Hub посылает process group `SIGTERM`. Bootstrap ставит `context.cancelled`; плагин увидит отмену только если регулярно вызывает:

```python
context.check_cancelled()
```

Метод поднимает `CancelledError`, который должен дойти до bootstrap. Не перехватывайте его общим `except BaseException`. Если нужна очистка, используйте `try/finally`, после чего снова вызовите `check_cancelled()` или пробросьте исключение.

Через 10 секунд после мягкого сигнала живой процесс принудительно завершается. На Windows текущая реализация использует `process.terminate()`, а не доставку Python-сигнала в bootstrap, поэтому cooperative cancellation и cleanup там не гарантированы даже при `safe_stop: true`. До исправления Windows signal path финансовый action обязан рассчитывать на journal/reconciliation после принудительного прерывания. Поэтому на всех платформах:

- проверяйте отмену между аккаунтами, retry и внешними операциями;
- ставьте таймауты на HTTP/RPC;
- переставайте создавать новые транзакции сразу после отмены;
- не называйте остановку safe, если сигнал может прийти между подписью и broadcast без durable journal;
- дочерние процессы и browser automation тоже должны иметь контролируемое завершение.

Принудительно остановленный write-run не считается безопасно отменённым: он получает `needs_attention`, а его chain/account либо external-service lease остаётся бессрочно. Оператор обязан проверить внешнее состояние, открыть журнал и ввести точное `RECONCILED`. Только после этого Hub меняет run на `reconciled` и снимает leases. Плагин не может эмитить `reconciled`. Штатное завершение `cancelled`/exit 130 снимает leases автоматически.

Не смешивайте это с известной ошибкой. `failed` означает определённый локальный исход до неоднозначного write и не должен иметь safety lease; такой run можно повторить. После чтения журнала оператор закрывает текущее уведомление действием review: Hub меняет только run status на `reviewed`, сохраняет error/events/results/account states и не выдаёт это за успешный outcome. Review отклоняется, если у failed run остался lease. `needs_attention` review-механикой не закрывается и блокирует повтор конфликтующего account/scope до внешней сверки.

`state_model` — честное описание стратегии:

- `stateless`: плагин не зависит от локального состояния Hub; это ничего не обещает об идемпотентности внешнего write;
- `resumable`: существует durable checkpoint/journal и определён recovery path;
- `externally_reconciled`: перед продолжением истина восстанавливается из chain/API.

В 0.6.4 Hub не реализует plugin state storage, resume callback или recovery action автоматически. Более того, новый reconcile run не получает через `HubContext` logs, results, summary, options, scratch или checkpoint предыдущего run, и Hub не запускает reconcile action сам. Сверка обязана читать внешнюю истину по текущему account identity и устойчивому business key/public operation ID; если без прошлого transient context доказательство невозможно, верните `unknown`. Для настоящего `resumable` софта нужен стабильный, не привязанный к версии data directory либо внешнее хранилище. Не используйте scratch для межзапускового состояния и не прячьте SQLite внутрь immutable package без плана миграции/rollback. `stateless` не доказывает идемпотентность повторного write, а `resumable` не создаёт storage.

## 8. Секреты и профили

Центральный импорт Hub создаёт строгую связку 1:1:

```text
EVM private key ↔ HTTP proxy ↔ email ↔ optional email password ↔ optional Twitter ↔ optional AdsPower profile ID
```

Private key, proxy и email обязательны для каждого Hub-профиля и уникальны между профилями. Прокси принимается в виде `host:port:user:password` или HTTP-варианта и хранится канонически. Плагин получает только выбранные пользователем профили и только объявленные secret kinds.

Правила автора:

1. Запрашивайте минимальный набор secrets.
2. Передавайте отдельные значения в client/signer, а не весь `HubAccount`.
3. Не копируйте секреты в plugin-owned БД, CSV, логи, summary или results.
4. Не храните пароль Vault и не просите его в options.
5. Не сохраняйте raw context из stdin.
6. После использования удаляйте ссылки на крупные secret-bearing структуры, понимая, что Python не гарантирует обнуление immutable strings.

Email password, Twitter и AdsPower profile ID входят в зашифрованный account payload. Глобальные Capsolver и AdsPower API keys шифруются отдельно. Публичный account/bootstrap state сообщает только boolean-флаги настройки, но никогда не раскрывает profile ID или API key. AdsPower profile ID — opaque строка до 256 символов без control characters и пробелов по краям; endpoint/auth Local API не являются частью Hub-контракта.

Перед созданием parallel workers browser-action обязан прочитать profile ID всех выбранных accounts в память и отклонить точные дубли как `blocked` для конфликтующих аккаунтов, не логируя значение. Vault не гарантирует уникальность AdsPower IDs между Hub accounts, а account lease не замечает общий внешний профиль. Host preflight проверяет только configured-флаги profile/API key; живую доступность Local API, auth, однозначное существование профиля и returned endpoint entrypoint проверяет bounded read-only запросом до первого browser/write side effect. Выключенный AdsPower, неверный key и неизвестный profile — понятный `blocked`, а не причина искать terminal traceback.

Центральный plaintext export — операторский flow с повторным паролем и точной фразой `EXPORT PLAINTEXT SECRETS`; account export может включать `adspower_profile`, но никогда не включает глобальные Capsolver/AdsPower API keys. Плагин не должен дублировать этот export собственным CSV или выносить выданные secrets в results.

### 8.1. Реферальная сеть 0.6.4

Реферальная сеть — host-owned зашифрованный граф. У каждого аккаунта может быть собственный `referral_code`, а источник его входящего кода имеет ровно один вид:

- `none` — реферер не назначен;
- `account` — выбран другой аккаунт Hub; эффективным `referrer_code` становится его актуальный собственный код;
- `external` — сохранён отдельный внешний код, когда реферера нет среди аккаунтов Hub.

Связь `account` хранит ссылку на родительский аккаунт, а не копию его кода. Поэтому изменение собственного кода родителя автоматически меняет эффективный входящий код прямых детей. Self-reference, неизвестный parent, конфликт parent + external и любой цикл отклоняются; bulk-изменение графа атомарно — либо применяются все отношения, либо ни одно. Повторный импорт аккаунта сохраняет его реферальные поля. Удаление родителя очищает ссылки его прямых детей, а не оставляет dangling relation.

Собственный и внешний коды после trim обязаны иметь длину `4..2048` символов и не содержать control characters. Они лежат только внутри AES-GCM account payload. Account metadata и ответы API сообщают лишь configured-флаги, вид связи, parent ID/label и число детей; фактического кода там нет. Ни один код не должен попадать в account-list/bootstrap UI projection, list/read API response, log, result, summary, exception, export или plugin-owned storage. Renderer не должен хранить сохранённый код в DOM/JS: после записи поле снова пустое, UI показывает только состояние «настроено».

Граф настраивает пользователь в разделе аккаунтов Hub. Host endpoint `POST /api/accounts/referrals` принимает только объект `relationships`; каждая запись содержит `account_id`, `referrer` и `own_code`. Коды изменяются явным патчем `{"mode":"keep"}`, `{"mode":"clear"}` либо `{"mode":"set","value":"..."}`. `referrer` имеет одну из строгих форм:

```json
{"kind":"none"}
{"kind":"account","account_id":"parent-account-id"}
{"kind":"external","code":{"mode":"set","value":"external-code"}}
```

Внешняя связь с `mode: "keep"` допустима только когда внешний код уже сохранён. API требует разблокированный Vault и возвращает только безопасную account metadata, никогда введённые значения кодов.

Плагин объявляет только то, что реально использует; ниже фрагмент манифеста:

```json
{
  "compatibility": {"hub": ">=0.6.4", "python": ">=3.12,<3.13", "os": ["darwin"]},
  "permissions": {"secrets": ["referral_code", "referrer_code"]},
  "actions": [{
    "permissions": {"secrets": ["referral_code", "referrer_code"]},
    "resources": {"account": ["referral_code", "referrer"], "settings": []}
  }]
}
```

Перед созданием subprocess Hub проверяет каждый выбранный аккаунт. Ресурс `referral_code` готов только при наличии собственного кода; ресурс `referrer` — только при наличии внешнего кода либо parent с настроенным собственным кодом. В entrypoint читайте уже разрешённые значения через `account.secret("referral_code")` и `account.secret("referrer_code")`; не принимайте mapped chain через options и не вычисляйте код по labels/IDs.

### 8.2. EVM safety

Подписывайте транзакции только локально в памяти. Private key/seed и raw signed transaction нельзя отправлять в RPC/API/browser, URL, файл, log, result или summary. Перед каждой подписью fail-closed проверяйте фактический `chainId`, sender, `to`, selector/calldata, `value`, token/spender/amount/decimals, nonce, gas/fee/max-cost bounds и соответствие выбранному action/risk. По возможности выполните simulation/`eth_call` до broadcast.

Unlimited approval, permit, arbitrary message и blind signing запрещены, если это не отдельный явно названный `mainnet_write` action с точным описанием и acceptance review. После broadcast сохраняйте только public chain ID, tx hash и значения с единицами. Отмена/ошибка между подписью, broadcast и подтверждённым receipt — `needs_attention`; локально рассчитанный tx hash не является доказательством включения.

## 9. Зависимости

Если `runtime.requirements` отсутствует или файл содержит только пустые строки/комментарии, модуль считается `ready` и запускается Python-интерпретатором Hub.

Если файл содержит зависимости:

1. После установки health будет `needs_setup`, пока Hub не завершил prepare и не создал собственный marker с SHA-256 текущего requirements.
2. «Подготовить» создаёт `.venv` через managed Python приложения; пользователю не нужно устанавливать Python или открывать терминал.
3. Hub сначала обновляет `pip` внутри `.venv` из закреплённого offline wheel приложения с `--no-index`, затем выполняет `python -m pip install --disable-pip-version-check -r <file>` с timeout 900 секунд.
4. После успешного pip Hub атомарно записывает `.venv/.soft-hub-ready.json` с SHA-256 requirements и ID текущего managed runtime; только Python + валидный marker дают health `ready`.

Pip stdout/stderr захватываются subprocess-вызовом, но Hub 0.6.4 не сохраняет их в журнал и при ошибке возвращает общее сообщение. Воспроизводите неудачный prepare в проверенном локальном окружении без secrets; не рассчитывайте на подробный install log в UI.

Каждая версия имеет собственную `.venv`; новая версия не наследует окружение старой. Failed pip не создаёт marker и не делает окружение готовым. Rollback вернёт health предыдущей версии только при совпадении marker с её requirements и managed runtime. Перемещение `.app` либо обновление встроенного Python также инвалидирует старое окружение: нажмите «Подготовить» ещё раз.

Рекомендации:

- закрепляйте версии всех зависимостей и тестируйте lock/constraints;
- для критичного supply chain используйте `--require-hashes`/проверенные hashes, когда экосистема пакета это позволяет;
- поставляйте готовые wheels для `macOS arm64 + CPython 3.12`, если пакет имеет native extension; сборка sdist может потребовать Xcode Command Line Tools;
- не полагайтесь на версии пакетов из core: bootstrap убирает core dependency directory после загрузки SDK, и зависимости из plugin `.venv` имеют приоритет;
- не включайте `.venv` в архив;
- не включайте `.env`, HAR/cookies, private key, proxy/email/account списки, сертификатные ключи или локальные БД: штатный builder и installer отклонят такие имена и потребуют перенести значения в Vault;
- учитывайте офлайн-установку и платформенные wheels;
- проверяйте лицензии и post-install/build hooks.

`prepare` не устанавливает Node.js packages, Playwright browsers, системные библиотеки и локальные сервисы. Для такого софта сегодня нужен заранее подготовленный runtime или отдельное развитие lifecycle core; один `requirements.txt` это не решает.

## 10. Выпуск обновления и rollback

Правильный patch lifecycle:

1. Изменить исходники.
2. Обновить SemVer в `hub.plugin.json`.
3. Выполнить unit/integration тесты без реальных secrets.
4. Собрать новый полный `.softhub.zip` штатным builder.
5. Установить архив через Hub.
6. При `needs_setup` подготовить окружение.
7. Запустить self-check/read action.
8. Провести ограниченный canary на одном testnet-профиле.
9. Только затем расширить запуск.

Установка новой версии сразу делает её активной. Предыдущие версии и их каталоги сохраняются. Флаг enabled существующего модуля при обновлении не сбрасывается: если модуль был выключен, новая версия останется выключенной.

Rollback выбирает наиболее недавно установленную версию, отличную от активной, а не математически предыдущий SemVer. После отката повторный rollback может вернуть только что покинутую версию. Пока у плагина есть активный/queued run, rollback отклоняется. Автоматической миграции/отмены plugin-owned state нет.

Команда удаления стирает все версии модуля и его `.venv`, но сохраняет историю запусков и результаты. Она блокируется при активном или не сверенном `needs_attention` run. Удаления одной выбранной версии и hot reload нет. Не изменяйте файлы уже установленной версии вручную: запись SHA архива останется прежней, но runtime-код станет не тем, что прошёл установку.

## 11. Адаптация существующего CLI-софта

Не вызывайте legacy `main()` из entrypoint. CLI обычно делает `getpass()`, рисует меню, читает `input/*.txt`, меняет cwd и пишет локальные логи — всё это нарушает контракт Hub.

Разделите код на слои:

```text
plugin/main.py       dispatch по context.action_id
plugin/adapter.py    HubAccount/options → внутренние модели
plugin/service.py    чистая предметная логика
plugin/client.py     HTTP/RPC с timeout и proxy
```

Пошаговая миграция:

1. Вынесите каждый пункт меню в вызываемую функцию.
2. Замените чтение private keys/proxies/email из файлов на `HubAccount`.
3. Удалите собственное дублирующее хранилище общих секретов.
4. Перенесите безопасные настройки из `parameters.py` в action options; опасные адреса/chain IDs оставьте constants и валидируйте fail-closed.
5. Замените console/file logger на небольшой adapter, вызывающий `context.log/progress/result`.
6. Преобразуйте CSV/таблицы в один `context.result()` на профиль плюс summary.
7. Добавьте отмену и таймауты.
8. Разделите read и write actions с правильным risk.
9. Для write-действий добавьте idempotency key, preflight и внешнюю reconciliation.
10. Упакуйте только runtime-код и статические ресурсы.

Не передавайте `context` глубоко во все предметные классы. Лучше определить узкий callback/protocol для событий: так старый софт останется тестируемым и сможет работать вне Hub при необходимости.

## 12. Локальная проверка до упаковки

Минимальный набор:

```bash
python3 -m compileall -q my-soft
python3 scripts/build_plugin.py my-soft dist/my-soft-1.0.0.softhub.zip
```

В unit tests создайте `HubContext` с тестовым emitter и `threading.Event`, передайте фиктивные `HubAccount` без реальных ключей и проверьте:

- dispatch всех action IDs;
- validation options;
- нулевое и несколько accounts там, где функция тестируется напрямую;
- JSON-сериализацию каждого event data и summary;
- progress в диапазоне `0..1`, строго монотонный, с первым `running` в `(0, 0.10]` и `1.0` только при success;
- минимум три фактических промежуточных milestone для долгого workflow и отсутствие таймерной симуляции;
- weighted progress на разных объёмах работы с denominator, зафиксированным до выполнения;
- AVG на двух аккаунтах и сохранение последнего milestone при failed/blocked/cancelled/needs_attention;
- один финальный предметный result на начатый профиль, стабильный `kind/data` и согласованный status;
- один `running` и ровно один terminal `account_state` на профиль;
- отмену до первого side effect и во время retry;
- отсутствие secrets в событиях и exception messages;
- повторный запуск после частичного внешнего успеха.
- review известного failed run без lease и отдельный `RECONCILED` flow для `needs_attention` с lease;
- AdsPower duplicate profile IDs и live preflight, если action использует браузер;
- HTTP `423` без создания run для одиночного старта и batch при locked Vault;
- для referral-aware action: installer gate `>=0.6.4`, точное соответствие `referral_code ↔ referral_code` и `referrer ↔ referrer_code`;
- разрешение собственного, parent и external кодов, а также preflight-ошибку при отсутствии нужного кода;
- atomic rollback при неизвестном parent/self-reference/цикле и изменение effective child code после смены кода родителя;
- отсутствие фактических referral codes в account metadata, API responses, events, logs, results, summary, exception и export;
- для manual-only кода: границы `4..2048`, отсутствие grant/resource `referrer_code`/`referrer` и отсутствие сохранения либо телеметрии значения.

Builder не проверяет, что entrypoint реально импортируется, а requirements совместимы с чистой `.venv`. Обязательно делайте smoke через установленный пакет, а не только из source tree.

## 13. Ошибки, которые встречаются чаще всего

| Симптом | Причина и исправление |
|---|---|
| «Версия уже установлена» | Изменили код без bump `version`. Выпустите новую версию. |
| `needs_setup` | В requirements есть зависимости, но prepare-marker отсутствует или не совпадает с requirements. |
| «Файл runtime.requirements отсутствует» | Путь в манифесте не совпадает с путём в архиве. |
| «Некорректный protocol frame» | Import-time stdout или код вручную пишет в stdout. Уберите вывод и используйте SDK. |
| Процесс убит после трёх warnings protocol | Три stdout-строки не были JSONL frames. Особенно проверьте import-time logging. |
| Run завершён, но Results пуст | Вернули summary, но не вызвали `context.result()`. |
| В таблице аккаунта `unknown` | Плагин завершил run без итогового `context.account_state(...)`. Не выводите статус из последнего log. |
| Run failed после успешной логики | Нет terminal event из bootstrap, entrypoint упал при возврате или summary не JSON-serializable. |
| Известная ошибка остаётся в текущем alert | Откройте общий журнал и выполните review: run станет `reviewed`, история сохранится, повтор разрешён. Не вводите `RECONCILED` для обычного `failed`. |
| Повтор запуска блокируется после аварии write | Run имеет `needs_attention` и safety lease. Сначала проверьте внешний API/chain/browser state, затем введите точное `RECONCILED`; review здесь намеренно недоступен. |
| Testnet action не устанавливается/не запускается | Не задавайте ему `confirmation_phrase`; UI использует один Hub checkbox, backend — acknowledgement `TESTNET`. Служебный `acknowledge_testnet_transactions` не является вторым пользовательским gate. |
| Дробная option не вводится корректно | Используйте schema type `number`, а не `integer`; при фиксированном шаге задайте положительный конечный `multipleOf`. |
| Mainnet action не устанавливается | Не указана непустая `confirmation_phrase`. |
| Stop недоступен | `runtime.safe_stop` не равен `true`. Не включайте его до реализации cooperative cancel. |
| Progress не меняется от `context.progress(...)` | У action выбраны аккаунты. Передавайте `account_id` или, предпочтительно, публикуйте `account_state(..., progress=...)`; глобальный event не участвует в AVG account-run. |
| Progress отклонён protocol-валидатором | Значение не в `0..1`, не является конечным числом либо меньше уже сохранённого. Не clamp-ите ошибку и не отправляйте stages из разных workers для одного аккаунта без сериализации. |
| Progress прыгает только `0 → 100` | Adapter сообщает лишь старт и terminal. Добавьте фактические weighted milestones после preflight/ответа/шага/receipt/cleanup; таймерная симуляция запрещена. |
| Старт или batch отвечает `423 Locked` | Vault заблокирован. Разблокируйте его в Hub; плагин не должен получать account/run metadata или обходить lock через другой источник. |
| Referral action не устанавливается при `>=0.6.3` | Любой referral grant/resource требует `compatibility.hub: ">=0.6.4"`; базовый `>=0.6.3` остаётся допустим только для пакета без реферального расширения. |
| `referrer` не настроен, хотя выбран parent | У parent отсутствует собственный код. Настройте его в реферальной сети Hub; child получает `referrer_code` из актуального `referral_code` родителя. |
| Пакет установился на неподдерживаемой ОС | `compatibility.os` пока не сопоставляется с текущей платформой; добавьте runtime preflight. |
| Network permission не остановил запрос | Allow-list декларативен и не является firewall. |

## 14. Release checklist

### Манифест

- [ ] Уникальный постоянный `id`, новая SemVer-версия.
- [ ] Есть точный `contract_version: SH-SOFTWARE-0.6/2`; пакет не маскируется под legacy.
- [ ] `compatibility.hub` не ниже `>=0.6.3`; перечислены только реально протестированные OS.
- [ ] Если объявлен `referral_code`, `referrer_code`, `resources.account.referral_code` или `resources.account.referrer`, стоит `compatibility.hub: ">=0.6.4"`.
- [ ] Есть полный `presentation`; оба локальных image asset входят в checksums и проходят лимиты формата/размера.
- [ ] Entry point имеет вид `package.module:function`.
- [ ] Каждое действие имеет честные `risk` и `account_mode`.
- [ ] Mainnet action имеет конкретную confirmation phrase.
- [ ] Testnet action не имеет конфликтующей custom phrase или второго пользовательского confirmation checkbox.
- [ ] Дробные параметры имеют type `number` и корректный положительный `multipleOf`, если нужен фиксированный шаг.
- [ ] Каждый action имеет закрытый `options` root с `required` и `additionalProperties: false`; у каждого option есть primitive type, title, description и `x-ui.group/order`.
- [ ] `x-ui.order` уникальны, enum имеют полный `enum_labels`, одна group не смешивает primary/advanced.
- [ ] Свободные строки ограничены `maxLength`, enum только строковый; на основном уровне не более 7 параметров (целевой диапазон — 5–7), остальные помечены advanced.
- [ ] Safe defaults обеспечивают batch без параметров прошлого run; required без default используется только при реальной необходимости.
- [ ] Запрошен минимальный набор secrets.
- [ ] У каждого action есть точные `permissions.secrets` и `resources`; requirements совпадают с grants.
- [ ] Соответствие resources ↔ secret grants проверено в обе стороны без overgrant.
- [ ] Referral grants совпадают с resources точно: `referral_code ↔ referral_code`, `referrer_code ↔ referrer`; mapped chain не дублируется в options.
- [ ] AdsPower grants сопровождаются `browser: true` и `local_services: ["adspower"]`.
- [ ] Перечислены все реально используемые domains и chain IDs.
- [ ] `financial_risk` равен максимальному риску плагина.
- [ ] `safe_stop` включён только после теста отмены.

### Код

- [ ] Нет import-time side effects и stdout.
- [ ] Нет меню, `input()`, `getpass()` и зависимости от cwd.
- [ ] Options валидируются в entrypoint.
- [ ] У HTTP/RPC есть timeout, ограниченные retries и proxy по профилю.
- [ ] Chain ID и критические addresses проверяются fail-closed.
- [ ] Нет secrets в logs/events/results/summary/files.
- [ ] Referral codes не попадают в metadata/API response/log/result/summary/exception/export; используются только через exact `account.secret(...)`.
- [ ] Manual-only referral option ограничен `4..2048`, не имеет `referrer_code`/`referrer` grant/resource и нигде не сохраняется.
- [ ] Внешняя exception boundary выдаёт только allowlisted safe codes/messages, без raw exception/traceback.
- [ ] Для каждого начатого профиля с предметным итогом создаётся ровно один структурированный result со стабильным kind/schema.
- [ ] Для каждого профиля есть `running` и ровно один terminal `account_state` на всех ветках выхода.
- [ ] Progress следует weighted work plan, имеет минимум три meaningful промежуточных milestone и не симулируется таймером.
- [ ] Entrypoint не возвращается до завершения/join всех worker threads и tasks.
- [ ] Write-путь идемпотентен либо имеет durable reconciliation.
- [ ] Ошибка после возможного write не маскируется как общий success.
- [ ] EVM-путь локально подписывает и fail-closed проверяет chain/to/calldata/value/spender/amount/nonce/gas; raw signed transaction не покидает память.
- [ ] AdsPower action отклоняет duplicate profile IDs до workers и делает bounded live preflight до browser/write side effect.
- [ ] Capsolver/AdsPower API keys берутся только из `context.settings`, имеют минимум 4 символа и не передаются через options/account bundle.
- [ ] `failed → reviewed` сохраняет историю и допускает rerun без lease; `needs_attention → reconciled` требует внешнюю проверку и `RECONCILED`.

### Пакет

- [ ] Нет `.venv`, `.git`, caches, legacy DB, plaintext input, HAR/cookies и реальных логов; builder проходит credential denylist.
- [ ] Dependencies закреплены и проверены в чистом окружении.
- [ ] Архив собран `scripts/build_plugin.py` вне source directory.
- [ ] Архив устанавливается как новая версия.
- [ ] `needs_setup` → prepare проходит на целевых ОС.
- [ ] Self-check/read smoke проходит из установленной версии.
- [ ] Проверено, что locked одиночный старт и batch получают HTTP `423` и не создают run.
- [ ] Для referral-aware пакета проверены own/parent/external resolution, missing-code preflight, cycle rejection и отсутствие code leakage.
- [ ] Canary write выполнен сначала на одном testnet-профиле.
- [ ] Проверен реальный rollback и совместимость состояния.

Главное правило: `.softhub.zip` — привилегированный исполняемый код. Checksums подтверждают целостность содержимого архива, но не автора. Устанавливайте только пакет, исходники и зависимости которого вы готовы доверить всем выбранным секретам и данным текущего OS-пользователя.
