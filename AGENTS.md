# Jabber Toolbox — архитектура проекта

XMPP-компонент (transport) «Jabber Toolbox», агрегирующий несколько ботов-сервисов.
Написан на Python 3 + `slixmpp` (XMPP-компонент, режим `ComponentXMPP`). Каждый бот —
отдельная JID-сущность вида `NAME@<domain-компонента>`.

Полное функциональное описание см. в [SPEC.md](SPEC.md).

---

## Запуск

```bash
pip install -r requirements.txt   # slixmpp==1.10.0
cp config.ini.example config.ini  # настроить [server]
python3 toolbox.py

python3 selftest.py               # автономные тесты (без XMPP-сервера)
```

Конфиг читается из `config.ini`: секция `[server]` (`JID`, `Host`, `Port`, `Password`),
секция `[settings]` (`Timezone` — смещение от UTC в часах, `TitleDefaultLength` — длина
первой строки для автозаголовка). Значения из `[settings]` заносятся в глобальные
константы модуля `plugins` (`TZ_OFFSET`, `TITLE_DEFAULT_LEN`), откуда их читают боты.

---

## Структура каталога

| Файл | Роль |
|------|------|
| `toolbox.py` | Ядро: класс `Toolbox(ComponentXMPP)`, сеть, протокольные функции, маршрутизация сообщений, фоновая доставка напоминаний, `main()`, `load_config()`. |
| `plugins/__init__.py` | Плагинный каркас: базовый класс `Bot`, загрузчик `load_plugins`, общие утилиты и глобальные настройки. |
| `plugins/<name>.py` | Каждый файл — один или несколько классов-ботов. |
| `config.ini` / `config.ini.example` | Настройки соединения и общие параметры. |
| `selftest.py` | Автономные проверки всех ботов и ядра (запуск без сервера). |
| `requirements.txt` | Зависимости (`slixmpp==1.10.0`). |
| `*.sqlite3` | БД ботов, создаются автоматически (`note`, `reminder`, `shorty`, `whois_cache`). |

---

## Архитектурные слои

### 1. Ядро — `toolbox.py`

- **`class Toolbox(ComponentXMPP)`** — подключается к серверу как компонент.
- **Инициализация** (`__init__`): ставит плагины slixmpp, грузит ботов, настраивает
  disco, обработчики и in-band регистрацию.
- **Подключённые XEP**: `xep_0030` (disco), `xep_0004` (data forms), `xep_0050`
  (ad-hoc), `xep_0092` (version), `xep_0012` (uptime), `xep_0054` (vCard), `xep_0085`
  (chat states), `xep_0184` (receipts, `auto_ack=False` — подтверждаем вручную).
- **Регистрация ботов** (`_load_bots`): имя приводится к нижнему регистру, проверяется
  на допустимость `[a-z0-9_-]`; каждому назначается `bot.jid = 'NAME@domain'`.
- **disco** (`_setup_disco`): identity и фичи самого компонента + по каждому боту.
- **ad-hoc** (`_setup_adhoc`, вызывается на `session_start`): команда на узел
  `bot.NAME`, запускает подписку на бота.
- **Регистрация XEP-0077** (`_setup_register`): кастомные матчеры
  `MatchRegisterQuery`, `MatchTimeQuery`.
- **Обработчики событий** (`_setup_handlers`): `session_start`, `message`,
  presence-события (`available`, `unavailable`, `subscribe`, `subscribed`,
  `unsubscribe`, `unsubscribed`).
- **Маршрутизация**: `_on_message` отдаёт текст боту по адресату; если адресат —
  не бот и не сам компонент, отвечает списком сервисов.
- **Фоновая задача** `_check_reminders_loop` (каждые 60 c) вызывает
  `bot.check_reminders()` у ботов, у которых этот метод есть.

### 2. Плагинный каркас — `plugins/__init__.py`

Контракт бота (`class Bot`):

```python
class Bot:
    NAME = ''         # локальная часть JID и ник
    DESCRIPTION = ''  # краткое описание (vCard, списки сервисов)
    HELP = ''         # справка на пустой ввод

    def __init__(self):
        self.jid = ''

    async def handle(self, text, ctx):
        # ctx = {'from': JID, 'to': JID}; возвращает строку-ответ
        raise NotImplementedError()
```

Загрузчик `load_plugins(directory)`:
- импортирует каждый `*.py`, не начинающийся с `_`;
- собирает все подклассы `Bot` (проверка `issubclass` + `__module__`) и
  инстанцирует их;
- неудачное исполнение модуля логируется, процесс продолжается.

Общие утилиты (модуль `plugins`):
- `valid_host(token)` — похоже ли на hostname/IP (включая IPv6).
- `split_args(text)` — `shlex.split` с запасным `text.split()`.
- `parse_flags(args)` — `(set_of_flags, positional)`; флаг — аргумент, начинающийся с `-`.
- `check_tcp(host, port, family, timeout=5.0)` — асинхронная проверка TCP,
  возвращает `(ok, elapsed_ms, err_str)`.

Глобальные настройки модуля: `TZ_OFFSET` (часы), `TITLE_DEFAULT_LEN`.

### 3. Состояние диалогов и персистентность

- Диалоги хранятся в `self.state[jid]` (bare JID пользователя). Это по сути
  конечный автомат бота: `'mode'` + данные шага. Боты с многошаговыми диалогами
  (note, reminder) переключают `state['mode']` и обрабатывают ввод диспетчером
  `_mode_<mode>`.
- reminder дополнительно держит `last_view` (последний список: `list`/`arch`/`srch`)
  и `last_search` (список id результата поиска) — для корректного открытия по номеру.
- Данные ботов — в отдельных SQLite-файлах, изолированы по `owner` (bare JID).

### 4. Жизненный цикл сообщения

1. Приходит `message` → `_on_message`.
2. Пропускаются: groupchat/error, от самих себя (компонент/боты),
   подтверждения receipt (XEP-0184), пустые chat-state сообщения.
3. Определяется бот по адресату (`_bot_for`).
4. Если бота нет — ответ списком сервисов `TOOLBOX_NAME v...` и завершение.
5. Отправляется receipt (`_ack_receipt`), формируется `ctx`.
6. Слать `composing` (XEP-0085) → `await bot.handle(body, ctx)`.
7. Ошибка бота ловится; ответ — `Internal error: <...>`.
8. Отправляется reply: body = результат (или `bot.HELP` при пустом),
   chat state `active`, `request_receipt=True`.

### 5. Presence и статусы

Компонент зеркалирует присутствие пользователя на ботов: когда пользователь
входит/выходит, его боты появляются/исчезают у него. Обмен подписками
(subscribe/subscribed) автоматизирован. Статус бота — `bot.DESCRIPTION or bot.HELP`;
статус самого компонента — `'%s online. Send "help" for usage.' % NAME`.

---

## Тестирование

`selftest.py` — автономный прогон (не подключается к сети; сетевые боты в части тестов
не вызываются реально). Структура:

- `check(name, cond, extra)` — печатает `[ok]`/`[FAIL]` и копит `FAILED`.
- Боты создаются из тех же плагинов, что и в проде; диалоги гоняются через
  `await bot.handle(...)` с поддельным `ctx` (`{'from': slixmpp.JID(...)}`).
- `_fake_iq(...)` строит поддельные IQ для проверки version/uptime/vCard.
- Конец: если `FAILED` непуст — `sys.exit(1)`, иначе «All checks passed».

---

## Конвенции

- Ответы большинства ботов на русском (строки с кодировкой «UTF-8»). Справки и
  технические строки частично на английском (наследие) — не смешивать стили без
  необходимости.
- Списки ботов форматируются строкой вида `N. | метка-времени | заголовок`; символ
  `|` в заголовке заменяется на `/` (`title.replace('|', '/')`), чтобы не ломать формат.
- Метки времени — локальные по `TZ_OFFSET`, формат `%Y-%m-%d %H:%M` (см. `_fmt` в
  note/reminder, `_local_tz` в reminder).
- Времена в БД хранятся в UTC (Unix-timestamp).
- Не добавлять лишних комментариев в код; следовать существующему стилю.

<!-- CODE_BRAIN_MANDATORY -->
## Code Brain MCP - Mandatory when loaded

Use Code Brain MCP first for substantive tasks in this project.

- Start with `start_task(runIntake=true)` or `neural_sync`.
- Use `agent_plan` before `agent_code` for chunk and deep work.
- Use `memory_retrieve` at intake and `memory_store` at task end.
- Use `uncertainty_guard` before storing conclusions.
- Disable duplicate MCPs with `get_superseded_mcps`.
<!-- CODE_BRAIN_MANDATORY -->

