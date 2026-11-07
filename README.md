# Скорифай — анализ изменений решений СПР

## Запуск на Mac

На этом Mac установка уже выполнена, локальный пароль восстановлен: **admin / admin**.
Откройте `start-local.command` двойным щелчком и перейдите на http://127.0.0.1:8765.
Если приложение уже работает, используйте открытый адрес; второй экземпляр не нужен.
Остановка — Ctrl+C в окне запуска. Сброс локального пароля — `reset-local-login.command`.

На новом Mac сначала установите Python 3.12–3.14 и откройте `install-local.command`.
Этот установщик явно создаёт/сбрасывает демонстрационный доступ admin/admin.

## Единые команды Windows macOS Linux

Из каталога проекта, Python 3.12–3.14:

```text
python3 launch.py install --local-demo
python3 launch.py start
```

На Windows используйте `python` вместо `python3`; после установки доступен `start-local.cmd`.
Для установки с собственным паролем вместо демонстрационного: `python3 launch.py install`.
Для существующей базы установка сохраняет пароль; явный сброс: `python3 launch.py reset-login`.
`start` применяет миграции и запускает API и worker; данные в `var` не сбрасываются.
Для импорта комплектного набора после установки: `.venv/bin/python -m spr.cli demo`
(Windows: `.venv\Scripts\python -m spr.cli demo`).

Тесты: `python3 launch.py test`. В этой команде доверенное исполнение выключено, чтобы проверять изоляцию.
Не переносите `.venv` и `node_modules` между ОС — создавайте их заново.
Собранный `frontend/dist` переносим и не требует Node.js. Данные переносите через backup/restore,
который обновляет пути. Старые Windows-резервные копии с `\` в списке файлов поддерживаются.

Единый локальный запуск включает доверенное исполнение Python: код имеет права пользователя.
Для изолированного режима запускайте `spr.cli serve` напрямую без `SPR_TRUSTED_LOCAL_CODE`;
на Mac используется Seatbelt, на Linux нужен bubblewrap, на Windows изолированное исполнение отключено.
Mac не обеспечивает лимит памяти через RLIMIT_DATA; ограничения CPU, времени, файлов и сеть проверены тестами.
Не используйте доверенный режим для недоверенных сетевых пользователей.
Для своей сети: `python3 launch.py start --host 0.0.0.0`; сначала смените admin/admin через
`.venv/bin/python -m spr.cli password admin` и задайте `BASE_URL` с доступным сетевым адресом.

Матрица CI добавлена для Windows/macOS/Linux и Python 3.12/3.14; результаты удалённого CI пока не получены.
Фактическая проверка этой ревизии проведена на macOS arm64 с Python 3.14.8.
Подробности исправлений и незакрытых требований: [аудит](docs/audit-2026-11-07.md).

FastAPI и React сравнивают загруженные ответы BEFORE/AFTER, сохраняют результаты в Parquet и позволяют перейти от количества в сводной к заявкам и исходным документам. SQLite хранит пользователей, проекты и задания; DuckDB читает опубликованные артефакты, Polars выполняет преобразования. Docker не требуется.

## Локальный запуск Windows

Из корня проекта, Python 3.12–3.14:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m spr.cli init
.\.venv\Scripts\python -m spr.cli demo
.\.venv\Scripts\python -m spr.cli serve
```

`init` применяет миграции и запрашивает пароль администратора (от 12 символов). `demo` импортирует открытый учебный набор. Приложение: http://127.0.0.1:8765, пользователь `admin`. Фронтенд уже собран. Команда `serve` запускает API и отдельный worker; Ctrl+C останавливает оба процесса.

Если локальная БД уже создана, установить собственный пароль:

```powershell
.\.venv\Scripts\python -m spr.cli password admin
```

Произвольные Python-проверки на Windows отключены: безопасный исполнитель реализован для Linux bubblewrap и macOS Seatbelt. Импорт, сравнение, сводная, сценарии и экспорт работают без пользовательского Python. Наличие bubblewrap ещё не подтверждает рабочую изоляцию: на сервере нужны отдельные проверки.

## Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m spr.cli init
.venv/bin/python -m spr.cli demo
.venv/bin/python -m spr.cli serve
```

Службы, HTTPS, резервирование и порядок установки: [развёртывание](docs/deployment.md). Файл `.env.example` — образец переменных; приложение не читает `.env` автоматически. Перед локальным запуском экспортируйте переменные в оболочке.

## Работа и проверка

1. В «Данные и профили» создайте датасет, загрузите BEFORE и AFTER. Один JSON/XML/CSV-файл соответствует одной заявке, имя файла — APP_ID.
2. В «Конструктор» выберите датасет и профиль `main` для открытого набора; запустите обработку. Не подключайте Python-проверки без изоляции ОС.
3. Откройте обзор, сводную и качество. Нажатие на число открывает заявки; на заявку — сравнение и исходные ответы.
4. HTML-отчёт сохраняется с реальными числами и абсолютной ссылкой на прогон. CSV использует BOM и `;`.

```powershell
.\.venv\Scripts\python -m pytest -q
$env:POLARS_MAX_THREADS='4'
.\.venv\Scripts\python scripts/benchmark.py --output tmp/measurement
.\.venv\Scripts\python scripts/benchmark.py --synthetic 20000 --output tmp/synthetic
```

Сборка интерфейса для разработчика (Node.js 22+): `cd frontend`, `npm ci`, `npm run build`. Автоматизация браузера: `scripts/browser-smoke.cjs`, нужен Playwright и установленный Chromium (или `BROWSER_CHANNEL=chrome` / `msedge`); путь к JSON с тестовыми реквизитами задаётся через `SPR_TEST_CREDENTIALS`. Реквизиты не включайте в Git.

Подробности: [архитектура и контракты](docs/architecture.md), [матрица требований](docs/requirements-matrix.md), [результаты проверки](docs/testing.md), [ограничения](docs/limitations.md), [сценарий демонстрации](docs/demo.md).

## Python на локальном Windows

`start-local.cmd` включает `SPR_TRUSTED_LOCAL_CODE=1`: доверенные проверки выполняются отдельным Python-процессом, максимум 25 секунд. Это не песочница: код имеет доступ к файлам и сети от имени пользователя ноутбука. Загружайте только доверенные скрипты. Для ручного запуска задайте эту переменную перед `python -m spr.cli serve`.

## Пользователи и дополнительные модули

Регистрация доступна на странице входа. Новые аккаунты ожидают назначения проектных ролей в меню администратора. [Роли, модуль пула и подключение СПР](docs/roles-and-modules.md). [Обязательная записка команды 1006](docs/team-note.md).
