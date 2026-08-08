# Безопасность Soft Hub MVP

Этот документ описывает фактическую модель безопасности Soft Hub `0.6.4`. Это не заявление об аудите и не гарантия сохранности средств. Текущий desktop build — локальный preview для одного оператора и доверенных плагинов, а не публичный production-релиз; использование ценных mainnet-ключей до независимого аудита не рекомендуется.

## Коротко

- Hub слушает только `127.0.0.1` и защищает API случайным токеном текущего запуска.
- Account secrets, включая email password, Twitter, AdsPower profile ID, собственный/внешний referral code и связь с account-реферером, а также глобальные Capsolver/AdsPower API keys зашифрованы в SQLite с помощью AES-256-GCM; ключ получается из мастер-пароля через scrypt.
- Закрытый Vault скрывает account/run/account-state/result/event/log projections и configured-статусы глобальных ключей; renderer очищает защищённый client cache. Остаются только безопасные aggregate counts активных/требующих внимания runs.
- Plaintext export требует открытый Vault, повторную проверку мастер-пароля и точную фразу `EXPORT PLAINTEXT SECRETS`; Excel-safe XLSX не исполняет formula-like ячейки, но скачанный файл всё равно больше не защищён Vault.
- Плагин получает только типы секретов из exact grant выбранного action; обязательные `actions[].resources` проверяются до создания subprocess.
- Run options повторно валидируются core по закрытой schema; неизвестные поля, неверные типы, enum/range и пропущенные required отклоняются до доступа к Vault.
- Архив проверяется на path traversal, symlink, конфликтующие пути, размер и SHA-256 файлов.
- Packaged app запускает core через собственный управляемый Python runtime с `-I`; Python/Node.js пользователя не входят в runtime-требования.
- **Python-плагин является доверенным кодом.** Subprocess и `.venv` не являются security sandbox.
- Плагины имеют статус `local_unsigned`. Локальный desktop preview подписан ad-hoc и не notarized; ни plugin checksums, ни ad-hoc подпись не доказывают личность издателя.

## Активы и границы модели

Hub защищает следующие активы от случайной утечки и части локальных атак:

- EVM private keys;
- HTTP proxy credentials;
- email и опциональные email passwords;
- Twitter credentials;
- глобальный Capsolver API key;
- AdsPower profile ID и глобальный AdsPower API key;
- собственные referral codes, внешние referrer codes и связи между Hub-аккаунтами;
- мастер-пароль и производный ключ vault;
- история запусков, результаты и локальная конфигурация;
- целостность установленного содержимого пакета относительно его `hub.checksums.json`.

Модель предполагает:

- одного доверенного пользователя за локальной сессией ОС;
- некомпрометированную ОС и учетную запись пользователя;
- доверенный исходный код самого Hub;
- ручную проверку происхождения каждого плагина и его зависимостей;
- отсутствие публикации loopback-сервиса через reverse proxy, port forwarding или измененную сетевую обвязку.

MVP не защищает от администратора/root, malware под тем же OS-пользователем, чтения памяти процесса, кейлоггера, вредоносного проверенного пользователем плагина или компрометированного Python-пакета из `requirements.txt`.

## Рассматриваемые угрозы

Реализованные проверки уменьшают риск следующих сценариев:

- вредоносный ZIP пытается записать файл за каталог версии, подложить symlink или конфликтующий по регистру путь;
- zip bomb исчерпывает диск или память;
- посторонняя web-страница отправляет запросы к локальному API;
- случайный `print`, traceback или structured result раскрывает выданный плагину private key, JWT либо proxy;
- два write-запуска одновременно используют один аккаунт в одной chain;
- Hub перезапускается во время незавершенного write-действия;
- плагин отправляет поврежденные или произвольные protocol frames.

Остаточные риски подробно перечислены ниже. Особенно важно: декларация разрешений не ограничивает системные вызовы Python-процесса.

## Vault

### Что хранится зашифрованно

Для каждого аккаунта Hub сериализует `evm_private_key`, полный proxy, email, email password, Twitter, AdsPower profile ID, `referral_code`, `referrer_account_id` и `external_referrer_code` в один JSON payload и шифрует его `AESGCM` со случайным 12-byte nonce. Associated data привязывает ciphertext к ID аккаунта и версии формата. Подмена или повреждение ciphertext приводит к ошибке аутентификации. Referral code после trim имеет длину `4..2048` и не содержит control-символы.

Глобальные Capsolver и AdsPower API keys хранятся отдельно в `vault_secrets`, каждый шифруется тем же derived key с отдельным nonce и AAD, привязанным к имени секрета. Каждый API key должен содержать минимум `4` символа: это fail-closed граница для корректной exact-value redaction, а не оценка силы ключа. При открытом Vault Bootstrap/UI возвращают только `capsolver_configured` и `adspower_api_configured`; при закрытом — `null`, чтобы не раскрывать даже факт настройки. Значение выдаётся run только при соответствующем exact action grant.

256-bit ключ получается из мастер-пароля через scrypt со случайной 16-byte salt и текущими параметрами `N=32768`, `r=8`, `p=1`. Пароль не сохраняется. В БД сохраняются salt, параметры KDF и зашифрованный verifier, необходимый для проверки пароля.

При unlock Hub принимает только этот точный поддерживаемый KDF profile, 16-byte salt и мастер-пароль не более 4096 UTF-8 bytes. Подмена `kdf_json` на ресурсоёмкие или неизвестные параметры завершается fail-closed до запуска scrypt.

Минимальная длина мастер-пароля — 14 символов; дополнительно отклоняются слишком однообразные строки. Это базовая проверка, а не оценка реальной энтропии. Используйте уникальную длинную passphrase и храните ее отдельно от data directory.

### Что остается метаданными

SQLite содержит в открытом виде данные, нужные интерфейсу и дедупликации:

- label и публичный EVM address;
- endpoint proxy без логина и пароля (`host:port`);
- маскированный email, включая домен;
- логический флаг `twitter_configured`, но не Twitter-значение;
- логические флаги `email_password_configured` и `adspower_configured`, но не пароль и не AdsPower profile ID;
- tags, статусы и timestamps;
- несоленые SHA-256 fingerprints private key, полного proxy и email;
- манифесты, события, результаты и пути установленных плагинов.

Fingerprints не раскрывают private key напрямую, но позволяют подтверждать догадку о значении с низкой энтропией — особенно об email или proxy. Поэтому backup базы тоже считается чувствительным.

### Жизненный цикл ключа

После create/unlock производный ключ находится в памяти процесса Hub до ручной блокировки или завершения процесса. `lock()` перезаписывает основной `bytearray` и удаляет ссылку, но Python и криптографические библиотеки могут создавать временные копии; нет `mlock`, secure enclave или гарантированного memory zeroization.

Desktop-оболочка также отправляет lock при системных событиях lock-screen и suspend. После возврата UI перечитывает фактическое состояние Vault. Это best effort и не может отозвать секрет, уже переданный работающему plugin process.

Блокировка является также границей проекций. Bootstrap возвращает пустые `accounts`, `runs` и `results`, нулевые account/result counters и не раскрывает configured-статусы глобальных ключей. Отдельные routes account list, runs, run-account states, results, run detail, events и technical log отвечают `423 Locked`. То же относится к stop/force-stop/review/reconcile и операциям с account/global secrets. Любой POST одиночного или batch-start после того, как существующий Vault закрыт, также возвращает `423`: новые run IDs, leases и run projections до unlock не создаются/не выдаются. Account-free action без секретов не обходит эту host-границу после создания Vault.

Renderer при lock синхронно очищает собственные массивы accounts/runs/results/activity/events, закрывает панели/модальные окна и увеличивает epoch, поэтому in-flight response, начатый до lock, не может повторно заполнить DOM. Статистика `active_runs`, `needs_attention` и `attention_runs` остаётся видимой как безопасный operational signal без ID, labels и текста ошибки.

При запуске выбранные секреты расшифровываются и один раз передаются дочернему процессу JSON-строкой через `stdin`. После этого Hub очищает основные Python-контейнеры best effort. Это не signing broker: разрешенный плагин получает сам private key и может сохранить или отправить его.

В MVP нет смены мастер-пароля, recovery key или встроенного зашифрованного backup/restore Vault. Потерянный пароль восстановить нельзя. Для нового мастер-пароля нужен новый data directory и повторный импорт из отдельного доверенного источника.

Блок реферальных связей редактируется bulk-запросом в одной SQLite-транзакции. Backend сначала расшифровывает и проверяет весь граф, отклоняет self-reference, несуществующего parent и любой цикл, и только затем перешифровывает все изменённые rows. Неудача откатывает весь batch. List API возвращает только kind/parent label/configured flags/child count, но не referral codes. Повторный импорт уже известного private key сохраняет эти поля; удаление parent-аккаунта атомарно очищает связь у его прямых children.

### Plaintext account export

Export аккаунтов — явная declassification-операция, а не зашифрованный backup. Backend выполнит её, только если:

1. Vault уже разблокирован.
2. Повторно введённый мастер-пароль проходит verifier.
3. Acknowledgement в точности равен `EXPORT PLAINTEXT SECRETS`.

Ответ имеет `Cache-Control: no-store`; account export может содержать `adspower_profile`, поэтому считается plaintext secret export целиком. Глобальные Capsolver/AdsPower API keys, email passwords, referral codes и связи не экспортируются. Поэтому этот plaintext-файл одновременно опасен как declassified subset и недостаточен как backup: повторный импорт в новый Vault не восстановит реферальную сеть. Эти проверки не защищают уже созданный plaintext-файл: его нельзя коммитить, отправлять в чат или оставлять в незашифрованном Downloads.

На POSIX Hub пытается создать data directory с mode `0700`, а базу и файлы пакета — с ограниченными mode. На Windows используются ACL, унаследованные от профиля пользователя; отдельная настройка ACL кодом не выполняется.

В macOS desktop data directory по умолчанию находится в `~/Library/Application Support/Soft Hub`, отдельно от `/Applications/Soft Hub.app`. Замена или удаление только `.app` не стирает Vault и историю. Это удобная граница обновления, а не security boundary: процесс с доступом к каталогу пользователя или его backup всё равно получает ciphertext и открытые metadata.

## Loopback API, токен и CSP

HTTP-сервер создается на `127.0.0.1` со случайным свободным портом. Он не должен быть доступен из LAN. Проверка `Host` принимает только `127.0.0.1` и `localhost`; изменяющие запросы дополнительно проверяют `Origin`, если браузер его прислал. `OPTIONS` отключен.

На каждом старте создается `secrets.token_urlsafe(32)`. Токен передается UI в URL fragment, считывается JavaScript, сохраняется в `sessionStorage` и сразу удаляется из адресной строки через `history.replaceState`. Все `/api/*` запросы обязаны передать его в `X-Soft-Hub-Token`; сравнение выполняется через `compare_digest`. Перезапуск Hub аннулирует старый токен.

Fragment обычно не отправляется HTTP-серверу и не попадает в Referer. Тем не менее токен доступен JavaScript-контексту страницы и любому, кто контролирует браузерную сессию или может читать память/хранилище того же пользователя. Не публикуйте стартовую строку `SOFT_HUB_READY`, не копируйте полный URL и не оставляйте терминал доступным посторонним.

Ответы получают `Cache-Control: no-store` и security headers: строгий CSP с ресурсами только от `'self'`, запрет object/base/frame, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` и same-origin resource policy. CSP уменьшает browser-атаку, но не защищает от измененного локального frontend-кода или вредоносного плагина под тем же OS-пользователем.

Electron renderer использует `contextIsolation`, отключенный `nodeIntegration`, включенные `sandbox`, `webSecurity` и запрет навигации на другой origin. Только явный CTA Patch Radar может открыть через системный браузер заранее проверенный HTTPS URL вида `github.com/<owner>/<repo>`; само Electron-окно наружу не переходит. Это относится к UI renderer, а не к Python-плагинам.

TLS и пользовательская аутентификация отсутствуют, потому что сервер задуман только для loopback. Не меняйте bind address на `0.0.0.0` и не выставляйте API наружу.

## Пакет плагина

При установке Hub проверяет:

- максимальный размер архива 256 MB, распакованного содержимого 512 MB и максимум 4000 entries;
- отсутствие абсолютных путей, `..`, backslash, NUL, Windows device names, непереносимых имен и symlink;
- отсутствие точных и case-insensitive конфликтов путей;
- подозрительный compression ratio для файлов больше 1 MB;
- наличие в корне `hub.plugin.json` и `hub.checksums.json`;
- совпадение списка файлов с checksums и SHA-256 каждого payload-файла;
- запрет известных credential/session путей: `.env`, HAR/cookies, private key, proxy/email/account списков, certificate keys и локальных DB; тот же denylist применяется штатным builder до создания архива;
- базовый контракт manifest v1, минимальную версию Hub и обязательные risk/actions поля.

Распаковка идет во временный staging-каталог, затем версия переносится в `plugins/<id>/<version>`. Одна и та же версия повторно не устанавливается; ранее установленные версии остаются для rollback.

Это защита целостности и безопасной распаковки, **не проверка происхождения**. `hub.checksums.json` находится в том же неподписанном архиве, поэтому автор вредоносного пакета может пересчитать все суммы. Текущий `trust_status` — `local_unsigned`. Нет подписи издателя, certificate chain, transparency log или allowlist доверенных ключей.

### GitHub Release и Patch Radar

Обычная GitHub-установка и Patch Radar в 0.6.4 работают только с public GitHub-данными без token/Authorization. Private repositories не видны, а tokens/credentials в URL отклоняются. Это также означает анонимные GitHub API rate limits.

Patch Radar принимает только username или точный HTTPS owner URL, читает не более первых 100 public repositories с `api.github.com`, отбирает точный case-insensitive суффикс `.patch` и читает только `releases/latest`. Ready-статус возможен лишь при ровно одном release asset с case-insensitive суффиксом `.softhub` или `.softhub.zip` и безопасной GitHub download URL того же owner/repository. Ранняя metadata-проверка требует, чтобы `asset.size` был целым числом от 1 byte до 256 MB, а `browser_download_url` — строкой не длиннее 2048 символов.

Само сканирование выполняет только ограниченные metadata GET: оно не скачивает, не открывает, не проверяет, не устанавливает и не запускает asset. Скачивание начинается только по явной команде оператора; downloader независимо применяет собственный лимит 256 MB, после чего архив проходит обычный install pipeline. Наличие repository в Patch Radar не доказывает личность или доверенность автора.

## Граница доверия плагина

Плагин запускается отдельным subprocess без shell, с отдельной process group, очищенным набором environment variables, рабочим scratch-каталогом и, при наличии зависимостей, отдельным `.venv`. Эти меры уменьшают случайные конфликты, упрощают остановку и не дают автоматически наследовать большинство переменных окружения.

Они **не создают границу безопасности**. Процесс работает от того же OS-пользователя и без filesystem/network sandbox. Он может обращаться к любым файлам, сокетам, процессам и устройствам, которые разрешены этому пользователю и ОС. Отдельный `.venv` изолирует Python dependencies, но не полномочия.

Фактическое значение manifest permissions в MVP:

| Поле | Что реально делает Hub | Чего оно не делает |
|---|---|---|
| `permissions.secrets` + `actions[].permissions.secrets` | Top-level показывает union плагина, а runner передаёт только точный набор выбранного action; legacy manifest получает top-level fallback | Не запрещает плагину самостоятельно читать доступные ему файлы или сеть |
| `actions[].resources` | До run/spawn проверяет наличие обязательных account/global значений и соответствие grant; ошибка указывает ресурс и аккаунт | Не проверяет доступность внешнего сервиса и корректность credential у провайдера |
| `permissions.chains` | Создает account leases для write-действий | Не проверяет RPC URL, chain ID транзакции или содержимое подписи |
| `actions[].risk` | Отличает read, non-financial `external_write` и chain writes; требует `TESTNET`/mainnet-фразу и выбирает fail/attention semantics | Не определяет фактическое поведение кода |
| `runtime.safe_stop` | Разрешает UI отправить stop; после 10 секунд возможен force kill | Не гарантирует транзакционность или возможность безопасного восстановления |
| `compatibility.hub` | Проверяет минимальную поддерживаемую версию Hub | Не является подписью или sandbox policy |
| `permissions.network`, `browser`, `local_services`, `financial_risk` | Являются декларативными метаданными; AdsPower grants требуют `browser=true` и canonical local service `adspower` | Не образуют принудительный allowlist или запрет системных вызовов |
| `compatibility.python`, `compatibility.os` | Описывают ожидаемую среду; значения OS проходят синтаксическую проверку | Текущий MVP не гарантирует runtime enforcement заявленного диапазона/платформы |

Устанавливайте плагин только если вы готовы выполнить его код с правами своего пользователя и выдать ему все заявленные секреты. Проверяйте не только исходники архива, но и каждую зависимость `requirements.txt`: команда **Подготовить** запускает `pip install`, а install hooks и импортируемые пакеты являются частью trust boundary.

С Hub 0.6.4 referral-aware action использует exact grants `referral_code` и/или `referrer_code` и точно совпадающие account resources `referral_code`/или `referrer`. Второй grant не раскрывает структуру графа: runner разрешает effective referrer в сам код в момент запуска. Manifest с этими grants обязан иметь `compatibility.hub >=0.6.4`; наличие grant не создаёт sandbox и не заменяет ручную проверку плагина.

## Запуски, остановка и журналы

Hub принимает от bootstrap только JSONL frames протокола `soft-hub-jsonl/1` с известными event types. После трех malformed frames процесс завершается принудительно; line и общий объём строк ограничены. Любой непустой `account_id` дополнительно должен принадлежать выбранным аккаунтам именно этого run; чужой ID не записывается в events/results. Для chain-write действий lease не даёт параллельно использовать тот же account и chain; `external_write` получает отдельный per-account service-lease. Оба вида продлеваются независимо от частоты вывода.

Выбранный набор аккаунтов и их lifecycle сохраняются отдельно в `run_account_states`. Terminal status меняет только типизированный `account_state`, а не текст последнего log/result. Это защищает интерфейс от ложного «успешно» в произвольном сообщении; отсутствие итогового события отображается как `unknown`, а неоднозначное завершение write — как `needs_attention`. В projection попадают только публичные ID/label и уже отредактированный message, но labels и сама БД всё равно считаются чувствительными метаданными.

Для `testnet_write` renderer показывает один Hub-owned checkbox, а backend требует точное acknowledgement `TESTNET`. Только после этой проверки runner принудительно добавляет в context `options.acknowledge_testnet_transactions=true`, если action schema объявляет такое boolean-поле; присланное клиентом значение не может обойти gate. Дублирующий plugin checkbox не является независимым подтверждением и в UI не рендерится.

Non-financial мутация внешнего API объявляется как `external_write`, а не `read`. Для неё нет financial acknowledgement, однако runner выдаёт write-lease и после force stop/падения не утверждает, что операция безопасно отменена: status становится `needs_attention`. Это важно для HTTP POST, где сервер мог принять запрос до разрыва локального процесса.

После restart, forced stop или неоднозначного завершения write-run получает `needs_attention`, а leases удерживаются. После внешней проверки оператор должен явно ввести `RECONCILED`; тогда статус становится `reconciled`, и только затем leases снимаются. Hub не может сам доказать, была ли транзакция отправлена до сбоя. Exclusive lock data directory не позволяет второму Hub одновременно переписать состояние первого.

Известный terminal `failed` не равен неоднозначному write. У него нет safety lease: оператор может отметить уведомление просмотренным, после чего run получает `reviewed` и исчезает только из live-attention projection. Эта операция fail-closed не выполняется при неожиданном lease, не изменяет исходную ошибку/account states/results/events и не называется внешней сверкой.

Force-stop является отдельной разрушительной операцией и на API требует точное acknowledgement `FORCE STOP`. На POSIX Hub сигналит process group, на Windows использует best-effort `taskkill /T /F`. Это завершение процесса, а не откат внешнего действия: отделившийся daemon теоретически может пережить сигнал, а write-run всё равно требует сверки.

Исключение — queued run, который ещё не получил concurrency slot и не создал subprocess. Timed acquire loop замечает safe/force cancellation до расшифровки account bundle и spawn, завершает такой run как `cancelled` и освобождает lease. Это не переносится на принудительно убитый или иначе неоднозначно завершившийся уже созданный write-процесс: для него сохраняется консервативная ambiguity semantics.

Пакетный запуск принимает пачку только одним backend-запросом: mainnet actions запрещены, options и подтверждения проверяются для каждого элемента, write-leases резервируются в одной транзакции, а persistent idempotency key предотвращает дубли после неопределённого сетевого ответа. Пачка либо целиком поставлена в очередь, либо не создаёт ни одного run. Эта атомарность относится к admission в Hub; уже запущенные внешние операции общей транзакцией не становятся.

При штатном shutdown Hub сначала прекращает admission, сигналит активным plugin processes, ждёт grace period и принудительно завершает оставшиеся до lock Vault. На POSIX дополнительно очищается process group descendants. Полноценного Windows Job Object в MVP нет, поэтому гарантии для отделившихся Windows descendants слабее.

Redactor получает точные значения и account bundles, и `HubSettings`, затем заменяет выданные секреты, secret-поля вложенных объектов, authorization/cookie headers, email, password/token/API-key assignments и строки, похожие на EVM private key, JWT, proxy или длинный credential. Он также ограничивает глубину, количество элементов и длину сообщений. Технический журнал скачивается только через token-authenticated API как один bounded UTF-8 JSONL-файл конкретного run: он содержит общий поток всех аккаунтов с безопасными account labels, но не manifest/options; сохранённые события повторно проходят redaction непосредственно перед export. Ответ имеет `Cache-Control: no-store`.

Это всё ещё best effort, а не DLP: Redactor не обнаружит все кодировки, фрагменты, хэши, файлы, изображения или нестандартно разбитые секреты. Плагин обязан никогда не помещать secrets в `message`, `data`, exception text, имя файла или собственные логи. Скачанный `.log` остаётся чувствительным артефактом истории и не должен публиковаться без проверки.

Scratch-каталог сохраняется после run. Если плагин записал туда секрет, он останется на диске вне зашифрованного vault. Hub пока не очищает и не сканирует scratch автоматически.

## Практика работы с секретами

- Для dev/test используйте отдельные пустые кошельки, тестовые email и proxy credentials.
- Не коммитьте private keys, `.env`, входные или экспортированные TXT/CSV/TSV, готовые пакеты с секретами, data directory или backup.
- Не вставляйте секреты в issue, мессенджер, AI-чат, скриншот, HAR, traceback и console output.
- Выдавайте плагину минимальный набор `permissions.secrets`; read-action без секретов должен использовать пустой список.
- Не добавляйте секреты в manifest, options или `requirements.txt` — эти данные не шифруются как account payload.
- Блокируйте vault после работы и полностью завершайте Hub на общей машине.
- Храните мастер-пароль отдельно от backup. Проверяйте возможность восстановления на тестовой копии.
- Делайте backup только при остановленном Hub и копируйте весь data directory, чтобы не получить несогласованный SQLite/WAL snapshot.
- Перед mainnet-запуском вручную проверяйте chain, RPC, адреса контрактов, лимиты, allowance и ожидаемый баланс в независимом источнике.
- Фиксируйте версии зависимостей; для критичных плагинов используйте проверенные hashes/локальный wheelhouse и воспроизводимую сборку.

## Реагирование на инцидент

Если есть подозрение на утечку или вредоносный плагин:

1. Остановите активные runs, завершите Hub и, если эксфильтрация еще возможна, отключите сеть машины.
2. Считайте скомпрометированными все секреты, выданные плагину, а при компрометации OS-пользователя — весь vault после последнего unlock.
3. Для EVM-ключа переведите активы и полномочия на новый кошелек; отзовите token approvals, sessions и делегированные права. Одной смены мастер-пароля недостаточно.
4. Смените proxy credentials, email password, Twitter credential, Capsolver/AdsPower API keys, раскрытые referral codes и активные email/API sessions, которые могли быть выданы плагину.
5. Сохраните для анализа копию data directory, hash исходного `.softhub.zip`, manifest, `requirements.txt`, run ID, timestamps и внешние transaction hashes. Делайте копию после остановки Hub и не открывайте ее на основной машине.
6. Не запускайте и не подготавливайте подозрительный пакет повторно. Удаление в UI стирает Hub-owned code, все версии и `.venv`, но сохраняет audit history/results и не отзывает уже раскрытые секреты. Активный или `needs_attention` run сначала нужно остановить и сверить.
7. Перезапустите Hub, чтобы сменился loopback API token. Если подозревается подмена frontend/core, переустановите приложение из доверенного DMG с проверенным SHA-256; разработчики могут восстановить его из проверенного исходника.
8. Сопоставьте `needs_attention` runs с chain explorer/API проекта. Не повторяйте write-действие, пока не исключен предыдущий broadcast.

Если скомпрометирован только мастер-пароль, но злоумышленник имел доступ к копии data directory, считайте account secrets раскрываемыми и все равно ротируйте их. Функции rekey в MVP нет: создайте чистый data directory с новым паролем и импортируйте уже замененные credentials.

## Ограничения текущего MVP

### Неподписанные пакеты и приложения

Плагины имеют статус `local_unsigned`. SHA-256 не доказывает авторство: злоумышленник может изменить package payload и пересчитать находящийся рядом checksum manifest.

Текущая локальная macOS-сборка использует ad-hoc подпись и не проходит Apple notarization. Ad-hoc подпись помогает macOS проверить внутреннюю согласованность code bundle, но не связывает его с проверенным издателем и не заменяет Developer ID. Поэтому Gatekeeper может заблокировать первое открытие preview.

Публичный релиз требует сертификата Developer ID Application, hardened runtime, Apple notarization и stapling. До появления такого pipeline распространяйте preview только по доверенному каналу вместе с внешним SHA-256. Не отключайте Gatekeeper глобально и не удаляйте quarantine-атрибут ради неизвестной сборки; для проверенного локального preview используйте штатный **Privacy & Security → Open Anyway**.

### Управляемый Python runtime

Текущий packaged Electron app включает CPython 3.12.13, зависимости core и сам `soft_hub`. Builder загружает закреплённый runtime archive, проверяет его заданный SHA-256, устанавливает `requirements-runtime.lock`, фиксирует hash исходников и выполняет import/crypto self-check. Launcher в packaged-режиме рассматривает только interpreter из `Contents/Resources/python`, запускает его с `-I` и не использует `PATH`, системный Python либо `SOFT_HUB_PYTHON` как fallback.

Managed runtime всё равно является частью trust boundary: доверять приходится источнику CPython artifact, закреплённому hash, release builder, lock-файлу и включённым wheels. Self-check обнаруживает часть повреждений и несовместимостей, но не доказывает отсутствие вредоносного кода и не создаёт sandbox.

Системный Python и `SOFT_HUB_PYTHON` используются только при разработке/запуске из исходников; разработчику нужен отдельный `.venv`. Команда **Подготовить** создаёт `.venv` плагина на базе interpreter Hub и затем устанавливает его `requirements.txt`. Эти сетевые зависимости и их build hooks остаются отдельным supply-chain риском, даже когда core runtime встроен в приложение.

### Нет OS sandbox и signing broker

Нет container/VM, seccomp, App Sandbox, Windows AppContainer, filesystem allowlist, network egress filter, privilege separation или отдельного низкопривилегированного пользователя. Нет внешнего broker, который подписывает заранее проверенную транзакцию без выдачи ключа. Плагин с разрешением `evm_private_key` получает private key целиком.

### Другие отсутствующие механизмы

- нет multi-user access control, TLS, remote API или rate limiting для unlock;
- нет публичного Developer ID/notarized release pipeline для текущего preview;
- нет автоматической очистки scratch, malware scanning и dependency audit;
- нет автоматического или зашифрованного backup/restore, rekey и recovery master password; plaintext XLSX/raw CSV export не заменяет эти механизмы;
- нет гарантии, что force stop откатит внешнее действие;
- нет аппаратного хранилища ключей и интеграции с OS keychain;
- нет проверки реального network destination/chain транзакции;
- нет независимого security audit для этой версии.

До устранения этих ограничений относитесь к Soft Hub как к удобному локальному оркестратору доверенного кода, а не как к изолированному хранилищу для запуска недоверенных автоматизаций.
