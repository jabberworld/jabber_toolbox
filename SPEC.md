# Jabber Toolbox — полная спецификация

> Документ описывает проект «Jabber Toolbox» (далее — «транспорт» или «компонент»)
> настолько подробно, что по нему можно воссоздать эквивалентный проект с нуля.
> Здесь зафиксированы: протокольное поведение, все команды и состояния ботов,
> точные тексты ответов и подсказок, схемы баз данных, алгоритмы нумерации и
> фоновых задач. Тексты ответов на русском приведены в UTF-8.

---

## 1. Назначение

Jabber Toolbox — это XMPP-**компонент** (transport), который публикует набор
«ботов-сервисов» внутри одного домена компонента. Каждый бот — отдельная
JID-сущность вида `NAME@<домен компонента>` (например `note@service2.example.com`).
Пользователи Jabber общаются с ботами как с обычными контактами: добавляют их в
ростер, пишут им личные сообщения, получают ответы.

Решаемая задача: вынести «мелкие удобные сервисы» (заметки, напоминания, проверка
портов/сайтов/сертификатов, WHOIS, сокращение ссылок, ping) за рамки
пользовательского клиента — в централизованный компонент, доступный любому
пользователю домена.

Компонент:
- не хранит пользовательских учётных записей (это делает сервер);
- подключается к серверу как компонент по сокетному домену/паролю;
- самостоятельно отвечает на сервисные IQ-запросы (version/uptime/vCard/время/register);
- маршрутизирует личные сообщения, адресованные ботам;
- зеркалирует присутствие пользователя на его ботов.

---

## 2. Стек, окружение, запуск

- Язык: Python 3 (требуется 3.8+; используется asyncio, `async/await`, f-строки не
  обязательны, код использует `%`-форматирование и `str.format`-подобные операции).
- Единственная внешняя зависимость: `slixmpp==1.10.0` (файл `requirements.txt`).
- Хранилища: SQLite (модуль `sqlite3` из стандартной библиотеки; каждый бот
  открывает свой файл своей БД).
- Запуск:
  ```bash
  pip install -r requirements.txt
  cp config.ini.example config.ini     # затем отредактировать
  python3 toolbox.py
  ```
- Автономные тесты (без сервера): `python3 selftest.py`.
- Библиотечный код не использует внешних сетей помимо явно указанных операций
  ботов (TCP/SSL/WHOIS/HTTP-API).

### Структура каталога

| Путь | Назначение |
|------|-----------|
| `toolbox.py` | Ядро компонента: класс `Toolbox(ComponentXMPP)`, вся протокольная логика, маршрутизация, фоновая доставка напоминаний, `load_config()`, `main()`. |
| `plugins/__init__.py` | Плагинный каркас: класс `Bot`, загрузчик `load_plugins`, утилиты, глобальные настройки. |
| `plugins/isdown.py` | Бот проверки «сайт лежит?». |
| `plugins/ping.py` | Бот ICMP-ping. |
| `plugins/port.py` | Бот проверки порта TCP/UDP. |
| `plugins/sslcheck.py` | Бот проверки SSL-сертификата. |
| `plugins/shorty.py` | Бот-сокращатель ссылок. |
| `plugins/whois.py` | Бот WHOIS по доменам и IP. |
| `plugins/whoip.py` | Бот «домен→IP→WHOIS». |
| `plugins/note.py` | Бот личных заметок. |
| `plugins/reminder.py` | Бот напоминаний. |
| `config.ini` / `config.ini.example` | Конфигурация соединения и настроек. |
| `selftest.py` | Автономный набор проверок. |
| `note.sqlite3`, `reminder.sqlite3`, `shorty.sqlite3`, `whois_cache.sqlite3` | Файлы БД, создаются автоматически при первом обращении бота. |

---

## 3. Общая архитектура

```
                 XMPP-сервер (ejabberd/Prosody/...)
                            │  (компонентное соединение, домен+пароль)
        ┌───────────────────▼───────────────────┐
        │            Toolbox.py                  │
        │  Toolbox(ComponentXMPP)                 │
        │  ─ bots: {name -> Bot}                  │
        │  ─ disco, ad-hoc, register, time,       │
        │    vCard, version, uptime, presence,    │
        │    receipts, chat-states                │
        │  ─ _check_reminders_loop (каждые 60 c)  │
        └───────────────┬─────────────────────────┘
                        │  вызов bot.handle(text, ctx)
        ┌───────────────▼─────────────────────────┐
        │  plugins/<name>.py (классы Bot)          │
        │  каждая с своей SQLite БД                │
        └──────────────────────────────────────────┘
```

Ключевые принципы:
- **Транспорт ничего не знает о внутренностях ботов** — только его контракт
  (`handle`), имя, описание и справка.
- Все ответы — обычные строки; транспорт сам оборачивает их в стanza (
  chat state, receipt).
- Многошаговые диалоги реализуются ботами через **собственный конечный автомат**
  (`self.state[jid]`), транспорт не является «хранителем» состояния диалога.
- Данные между сессиями не теряются благодаря SQLite.

---

## 4. Протокольное поведение компонента (toolbox.py)

### 4.1 Константы и глобальные параметры

`toolbox.py` определяет:

| Константа | Значение | Назначение |
|-----------|----------|------------|
| `TOOLBOX_NAME` | `'Jabber Toolbox'` | Отображаемое имя компонента. |
| `TOOLBOX_VERSION` | `'1.0.0'` | Версия (version query, статусы). |
| `TOOLBOX_URL` | `'https://jabberworld.info'` | URL в vCard. |
| `TOOLBOX_BDAY` | `'2026-08-24'` | Дата рождения в vCard. |
| `TOOLBOX_PHOTO` | base64-строка PN | Фотография в vCard (PNG, «image/png»). |
| `VCARD_NS` | `'vcard-temp'` | Пространство имён vCard. |

Логгер ядра: `logging.getLogger('toolbox')`.

### 4.2 Класс `Toolbox(ComponentXMPP)`

Сигнатура: `Toolbox(jid, secret, host, port)`, где `jid` — домен компонента
(например `service2.ets.jabberworld.info`), `secret` — компонентный пароль,
`host`/`port` — адрес сервера.

Конструктор выполняет по порядку:

1. `super().__init__(jid=jid, secret=secret, host=host, port=port)`
2. записывает `self.start_time = time.time()` (для uptime);
3. инициализирует `self.bots = {}`;
4. `_setup_plugins()` — регистрация XEP-плагинов slixmpp и их настройка;
5. `_load_bots()` — загрузка ботов из `plugins/`;
6. `_setup_disco()` — настройка Service Discovery;
7. `_setup_handlers()` — подписка на события (`session_start`, `message`,
   presence-события);
8. `_setup_register()` — регистрация кастомных IQ-обработчиков
   (регистрация и время).

### 4.3 Настройка XEP-плагинов (`_setup_plugins`)

Регистрируются плагины slixmpp:

| Плагин | XEP | Зачем |
|--------|-----|-------|
| `xep_0030` | Service Discovery | `disco#info` / `disco#items`. |
| `xep_0004` | Data Forms | Формы в регистрации. |
| `xep_0050` | Ad-Hoc Commands | Команда подписки на каждого бота. |
| `xep_0092` | Software Version | Ответ на запрос версии. |
| `xep_0012` | Last Activity | Ответ uptime. |
| `xep_0054` | vCard-temp | Ответ vCard. |
| `xep_0085` | Chat State Notifications | Отправка `composing`/`active`. |
| `xep_0184` | Message Delivery Receipts | Подтверждение доставки. |

Дополнительные настройки:
- `self['xep_0184'].auto_ack = False` — **подтверждения не отправляются
  автоматически**, только вручную (см. 6.5).
- Версия плагина `xep_0092`: `software_name=TOOLBOX_NAME`,
  `version=TOOLBOX_VERSION`, `os='Python <версия> / slixmpp <версия>'`.
- `xep_0012.api.register(self._get_uptime, 'get_last_activity', default=True)`.
- `xep_0054.api.register(self._get_vcard, 'get_vcard', default=True)`.

### 4.4 Загрузка ботов (`_load_bots`)

```python
for bot in load_plugins(os.path.join(BASE_DIR, 'plugins')):
    name = (bot.NAME or '').strip().lower()
    if not name or not all(c.isalnum() or c in '-_' for c in name):
        continue                     # бот пропускается, пишется warning
    bot.NAME = name
    bot.jid = '%s@%s' % (name, self.boundjid.domain)
    self.bots[name] = bot
```

Правила:
- Имя бота приводится к нижнему регистру;
- допустимые символы: буквы/цифры (`isalnum`) и `-_`;
- JID бота = `имя@домен-компонента`;
- словарь `self.bots` индексируется локальной частью (без регистра).

### 4.5 Service Discovery (`_setup_disco`)

**Для корня компонента** добавляется identity:
`category='component', itype='generic', name=TOOLBOX_NAME`
и фичи:
- `jabber:iq:version`
- `jabber:iq:last`
- `vcard-temp`
- `urn:xmpp:time`
- `jabber:iq:register`
- `http://jabber.org/protocol/commands`

**Для каждого бота** (сортировка по имени) добавляется:
- identity: `category='account', itype='generic', name='<name> - <DESCRIPTION>'`,
  с указанием `jid=bot.jid`;
- фичи (внутри JID бота):
  - `jabber:iq:version`
  - `jabber:iq:last`
  - `vcard-temp`
  - `urn:xmpp:time`
  - `urn:xmpp:receipts`
  - `http://jabber.org/protocol/chatstates`
- item к корню: `disco.add_item(jid=bot.jid, name=..., ijid=root)` — так боты
  перечисляются в `disco#items` корня.

### 4.6 Ad-Hoc команды (`_setup_adhoc`)

Вызывается из `_on_session_start` **после** session bind (комментарий в коде:
XEP-0050 сбрасывает disco-элементы командного узла при session_bind). Для каждого
бота регистрируется одна команда:

```python
self['xep_0050'].add_command(
    jid=str(self.boundjid), node=bot.NAME,
    name=self._display_name(bot),
    handler=partial(self._cmd_add_bot, bot=bot))
```

`_display_name(bot)` возвращает `'<name> - <DESCRIPTION>'`.

Обработчик `_cmd_add_bot(iq, session, bot)`:
- отправляет запрос подписки от бота пользователю:
  `self._request_subscription(bot, session['from'])` — это presence
  `type='subscribe'` от `bot.jid` к пользователю;
- завершает команду без payload и припиской:
  `'%s has sent you a subscription request. Approve it and the bot will show up as online.' % bot.jid`.

### 4.7 Регистрация в band и время (XEP-0077 / urn:xmpp:time)

В `_setup_register()` регистрируются два обработчика через `register_handler` +
`CoroutineCallback` с собственными матчерами:

- `MatchRegisterQuery(MatcherBase)` — `match()` возвращает `True`, если у stanza
  есть дочерний элемент `{jabber:iq:register}query`.
- `MatchTimeQuery(MatcherBase)` — `match()` возвращает `True`, если у stanza есть
  дочерний элемент `{urn:xmpp:time}time`.

#### Обработчик времени `_handle_time(iq)`

Возвращает `iq.reply()` с элементом `<time xmlns="urn:xmpp:time">`:
- `<utc>` — текущее время UTC, формат `%Y-%m-%dT%H:%M:%SZ`;
- `<tzo>` — смещение относительно UTC по `TZ_OFFSET`, формат `±HH:00`;
- `<display>` — текущее время в локальном поясе `TZ_OFFSET`,
  формат `%Y-%m-%d %H:%M:%S`.

Обработчик обслуживает **и корень компонента, и всех ботов** (мастер-матчер без
фильтра по `to`).

#### Обработчик регистрации `_handle_register(iq)`

- `type='get'`: возвращает форму с инструкцией:
  ```
  Submitting this form adds Jabber Toolbox to your roster. Approve the
  following subscription request and it will come online.
  ```
- `type='set'`: от имени корня шлёт пользователю:
  1. presence `subscribe` (`_send_subscription_request(str(self.boundjid), user)`);
  2. presence `available` от корня пользователю со статусом
     `'%s online. Send "help" for usage.' % TOOLBOX_NAME`;
  3. форму (как в `get`).

Форма строится функцией `_build_register_reply(iq)` — в `<query>` элемент
`<instructions>`.

### 4.8 Presence: зеркалирование и подписки

Цель: как только пользователь появляется в сети, его боты тоже «появляются» у
него; при уходе — исчезают. Плюс автоматический взаимный обмен подписками.

Вспомогательные функции:

- `_bot_for(jid)` → `self.bots.get(jid.user.lower())` — бот по адресату.
- `_entity_info(jid)` → возвращает пару `(source_jid, label)`:
  - если это бот — `(bot.jid, '<name> bot')`;
  - если это корень компонента — `(str(self.boundjid), TOOLBOX_NAME)`;
  - иначе `None`.
- `_send_subscription_request(from_jid, user)` — presence `subscribe`.
- `_send_entity_presence(source_jid, to, ptype=None, status=None)` — presence от
  `source_jid` к `to`; `ptype='available'` очищается (не пишется), остальные
  типы пишутся в `pres['type']`; при `status` заполняется `<status>`.

Определение статуса сущности (везде одинаковое):
```
status = (bot.DESCRIPTION or bot.HELP) if есть бот
         else '%s online. Send "help" for usage.' % label  # label=TOOLBOX_NAME
```

#### Обработчики

- `presence_available` / `presence_unavailable` → `_mirror_presence(pres)`:
  если `pres['to']` распознан `_entity_info`, отправляет от соответствующего
  источника (`bot.jid` или корня) presence пользователю с `pres['type']`
  (или `available`) и статусом выше.
- `presence_subscribe` → `_on_subscribe(pres)` (пользователь подписывается на
  сущность): отправляем `subscribed`, затем взаимное `subscribe`, затем
  `available` со статусом.
- `presence_subscribed` → `_on_subscribed(pres)` (пользователь одобрил нашу
  подписку): отправляем `available` со статусом.
- `presence_unsubscribe` → `_on_unsubscribe(pres)`: `unsubscribed` + `unavailable`.
- `presence_unsubscribed` → `_on_unsubscribed(pres)`: `unavailable`.
- `_handle_probe(pres)`: на presence-пробу отвечает `available` со статусом.
  (Обработчик проб подключается транспортным кодом — в selftest проверяется.)

### 4.9 Сообщения → боты (`_on_message`)

#### Шаг 1. Фильтрация

Пропускаются (игнорируются) сообщения:
- с `type` в `('groupchat', 'error')`;
- от самой системы или от ботов (`_from_self(jid)`):
  `JID(jid).bare == str(self.boundjid)` **или** `_bot_for(jid) is not None`;
- с заполненным `msg['receipt']` (это подтверждение доставки — только логируется);
- пустой `body` при наличии chat-state (сообщения-статусы).

#### Шаг 2. Маршрутизация

Определяется `bot = self._bot_for(msg['to'])`.

Если бот **не найден** — отвечает с адреса сообщения списком сервисов:
```
Jabber Toolbox v1.0.0
Available services:
  isdown@<домен> - <DESCRIPTION isdown>
  note@<домен> - <DESCRIPTION note>
  ...
Run one of my ad-hoc commands to add a bot to your roster, or add a bot JID directly.
```
и завершает обработку.

#### Шаг 3. Обработка

1. Если исходное `body` пусто — `out = bot.HELP`.
2. Иначе:
   - `_ack_receipt(bot, msg)` — если в сообщении был `request_receipt`, бот
     отправляет подтверждение (см. 4.10);
   - `_send_chat_state(bot, msg['from'], 'composing')` — шлётся chat-state
     `composing` от бота;
   - `ctx = {'from': msg['from'], 'to': msg['to']}`;
   - `out = await bot.handle(body, ctx)`.
3. Любое исключение перехватывается: логируется (`log.exception`), ответ —
   `'Internal error: <тип/текст>'`.
4. Ответ — reply:
   - `body = out if out else bot.HELP`;
   - `chat_state='active'`;
   - `request_receipt=True`;
   - отправка `reply.send()`.

#### 4.10 Receipt (XEP-0184)

`_ack_receipt(bot, msg)` — подтверждение доставки от бота:
- условие: `msg['id']` задан и `msg['request_receipt']` истинно;
- создаётся сообщение от `bot.jid` к отправителю, `type` — как у входящего
  (если `chat`/`normal`, иначе `chat`);
- `ack['receipt'] = msg['id']`; `ack.send()`.

`_send_chat_state(bot, to, state)` — сообщение с chat-state
(`chat_state=state`, `type='chat'`).

#### 4.11 Background: `_check_reminders_loop`

Запускается в `_on_session_start` через `asyncio.ensure_future`. Цикл:
- `await asyncio.sleep(60)`;
- для каждого бота: если у него есть метод `check_reminders` — вызывается
  `bot.check_reminders()`, результат — список `(owner_bare_jid, text)`;
  каждый элемент отправляется `_send_bot_message(bot, owner_jid, text)` —
  сообщение от `bot.jid` адресату с `body`, `chat_state='active'`,
  `request_receipt=True`;
- исключения ловятся и логируются (`log.exception('check_reminders failed for %s', ...)`),
  цикл продолжается.

### 4.12 Version / uptime / vCard

- `_get_uptime(jid, node, ifrom, iq)` — возвращает Iq с `last_activity.seconds`
  = `int(time.time() - self.start_time)`. Совместимо с объектом `slixmpp.stanza.Iq`
  (тогда `iq.reply()`) либо с произвольным вызовом (создаётся `self.Iq(stype='result')`).
- `_get_vcard(jid, node, ifrom, args)` — строит `VCardTemp()`:
  - для любого распознанного бота: `FN`, `NICKNAME` = имя бота,
    `DESC` = `bot.DESCRIPTION or bot.HELP`;
  - для корня: `FN`/`NICKNAME` = `TOOLBOX_NAME`,
    `DESC` = `'%s v%s. Services: <список имён через запятую>'`
    (если ботов нет — `'none loaded'`);
  - всегда: `BDAY=TOOLBOX_BDAY`, `URL=TOOLBOX_URL`, `PHOTO` с `TYPE=image/png`
    и `BINVAL=TOOLBOX_PHOTO`.

### 4.13 `load_config(path)` и `main()`

`load_config(path)` использует `configparser.ConfigParser`:
- если файл не читается — `raise SystemExit('Cannot read config file: <path>')`;
- читает `[server]`: `jid`, `host`, `port` (getint), `password` (⇒ `secret`);
- читает `[settings]` (если есть секция): `timezone` (getint, по умолчанию 0),
  `titledefaultlength` (getint, по умолчанию 20);
- возвращает dict с ключами `jid`, `host`, `port`, `secret`, `timezone`,
  `title_default_length`.

`main()`:
1. `logging.basicConfig(level=INFO, format='%(asctime)s %(levelname)-8s %(name)s: %(message)s')`;
2. создаёт новый event loop;
3. читает `config.ini`;
4. заносит значения: `plugins_mod.TZ_OFFSET = cfg['timezone']`,
   `plugins_mod.TITLE_DEFAULT_LEN = cfg['title_default_length']`;
5. создаёт `Toolbox(...)`;
6. регистрирует обработчики `SIGINT`/`SIGTERM` → `xmpp.disconnect()`
   (если платформа это поддерживает);
7. логирует запуск, вызывает `xmpp.connect()`, ждёт `xmpp.disconnected`,
   в `finally` логирует остановку.

---

## 5. Плагинный каркас (plugins/__init__.py)

### 5.1 Глобальные атрибуты модуля

| Атрибут | Значение по умолчанию | Назначение |
|---------|----------------------|------------|
| `BASE_DIR` | каталог проекта (родитель `plugins/`) | Путь к файлам БД и плагинам. |
| `TZ_OFFSET` | `0` | Смещение времени от UTC в часах (из `config.ini [settings]`). |
| `TITLE_DEFAULT_LEN` | `20` | Длина первой строки, используемой как автозаголовок. |

Боты импортируют эти значения в момент вызова (через `from plugins import ...` в
методе), чтобы конфиг применялся без перезагрузки бота.

### 5.2 Класс `Bot`

```python
class Bot:
    NAME = ''          # локальная часть JID + ник
    DESCRIPTION = ''   # краткое описание (vCard, списки сервисов)
    HELP = ''          # справка на пустой ввод

    def __init__(self):
        self.jid = ''

    async def handle(self, text, ctx):
        # ctx = {'from': JID, 'to': JID}
        # возвращает строку-ответ (или пустую — тогда транспорт пришлёт HELP)
        raise NotImplementedError()
```

Внимание: конструктор бота должен вызывать `super().__init__()`, чтобы получить
`self.jid`.

### 5.3 Загрузчик `load_plugins(directory)`

Алгоритм:
1. Перебирает отсортированные имена файлов каталога.
2. Пропускает всё, что не оканчивается на `.py` или начинается с `_`.
3. Для каждого файла загружает модуль через `importlib.util` с именем
   `toolbox_plugin_<имя без .py>`.
4. Если исполнение модуля упало — логирует исключение
   (`log.exception('Failed to load plugin %s', fname)`) и продолжает.
5. Собирает объекты-классы, для которых выполняется:
   - `isinstance(obj, type)`,
   - `issubclass(obj, Bot)`,
   - `obj.__module__ == <имя модуля плагина>` (чтобы не подхватить чужие классы).
   Каждый такой класс инстанцируется (`obj()`) и добавляется в результат.
6. Если подходящих классов нет — `log.warning('No Bot subclass found in %s', fname)`.

Возвращает список экземпляров ботов.

### 5.4 Утилиты

#### `valid_host(token) -> bool`

`True`, если токен похож на hostname или IP-литерал (в т.ч. IPv6). Реализация —
регулярные выражения:
- `_HOSTNAME_RE`: `^[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?(\...)*$` — куски
  меток разделены точками;
- `_IPV6_RE`: расширенный вариант с сжатием `::`, IPv4-mapped и zone id (`%...`).
Пустой токен → `False`.

#### `split_args(text) -> [str]`

`shlex.split(text)`; при `ValueError` (несбалансированные кавычки и т.п.) —
запасной вариант `text.split()`.

#### `parse_flags(args) -> (set, list)`

Разделяет список аргументов на set флагов (аргументы, начинающиеся с `-`) и
позиционные аргументы.

#### `async check_tcp(host, port, family=socket.AF_UNSPEC, timeout=5.0) -> (ok, elapsed_ms, err)`

- Пытается `asyncio.open_connection(host, port, family=family)` с таймаутом.
- При успехе закрывает writer (с безопасным `wait_closed()`), возвращает
  `(True, elapsed_ms, None)`.
- При ошибке возвращает `(False, elapsed_ms, текст_ошибки)`, где текст:
  - `asyncio.TimeoutError` → `'timed out after <timeout*1000> ms'`;
  - `ConnectionRefusedError` → `'connection refused'`;
  - `socket.gaierror` → `'name resolution failed'`;
  - иначе `str(exc) or type(exc).__name__`.

---

## 6. Боты — общие соглашения

### 6.1 Формат списков

Списки ботов (note, reminder) используют общий формат строки:

```
<N>. | <метка-времени> | <заголовок>
```

- время — локальное по `TZ_OFFSET`, формат `%Y-%m-%d %H:%M`;
- символ `|` в заголовке заменяется на `/` (`title.replace('|', '/')`);
- при наличии поля срока добавляется хвост ` | до <время>` (note).

### 6.2 Время и часовой пояс

- В БД времена хранятся в UTC (Unix-timestamp).
- Для вывода локального времени боты используют `TZ_OFFSET`
  (`datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=TZ_OFFSET)))`).
- `note`/`reminder` имеют статический метод `_fmt(ts)` → строка `%Y-%m-%d %H:%M`.
- `reminder` дополнительно имеет `_local_tz()` → `timezone(timedelta(hours=TZ_OFFSET))`.

### 6.3 Автозаголовок

При создании заметки/напоминания из произвольного текста заголовок берётся из
первой строки текста, обрезанной до `TITLE_DEFAULT_LEN` символов и обрезанной
по пробелам справа (`first_line[:len].rstrip()`); если результат пуст — заголовок
`'(без заголовка)'`.

### 6.4 Изоляция данных

Все таблицы ботов содержат колонку `owner` = bare JID пользователя. Все выборки
фильтруются по `owner`. Один пользователь не видит данные другого.

### 6.5 Соглашение об ответах

Ответы на русском (текст выше). Справки и технические строки частично на
английском (наследие). Пустой ввод → `HELP`.

---

## 7. Бот isdown

Файл: `plugins/isdown.py`. Класс `IsDown`.

```python
NAME = 'isdown'
DESCRIPTION = ('Checks whether a site is down. Accepts a hostname, IP '
               'or URL (http://... / https://...).')
HELP = ('IsDown bot.\n'
        'Usage: <host[:port]|IP|URL>\n'
        'Default port: 443 (or 80/443 taken from the URL scheme).\n'
        'Examples:\n'
        '  google.com\n'
        '  8.8.8.8:80\n'
        '  http://google.com')
```

### Поведение

`handle(text, ctx)`:

1. Пустой ввод → `HELP`.
2. Разбор цели:
   - если содержит `://` — `urlsplit`; схема из `_SCHEME_PORTS = {'http': 80,
     'https': 443}`; если схема неизвестна → `"Unsupported URL scheme '<x>'."`;
     host = `parts.hostname`, port = `parts.port or <порт схемы>`.
   - если начинается с `[` — парсится `_BRACKET_RE = ^\[(.+?)\](?::(\d+))?$`;
     если не совпало → `HELP`; host = группа 1, port = группа 2 или 443.
   - иначе: `rpartition(':')`; если после последнего двоеточия цифры — host/port,
     иначе host=целиком, port=443.
3. `host = host.strip('[]')`; если пусто или `not valid_host(host)` →
   `'Invalid host.'`.
4. Если `not 1 <= port <= 65535` → `'Invalid port.'`.
5. `ok, elapsed, err = await check_tcp(host, port, timeout=5.0)`.
   - успех: `'%s:%d is UP (%d ms)' % (host, port, elapsed)`;
   - неуспех: `'%s:%d is DOWN (%s)' % (host, port, err)`.

---

## 8. Бот ping

Файл: `plugins/ping.py`. Класс `Ping`.

```python
NAME = 'ping'
DESCRIPTION = 'ICMP ping. Send me a hostname or IP address.'
HELP = ('Ping bot.\n'
        'Usage: <host|IP>\n'
        'Options:\n'
        '  -4    force IPv4\n'
        '  -6    force IPv6\n'
        'Example:\n'
        '  linuxoid.in')
```

### Поведение

1. `args = split_args(text)`; `flags, positional = parse_flags(args)`.
2. Если не ровно один позиционный аргумент или `not valid_host(...)` → `HELP`.
3. Строит команду `['ping', '-c', '4', '-w', '20']`; флаг `-6` добавляет `-6`,
   иначе `-4` (при наличии) добавляет `-4`; затем цель.
4. Запускает через `asyncio.create_subprocess_exec(...)`, stdout+stderr в один
   поток (`STDOUT`).
5. `await asyncio.wait_for(proc.communicate(), timeout=25)`; при таймауте —
   `proc.kill()`, `await proc.wait()`, ответ `'Ping timed out.'`.
6. Результат декодируется UTF-8 (errors='replace'), обрезается. Пустой →
   `'No output from ping.'`.

---

## 9. Бот port

Файл: `plugins/port.py`. Класс `Port`.

```python
NAME = 'port'
DESCRIPTION = 'TCP/UDP port availability checker.'
HELP = ('Port checker bot.\n'
        'Usage: [-6] [-u] <host|IP> <port>\n'
        'Options:\n'
        '  -6    check via IPv6\n'
        '  -u    use UDP instead of TCP\n'
        'Examples:\n'
        '  example.com 443\n'
        '  -6 example.com 25\n'
        '  -u 8.8.8.8 53')
```

### Поведение

1. `split_args`, `parse_flags`.
2. Если позиционных != 2 → `HELP`.
3. `valid_host(host)` иначе `'Invalid host.'`.
4. Порт: `int(positional[1])`, диапазон 1–65535, иначе (в т.ч. не-число)
   `'Invalid port (must be an integer 1-65535).'`.
5. `family = AF_INET6` если flag `-6`, иначе `AF_UNSPEC`.
6. Если flag `-u` → `_check_udp`.
7. TCP через `check_tcp(host, port, family=family, timeout=5.0)`:
   - ok → `'%s:%d/tcp is OPEN (%d ms)'`;
   - не ok → `'%s:%d/tcp is CLOSED/FILTERED (%s)'`.

### UDP (`_check_udp`)

- Создаёт datagram endpoint (`asyncio.DatagramProtocol`) до `(host, port)`.
  Ошибка создания → `'%s:%d/udp is CLOSED/FILTERED (<err>)'`.
- Шлёт пустой датаграмма (`transport.sendto(b'')`), ждёт 3 секунды результат:
  - получили ответ (`datagram_received`) → `'%s:%d/udp is OPEN (reply received from <addr> in <N> ms)'`;
  - получили ICMP-ошибку (`error_received`) → `'%s:%d/udp is CLOSED/FILTERED (<err>)'`;
  - таймаут → `'%s:%d/udp is OPEN (no reply and no ICMP error in <N> ms - assuming open)'`.
- В `finally` — `transport.close()`.

---

## 10. Бот sslcheck

Файл: `plugins/sslcheck.py`. Класс `SSLCheck`.

```python
NAME = 'sslcheck'
DESCRIPTION = 'Checks SSL certificate validity for a host or URL.'
HELP = ('SSLCheck bot.\n'
        'Usage: <host[:port]|URL>\n'
        'Checks the SSL certificate of the given address.\n'
        'Default port: 443.\n'
        'Examples:\n'
        '  google.com\n'
        '  example.com:8443\n'
        '  https://example.com')
```

### Поведение

`handle(text, ctx)`:

1. Пустой ввод → `HELP`.
2. `_parse_target(target)` → `(host, port)` либо строка ошибки. Правила:
   - содержит `://`: только схема `https` (иначе `'Only HTTPS URLs are supported.'`);
     host=`parts.hostname`, port=`parts.port or 443`;
   - начинается с `[`: `_BRACKET_RE = ^\[(.+?)\](?::(\d+))?$`; иначе
     `'Invalid bracket notation.'`; port по умолчанию 443;
   - иначе: `rpartition(':')` (host:port), иначе цель целиком + порт 443;
   - `host = host.strip('[]')`; пусто или `not valid_host(host)` → `'Invalid host.'`;
   - порт вне 1–65535 → `'Invalid port.'`.
3. Выполняет с таймаутом 15 c (`asyncio.wait_for(self._get_cert(host, port), timeout=15)`).
   Обработка исключений:
   - `asyncio.TimeoutError` → `'Connection to %s:%d timed out.'`;
   - `ssl.SSLError` → `'SSL error for %s:%d: <err>'`;
   - `OSError` → `'Connection to %s:%d failed: <err>'`.
4. `_format(host, port, cert_data, elapsed, trusted, ssl_info)` формирует ответ.

### Получение сертификата (`_get_cert`)

Двухстадийная процедура:

1. Пробуем контекст `ssl.PROTOCOL_TLS_CLIENT`, `load_default_certs()`,
   `check_hostname=False`, `verify_mode=CERT_REQUIRED`. Успех → `trusted=True`.
2. При `(ssl.SSLCertVerificationError, ssl.SSLError)` — повторяем подключение с
   `check_hostname=False, verify_mode=CERT_NONE`, `trusted=False`; сертификат
   раскодируем вручную:
   - `getpeercert(binary_form=True)` → DER → PEM (`ssl.DER_cert_to_PEM_cert`),
     запись во временный `.pem` файл, чтение через
     `ssl._ssl._test_decode_cert(path)` (`_decode_cert`), файл удаляется.
3. Вызывается `_extract_ssl_info(sslsock, loop)` (для обоих веток):
   - `protocol = sslsock.version()`;
   - `cipher_name, _, cipher_bits = sslsock.cipher()`;
   - `available`: список имён шифров из `sslsock.shared_ciphers()` (уникальные,
     отсортированные по убыванию), вызов с таймаутом 2 c через
     `loop.run_in_executor` (иначе пропуск);
   - возвращает dict или `None`.
4. `_probe_versions(host, port)` — если 'available' всё ещё нет: для каждой
   версии из `['TLSv1', 'TLSv1_1', 'TLSv1_2', 'TLSv1_3']` пробует подключиться с
   `ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.<ver>` (таймаут 1.5 c),
   собирает успешные версии (отсортированный список). Пробуются параллельно
   через `asyncio.gather`.

### Форматирование ответа (`_format`)

`icon = '✓'`, если сертификат совпал с хостом и нет предупреждений, иначе `'⚠'`.

Строки ответа:
```
SSL <host>:<port> <icon>
Host: <host>
Valid: <'yes'|'NO'>
Expires: <YYYY-MM-DD> (<human>)
Issuer: <org-or-CN|unknown>
[Protocol: <ver> [✓ (available: TLSv1.3, TLSv1.2, ...)]]
[Cipher: <name> [(<bits> bit)]]
[Handshake: <N> ms]
[WARNING: ...]
```

Существенно:
- `notAfter` парсится как `'%b %d %H:%M:%S %Y %Z'` в `timezone.utc`;
  `days_left` = разница в днях с `datetime.now(timezone.utc)`.
- Проверка хоста `_check_hostname(cert, host)`: сначала SAN (DNS-имена),
  затем CN из subject; поддержка wildcard `*.domain` (для поддомена одного
  уровня — `prefix` без точек).
- Предупреждения (`warnings`):
  - не совпал хост:
    `'Certificate does NOT match host (expected <host>, got <CN|SANs|unknown>)'`;
  - `days_left < 0` → `'Certificate has EXPIRED!'`;
  - иначе `days_left < 30` (`WARN_DAYS`) →
    `'Certificate expires in less than 30 days!'`;
  - self-signed (`not trusted` и CN==issuer CN) →
    `'Certificate is SELF-SIGNED (not trusted).'`;
  - иначе при `not trusted` →
    `'Certificate is NOT trusted (chain validation failed).'`.
- `_format_days(days)`: `'unknown'` / `'expired <N> day(s) ago'` /
  `'expires today'` / `'1 day remaining'` / `'<N> days remaining'`.

---

## 11. Бот shorty

Файл: `plugins/shorty.py`. Классы: `Uto`, `Clck`, `Isgd` (движки) и `Shorty`.

```python
NAME = 'shorty'
DESCRIPTION = ('URL shortener. Send a link to shorten it or send '
               '"conf" to choose a shortening engine.')
HELP = ('Shorty bot - link shortener.\n'
        'Commands:\n'
        '  <link>      shorten the link\n'
        '  conf        list available engines and select one\n'
        'Default engine: u.to.\n'
        'Example:\n'
        '  https://example.com/very/long/link')
```

### Движки

Общий HTTP-примитив `_http(url, data, headers, method, timeout=15)`: запрос через
`urllib.request` с `User-Agent` = строкой браузера + `JabberToolbox`, возвращает
текст UTF-8 (errors='replace'). Все обращения в ботах — через `asyncio.to_thread`.

| Движок | Запрос |
|--------|--------|
| `u.to` | POST `https://u.to/api/shorten/` JSON `{'url': url}` с заголовками Content-Type/Accept/Referer/Origin; ответ JSON; `doc['success'] and doc['shortUrl']` → вернуть shortUrl, иначе `RuntimeError(doc['message'] or 'service returned an error')`. |
| `clck.ru` | GET `https://clck.ru/--?url=<quote(url, safe='')>`; если ответ начинается с `http` → это короткая ссылка, иначе `RuntimeError(raw[:200])`. |
| `is.gd` | GET `https://is.gd/create.php?format=simple&url=<quote(url, safe='')>`; тот же принцип. |

Реестр: `ENGINES = {'u.to': Uto, 'clck.ru': Clck, 'is.gd': Isgd}`,
`DEFAULT_ENGINE = 'u.to'`.

### Хранилище

Файл `shorty.sqlite3`, таблица:

```sql
CREATE TABLE IF NOT EXISTS settings (
    jid TEXT PRIMARY KEY,
    engine TEXT NOT NULL);
```

`get_engine(jid)` → сохранённый движок (если он в `ENGINES`) или `DEFAULT_ENGINE`.

### Поведение

1. `lowered = body.lower()`.
2. Команда `conf`:
   - помечает JID в `self.pending` (set);
   - выводит:
     ```
     Available engines:
     1. u.to
     2. clck.ru
     3. is.gd [<- current]
     Reply with a number to select an engine.
     ```
     (метка `<- current` у текущего движка);
   - следующий ввод от этого JID, если число из диапазона → движок сохранён
     (`INSERT OR REPLACE INTO settings`), ответ `'Engine set to <label>.'`;
     если число вне диапазона →
     `"No such engine number. Send 'conf' to see the list."`;
     иначе обрабатывается как обычная ссылка (fallthrough).
3. Определение URL:
   - `_URL_RE = ^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$` — прямой URL;
   - иначе `_DOMAIN_RE = ^[A-Za-z0-9][A-Za-z0-9.\-_]+(:\d+)?(/\S*)?$` —
     домен → префикс `https://`;
   - иначе → `HELP`.
4. `short = await engine.shorten(url)`; исключение →
   `'Shortening failed (<label>): <err>'`.
5. Успех → строка короткой ссылки.

---

## 12. Бот whois

Файл: `plugins/whois.py`. Класс `Whois`. Вспомогательные константы:

- `CACHE_TTL = 24 * 3600` (24 часа);
- `IANA_SERVER = 'whois.iana.org'`, `ARIN_SERVER = 'whois.arin.net'`;
- `_IPV4_RE = ^\d{1,3}(\.\d{1,3}){3}$`.

```python
NAME = 'whois'
DESCRIPTION = 'WHOIS lookup for domains and IP addresses.'
HELP = ('Whois bot.\n'
        'Usage: <domain|IP>\n'
        'Results are cached for 24 hours.\n'
        'Example:\n'
        '  linuxoid.in')
```

### Хранилище

Файл `whois_cache.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS cache (
    qkey TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    result TEXT NOT NULL);
```

### Поведение (`handle`)

1. Пустой ввод или `not valid_host(target)` → `HELP`.
2. `key = target.lower().rstrip('.')`; `now = int(time.time())`.
3. Кэш: `SELECT ts, result FROM cache WHERE qkey='v4:'+key`; если свежий
   (`now - ts < CACHE_TTL`) →
   `'<result>\n[cached <N> hour(s) ago]'` (`N = max(1, hours)`).
4. `_lookup(target)` с `asyncio.wait_for(..., timeout=30)`; ошибка →
   `'Whois lookup failed: <err>'`.
5. `_clean(result).strip()`; пусто → `'Empty whois response.'`.
6. Кэширование `INSERT OR REPLACE INTO cache VALUES ('v4:'+key, now, result)`.
7. Возврат `result`.

### `_lookup(target)` — маршрут по WHOIS-серверам

- IPv4 (по regex) или содержит `:` → начальный сервер `ARIN_SERVER:43`;
- иначе → `IANA_SERVER:43`.
- `_query(server, port, target)`: TCP-соединение, запись `'<query>\r\n'`,
  чтение по 8192 байта до EOF, декод UTF-8 (errors='replace'); таймаут на
  закрытие writer (`wait_closed` безопасно).
- До 2 реферальных переходов (`hops < 2`):
  - `_referral(text)`: ищет по `_REFERRAL_RE`
    (`^(?:registrar whois server|whois server|referralserver|whois)\s*:\s*(\S+)`,
    с флагами `re.IGNORECASE | re.MULTILINE`);
  - очищает префиксы `rwhois://`, `whois://`, `http://`, `https://`;
  - разбирает host[:port] (port по умолчанию 43);
  - если реферальный сервер совпал с текущим — стоп;
  - запрашивает данные на новом сервере; если результат непустой — берёт его.

### Очистка результатов (`_clean` → `_strip_disclaimers` + `_filter_placeholders`)

`_strip_disclaimers(text)`:
1. удаляет строки-концовки `>>> ... <<<` (`_TRAILER_RE`);
2. срезает «хвост дисклеймера»: если есть строка, начинающаяся с паттернов
   `_DISCLAIMER_TAIL_RE` (terms of use / disclaimer / by submitting / the data ...
   whois / access to ... whois / registration data / whois output ... / the arin ...),
   и в этом хвосте количество строк-полей (`_FIELD_RE`) не больше
   `max(1, nonempty // 5)` — хвост отбрасывается;
3. если «шапка» (до первого пустого абзаца) не содержит полей и содержит
   дисклеймер-предложение (`_DISCLAIMER_SENTENCE_RE`) — шапка отбрасывается;
4. многократно: если «хвост» после последнего пустого абзаца не содержит полей
   и содержит юр. прозу (`_LEGAL_PROSE_RE` — agree, lawful purposes, all rights
   reserved, terms of use, disclaimer, icann, web-based whois, legitimate
   interest, spam, abuse) — отбрасывается;
5. срезает пустые и `#`/`%`-строки по краям.

`_filter_placeholders(text)`: удаляет строки, у которых значение поля
(`value` после `:`) равно одному из `{redacted for privacy, redacted, n/a}`
(с точкой/запятой/точкой с запятой на конце — рвущей).

### Регулярные выражения (служебные)

- `_FIELD_RE = ^\s*[A-Za-z][A-Za-z0-9 _.-]{0,40}:\s*(?!/)\S` — строка-поле;
- `_TRAILER_RE = ^\s*>>>.*<<<\s*$`.

---

## 13. Бот whoip

Файл: `plugins/whoip.py`. Класс `Whoip`. Использует класс `Whois` и его
константы из `plugins.whois`.

```python
NAME = 'whoip'
DESCRIPTION = 'Resolves a domain to IP, then WHOIS lookup on that IP.'
HELP = ('Whoip bot.\n'
        'Usage: <domain|IP>\n'
        'Domains are resolved to IP first, then WHOIS is performed.\n'
        'Results are cached for 24 hours.\n'
        'Example:\n'
        '  google.com')
```

### Поведение

1. `valid_host(target)` иначе `HELP`.
2. `key = target.lower().rstrip('.')`; кэш (`qkey='v4:'+key`) — как в whois
   (кэш хранит итоговый вывод, с пометкой `[cached ...]`).
3. `is_ip = _IPV4_RE.match(target) or ':' in target`.
   - если IP — `ip = target`;
   - иначе `ip = await self._resolve(target)`:
     `loop.getaddrinfo(host, None, family=AF_UNSPEC)`; берёт первый `AF_INET`
     (или первые в списке); пусто → `'Could not resolve <target> to an IP address.'`.
4. `await asyncio.wait_for(self._whois._lookup(ip), timeout=30)`; ошибка →
   `'Whois lookup failed: <err>'`.
5. Результат очищается (`Whois._clean(...).strip()`) и
   `_filter_comments(result)` — убираются строки, начинающиеся с `#`.
6. Пусто → `'Empty whois response.'`.
7. Вывод:
   - для IP — просто результат;
   - для домена — `'<target> → <ip>\n<result>'`.
8. Кэширование в том же файле и возврат.

---

## 14. Бот note

Файл: `plugins/note.py`. Класс `Note`. Константа `TITLE_MAX = 100`.

```python
NAME = 'note'
DESCRIPTION = ('Personal notes. Send any text to save it as a note, '
               '"list" to see your notes.')
HELP = ('Note bot - личные заметки.\n'
        'Отправьте любой текст - он будет сохранён как ваша заметка.\n'
        'Команды:\n'
        '  list      список заметок (новые сверху)\n'
        '  srch <текст>  поиск по заголовкам и содержимому\n'
        '  <номер>   открыть заметку\n'
        '  в открытой заметке: 1|title, 2|add, 3|del, 4|old, 0 - выход\n'
        '  help / ?  эта справка\n'
        'Срок хранения задаётся через old; по умолчанию заметка хранится '
        'бессрочно.')
```

### Хранилище

Файл `note.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    expires_at INTEGER,
    title TEXT NOT NULL,
    body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_notes_owner ON notes (owner, created_ts);
```

Соединение: `isolation_level=None` (autocommit).

### Варианты срока хранения

`EXPIRY_OPTIONS` (метрка, секунды; `None` = бессрочно):

```python
(('1 час', 3600), ('6 часов', 6*3600), ('12 часов', 12*3600),
 ('1 день', 24*3600), ('3 дня', 3*24*3600), ('1 неделя', 7*24*3600),
 ('2 недели', 14*24*3600), ('1 месяц', 30*24*3600),
 ('3 месяца', 90*24*3600), ('6 месяцев', 180*24*3600),
 ('1 год', 365*24*3600), ('Никогда', None))
```

### Диспетчер `handle(text, ctx)`

1. `body = text.strip()`; пусто → `HELP`.
2. Если есть `st = self.state.get(jid)`:
   - слова отмены `CANCEL = ('0', 'отмена', 'cancel')` (кроме режима `'del'`):
     состояние удаляется; если режим `'menu'` → `_list(jid)`, иначе `'Отменено.'`;
   - иначе вызывается `_mode_<mode>`.
3. Idle-команды (по `body.lower()`):
   - `help` / `?` → `HELP`;
   - `list` → `_list(jid)`;
   - `srch` (ровно) → `'Что искать?'`;
   - начинается с `srch ` → `_search(jid, body[5:].strip())`;
   - `body.isdigit()` → `_open_by_number(jid, int(body))`;
   - иначе → `_create(jid, body)`.

### Данные и внутренние методы

- `_purge(jid)`: `DELETE FROM notes WHERE owner=? AND expires_at IS NOT NULL AND expires_at < now`.
- `_rows(jid)`: `SELECT id, title, created_ts, expires_at FROM notes WHERE owner=? ORDER BY created_ts DESC, id DESC`.
- `_load(jid, nid)`: `(title, body, created_ts, expires_at)` или `None`.
- `_fmt(ts)`: локальное время `%Y-%m-%d %H:%M`.

### `_create(jid, body)`

- Автозаголовок из первой строки (см. 6.3).
- Запись `INSERT ... (owner, created_ts, expires_at, title, body)
  VALUES (?, ?, NULL, ?, ?)`.
- `pos = SELECT COUNT(*) FROM notes WHERE owner=?` (после вставки — номер новой).
- Ответ:
  ```
  Заметка #<pos> сохранена.
  Заголовок: <title>
  Отправьте list для списка или номер, чтобы открыть.
  ```

### `_list(jid)`

- `_purge`, `_rows`.
- Пусто →
  `'Заметок нет. Отправьте мне любой текст, чтобы сохранить заметку.'`.
- Заголовок `'Заметки (новые сверху):'`.
- **Стабильная нумерация**: `stable_num = len(rows) - pos + 1` (самая новая —
  наибольший номер; при добавлении новых заметок «старые» номера не сдвигаются).
- Строка: `<num>. | <created> | <title>` и при сроке ` | до <expires>`.
- Хвост: `'Отправьте номер, чтобы открыть заметку.'`.

### `_search(jid, query)`

- `_purge`, `_rows`. `q = query.lower()`.
- Для каждой записи: `_load`; совпадение, если `q in title.lower() or
  q in body.lower()` (поиск **регистронезависимый, включая кириллицу**,
  реализован на стороне Python — SQLite `LOWER()`/`LIKE` для кириллицы не
  сворачивают регистр).
- Нет совпадений → `'Ничего не найдено.'`.
- Заголовок `'Заметки с «<query>»:'`; строки в том же формате, что и `_list`,
  с теми же стабильными номерами (можно открывать по номеру).
- Хвост: `'Отправьте номер, чтобы открыть заметку.'`.

### `_open_by_number(jid, num)`

- `_purge`, `_rows`. Вне диапазона:
  - без заметок → `'У вас пока нет заметок.'`;
  - иначе → `'Нет заметки с номером <num> (доступно 1-<N>).'`.
- `nid = rows[len(rows) - num][0]` (трансляция стабильного номера в позицию).
- `_view(jid, nid)`.

### `_view(jid, nid)` и меню

`_view`:
- `None` → снять state, `'Заметка не найдена.'`.
- Устанавливает `self.state[jid] = {'mode': 'menu', 'id': nid}`.
- Ответ:
  ```
  Заметка #<id> «<title>»
  <создана: YYYY-MM-DD HH:MM[, хранится до: ...]>
  ------------------------------
  <body>
  ------------------------------
  Операции:
  1. title - задать заголовок заметки
  2. add - добавить текст к заметке
  3. del - удалить заметку
  4. old - установить срок хранения
  0. Выход
  ```
  (поле `meta` — `created`, при сроке — ещё и `хранится до`; разделитель —
  `'-' * 30`).

`MENU_KEYS`:
```python
{'1':'title','title':'title','2':'add','add':'add','3':'del','del':'del',
 '4':'old','old':'old','0':'exit','exit':'exit','выход':'exit','quit':'exit'}
```

`_mode_menu`:
- `title` → `self.state = {'mode':'title','id':nid}`, `'Введите заголовок:'`;
- `add` → `{'mode':'add'}`, `'Введите текст, который нужно добавить к заметке:'`;
- `del` → `{'mode':'del'}`, ответ `'Удалить заметку «<title>»?\n1. Да\n0. Выход'`;
- `old` → `{'mode':'old'}`, список `'Выберите срок хранения:'` + пункты 1..12;
- `exit` → `del state`, `_list(jid)`;
- иначе → `'Не понял. Доступно: 1|title, 2|add, 3|del, 4|old, 0 - выход.'`.

### Под-режимы

- `_mode_title`: запрет переносов строк →
  `'В заголовке не должно быть переносов строк. Введите заголовок:'`;
  пусто → `'Заголовок не может быть пустым. Введите заголовок:'`;
  `' '.join(body.split())`, если длиннее 100 — `title[:100]`;
  `UPDATE notes SET title=? WHERE id=? AND owner=?`; rowcount=0 → снять state,
  `'Заметка не найдена.'`; иначе `_view`.
- `_mode_add`: `merged = old_body.rstrip('\n') + '\n' + body`;
  `UPDATE notes SET body=? ...`; `_view`.
- `_mode_del`: `DEL_YES = ('да','yes','y','1')` → удаление, снять state,
  `'Заметка удалена.'` (rowcount=0 → `'Заметка не найдена.'`);
  `DEL_NO = ('0','нет','no','n','выход','exit')` → `_view`;
  иначе → `'Ответьте 1 (Да) или 0 (Выход): удалить заметку?'`.
- `_mode_old`: не число или вне 1..12 →
  `'Выберите номер варианта от 1 до <N>.'`;
  `expires_at = int(time.time()) + delta` (для `Никогда` — `None`);
  `UPDATE notes SET expires_at=? ...`; `_view`.

---

## 15. Бот reminder

Файл: `plugins/reminder.py`. Класс `Reminder`. Константы:

- `TITLE_MAX = 100` (для заголовка, см. дополнительно `TITLE_DEFAULT_LEN`);
- `RELATIVE_UNITS`:
  ```python
  (('минута', 60), ('час', 3600), ('день', 86400), ('неделя', 604800),
   ('месяц', 2592000), ('год', 31536000))
  ```

```python
NAME = 'reminder'
DESCRIPTION = ('Timed reminders. Send any text to save it as a '
               'reminder, "list" to see your reminders, '
               '"arch" for delivered ones.')
HELP = ('Reminder bot - напоминания.\n'
        'Отправьте текст - он будет сохранён как напоминание.\n'
        'Команды:\n'
        '  list      активные напоминания (ближайшие первые)\n'
        '  arch   доставленные напоминания\n'
        '  srch <текст>  поиск по заголовкам и тексту\n'
        '  <номер>   открыть напоминание\n'
        '  help / ?  эта справка\n'
        'По умолчанию напоминание через 1 час, хранится 7 дней.')
```

### Хранилище

Файл `reminder.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    remind_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders (owner, remind_at);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (remind_at, expires_at);
-- появляется при первом запуске бота:
ALTER TABLE reminders ADD COLUMN delivered_at INTEGER;   -- в try/except OperationalError
```

Соединение: `isolation_level=None`.

Дополнительные атрибуты экземпляря:
- `self.state = {}` — конечный автомат диалога;
- `self.last_view = {}` — последний показанный список (`'list'`/`'arch'`/`'srch'`);
- `self.last_search = {}` — список id результата последнего поиска.

### Внутренние запросы

- `_rows(jid)` — активные:
  ```sql
  SELECT id, title, created_ts, remind_at, expires_at FROM reminders
  WHERE owner=? AND delivered_at IS NULL
  ORDER BY remind_at ASC, id ASC
  ```
- `_archive_rows(jid)` — доставленные:
  ```sql
  SELECT id, title, created_ts, remind_at, expires_at, delivered_at FROM reminders
  WHERE owner=? AND delivered_at IS NOT NULL
  ORDER BY delivered_at DESC, id ASC
  ```
- `_purge(jid)`: `DELETE FROM reminders WHERE owner=? AND expires_at < now`.
- `_load(jid, nid)` → `(title, body, created_ts, remind_at, expires_at, delivered_at)`.

### Диспетчер `handle`

1. пусто → `HELP`.
2. Сначала idle-команды (снимают состояние):
   - `help` / `?` → `HELP`;
   - `list` → `state.pop` + `_list`;
   - `arch` **или** `archive` → `state.pop` + `_archive`;
   - `srch` (ровно) → `state.pop` + `'Что искать?'`;
   - `srch <текст>` → `state.pop` + `_search`.
3. Если есть состояние:
   - слова отмены `('0', 'отмена', 'cancel', 'выход', 'exit')` → `_handle_cancel`;
   - `назад`/`back` **в** режимах `relative`, `relative_value`, `relative_unit` →
     `_restore_picker`;
   - иначе → `_mode_<mode>`.
4. Idle-число → `_open_by_number`.
5. Иначе → `_create`.

### `_handle_cancel` (важно, включает создание)

- режим `'menu'`:
  - если `creating` → `DELETE FROM reminders WHERE id=? AND owner=?` (удаление
    несохранённого);
  - `del state`; `_list(jid)`.
- режимы `relative*` → `_restore_picker`.
- любой другой режим (пикер, ввод поля):
  - если `creating` → удаление + `del state` + ответ `'Напоминание удалено.'`;
  - иначе → `_view(jid, st['id'])`.

### `_restore_picker`

Восстанавливает state в `'picker'` с сохранением `creating`, `target`,
year/month/day/hour/minute и перерисовывает пикер.

### `_list` / `_archive`

- `_list`: `last_view='list'`; пусто →
  `'Напоминаний нет. Отправьте мне любой текст, чтобы создать напоминание.'`;
  заголовок `'Активные напоминания (ближайшие первые):'`; номер = позиция (1..N,
  в порядке сортировки активных); время — `remind_at`;
  хвост `'Отправьте номер, чтобы открыть напоминание.'`.
- `_archive`: `last_view='arch'`; пусто →
  `'Доставленных напоминаний нет.'`;
  заголовок `'Доставленные напоминания (новые первые):'`; время — `delivered_at`.

### `_search(jid, query)` — поиск по активным и доставленным

- `q = query.lower()`; `_purge`.
- Активные: фильтр `q in title.lower() or q in body.lower()`; строки
  `<n>. | <remind_at> | <title>`.
- Доставленные: тот же фильтр по строкам `_archive_rows`; строки с той же
  нумерацией по `delivered_at`.
- **Нумерация сквозная**: активные идут первыми (нумерация 1..A), затем
  доставленные (A+1..).
- Нет совпадений → `'Ничего не найдено.'`.
- Заголовок `'Найдено по «<query>»:'`; секции `'Активные:'` и
  `'Доставленные:'` (непустые выводятся); хвост как обычно.
- `last_view='srch'`, `last_search = active_ids + delivered_ids`.

### `_open_by_number`

- `view = last_view.get(jid, 'list')`.
- `'srch'`: `rows = last_search[jid]` (список id); вне диапазона →
  пусто `'Ничего не найдено.'`, иначе
  `'Нет совпадения с номером <n> (доступно 1-<N>).'`;
  `nid = rows[num-1]`; `_view`.
- `'arch'`: строки `_archive_rows`, иначе `_rows`. Вне диапазона:
  - пусто → (arch) `'Доставленных напоминаний нет.'` / (list) `'У вас нет напоминаний.'`;
  - иначе `'Нет напоминания с номером <n> (доступно 1-<N>).'`.
  `nid = rows[num-1][0]`; `_view`.

### Per-user нумерация (`_user_number(jid, nid) → int`)

Возвращает «пользовательский» номер напоминания (для заголовка «Напоминание
#<N>»):
- **активные**: считает записи с `delivered_at IS NULL` с более ранним
  `remind_at` (или равным и меньшим id) + 1;
- **доставленные**: считает записи с `delivered_at IS NOT NULL` с более новым
  `delivered_at` (или равным и меньшим id) + 1;
- нет записи → 0.

### `_create(jid, body)` — создание напоминания

- Автозаголовок (см. 6.3).
- `remind_at = now + 3600`; `expires_at = remind_at + 7 * 86400`.
- SQL insert (six columns).
- state = `{'mode': 'picker', 'id': nid, 'creating': True, 'target':
  'remind_at', 'year': ..., 'month': ..., 'day': ..., 'hour': ...,
  'minute': ...}` — из `datetime.fromtimestamp(remind_at, tz=local)`.
- Ответ — пикер (см. ниже).

### Пикер даты/времени

`_fmt_picker(nid, target, year, month, day, hour, minute, creating=False)`:

```
Настройка: время напоминания        # (или «срок хранения»)
Текущее время: <now>
Когда напомнить: YYYY-MM-DD HH:MM   # (или «Срок хранения: ...»)
------------------------------
1. Год: <year>
2. Месяц: <month>
3. День: <day>
4. Час: <hour>
5. Минута: <minute>

6. Относительное время

7. Сохранить
0. Удалить                         # если creating=True; иначе «0. Выход»
```

Правила:
- `_enter_picker(jid, st, target)` — вход из меню (пункты `4`/`5`): берёт
  текущее значение `remind_at`/`expires_at`, переносит в локальный пояс,
  ставит `mode='picker'`, сохраняет `creating` из текущего state.
- `_mode_picker`:
  - `1`→`picker_value` (field year), приглашение `'Введите год (2024-2099):'`;
  - `2`→month `'Введите месяц (1-12):'`;
  - `3`→day `'Введите день (1-31):'`;
  - `4`→hour `'Введите час (0-23):'`;
  - `5`→minute `'Введите минуту (0-59):'`;
  - `6`→открыть подменю относительного времени;
  - `7`→`_picker_save`;
  - иначе `'Введите номер пункта от 1 до 7 (0 - выход).'`.
- `_mode_picker_value`:
  - не число или вне лимитов (`year` 2024–2099, `month` 1–12, `day` 1–31,
    `hour` 0–23, `minute` 0–59) → повтор приглашения;
  - иначе возвращает в picker с обновлённым полем и перерисовкой.
- `_picker_save`:
  - `ts = _ts_from_picker(st)`: `datetime(y, m, min(day,
    calendar.monthrange(y, m)[1]), hour, minute, tzinfo=local).timestamp()`;
  - `UPDATE reminders SET <field>=? WHERE id=? AND owner=?`;
  - rowcount=0 → `'Напоминание не найдено.'`;
  - если поле `remind_at`:
    1. `_fix_expires` — если `expires_at <= new_remind_at`, то
       `expires_at = new_remind_at + 7*86400`;
    2. `UPDATE reminders SET delivered_at=NULL ...` — **re-arm**;
  - если `creating` → сохранить `creating: False` в state (пометить сохранённым);
  - `_view`.

### Относительное время

`_fmt_relative`:
```
Относительное время:
1. Значение: <value>
2. Единица: <label>

3. Сохранить
0. Назад
```

- `_mode_relative`: `1`→`relative_value` `'Введите значение (1-1000):'`;
  `2`→`relative_unit`, список единиц (`1. минута ... 6. год`);
  `3`→`_relative_save`; иначе перерисовка.
- `_mode_relative_value`: число 1–1000 иначе повторить приглашение.
- `_mode_relative_unit`: номер 1..6 иначе `'Выберите номер единицы от 1 до 6.'`.
- `_relative_save`: `new_ts = now + val * seconds_per` для текущего поля;
  `UPDATE`; при `remind_at` — `_fix_expires` + `delivered_at=NULL`;
  при `creating` — пометить `creating: False`; `_view`.

### Меню просмотра (`_view`)

`_view(jid, nid)`:
- `_load` `None` → `'Напоминание не найдено.'`;
- state = `{'mode':'menu','id':nid,'creating':<сохр. из прежнего state>}`;
- если `delivered_at` — добавляется `'\nСтатус: доставлено <time>'`;
- ответ:
  ```
  Напоминание #<user_number> «<title>»
  Создано: <created>
  Напомнить: <remind_at>
  Хранится до: <expires_at>
  [Статус: доставлено <time>]
  ------------------------------
  <body>
  ------------------------------
  Операции:
  1. title - задать заголовок
  2. body - редактировать текст
  3. del - удалить
  4. remind - изменить время напоминания
  5. old - изменить срок хранения

  0. Выход
  ```

`MENU_KEYS`:
```python
{'1':'title','title':'title','2':'body','body':'body','3':'del','del':'del',
 '4':'remind','remind':'remind','5':'old','old':'old',
 '0':'cancel','cancel':'cancel','выход':'exit'}
```

`_mode_menu`: `title`→режим `title` `'Введите заголовок:'`; `body`→режим `body`
`'Введите текст напоминания:'`; `del`→режим `del`
`'Удалить напоминание «<title>»?\n1. Да\n0. Выход'`; `remind`→`_enter_picker
(...,'remind_at')`; `old`→`_enter_picker(...,'expires_at')`; иначе →
`'Не понял. Доступно: 1|title, 2|body, 3|del, 4|remind, 5|old, 0 - выход.'`.

### Под-режимы

- `_mode_title` — как в note, лимит `TITLE_MAX=100`, ошибки те же,
  `'Введите заголовок:'`.
- `_mode_body` — `UPDATE reminders SET body=?`; `_view`.
- `_mode_del` — `DEL_YES=('да','yes','y','1')` → удаление, `'Напоминание удалено.'`;
  `DEL_NO=('0','нет','no','n','выход','exit')` → `_view`;
  иначе `'Ответьте 1 (Да) или 0 (Выход): удалить напоминание?'`.

### Фоновая доставка (`check_reminders`)

```python
SELECT id, owner, title, body FROM reminders
WHERE remind_at <= now AND expires_at > now AND delivered_at IS NULL
```

- для каждой записи: `(owner, 'Напоминание: <title>\n<body>')`;
- `UPDATE reminders SET delivered_at=now WHERE id IN (...)` — батч;
- возвращает список `(owner_ bare, text)`.

Транспорт вызывает это каждые 60 c и отправляет сообщения адресатам.

---

## 16. Фоновые задачи

| Задача | Период | Метод | Действие |
|--------|--------|-------|----------|
| `_check_reminders_loop` | 60 c | `bot.check_reminders()` | Отправка напоминаний (reminder), см. 4.11. |

Контракт: любой бот, реализующий `check_reminders(self) -> list[(owner, text)]`,
автоматически получает фоновую доставку. Исключения не роняют цикл.

---

## 17. Конфигурация

`config.ini` / `config.ini.example`:

```ini
[server]
JID = service2.example.com      ; домен компонента
Host = 127.0.0.1                ; адрес XMPP-сервера
Port = 5275                     ; порт компонентного соединения
Password = changeme             ; компонентный пароль

[settings]
; Смещение часового пояса от UTC (например 3 для Москвы, -5 для Нью-Йорка).
Timezone = 0
; Длина первой строки, используемой для автозаголовка (по умолчанию 20).
TitleDefaultLength = 20
```

`[settings]` необязателен. Настройки применяются при старте `main()`.

---

## 18. Тестирование

`selftest.py` — автономный прогон без сети и без сервера. Структура:

- `check(name, cond, extra='')` — печатает `[ok]`/`[FAIL] <name> <extra>`,
  копит имена в глобальный `FAILED`.
- `main()` (async):
  - загрузка плагинов и проверка списка имён
    `['isdown','note','ping','port','reminder','shorty','sslcheck','whoip','whois']`;
  - создание `Toolbox(...)` (без реального подключения) и проверка:
    - зарегистрированных ботов;
    - disco#items корня (все JID ботов);
    - identity/vcard => `_get_vcard`;
    - ad-hoc команд на узлы `bot.NAME`;
    - времени: `_handle_time` через поддельный IQ (утверждается `utc`/
      `tzo`/`display`);
    - регистрации XEP-0077;
    - presence-отражения и обработки probe;
    - маршрутизация сообщений, receipts, chat-state;
  - сетевые боты проверяются без реальных сетевых вызовов (случаи невалидного
    ввода, HELP, парсинг), там где нужно — подменой данных в БД;
  - note/reminder — полные диалоги (создание, меню, поиск, пикеры, архив,
    re-arm, таймзона);
  - `_fake_iq(iqtype, ns, to=None)` — строит слэк IQ для проверки
    version/uptime/vCard.
- В конце: если `FAILED` — вывод списка и `sys.exit(1)`, иначе
  `'All checks passed (slixmpp <ver>).'`.

Проверки изолируют данные: перед диалогом — `DELETE FROM ... WHERE owner=?`,
`state.clear()`.

---

## 19. Глоссарий

| Термин | Значение |
|--------|----------|
| Компонент (XMPP component) | Подключение к серверу со своим доменом и паролем; функционирует как «под-сервер». |
| Transport | Термин для такого компонента, «агрегирующего» сервисы. |
| Бот / сервис | JID-сущность `NAME@домен`, являющаяся экземпляром плагина. |
| Bare JID | JID без ресурса (`user@domain`). Ключ изоляции данных. |
| payload | Тело сообщения (`<body>`). |
| stable_num | Стабильный номер заметки (note), не сдвигающийся при добавлении новых. |
| user_number | Пользовательский номер напоминания (reminder), «позиция» среди группа активных/доставленных. |
| re-arm | Повторная активация доставленного напоминания через новое `remind_at` (сброс `delivered_at=NULL`). |
| TZ_OFFSET | Смещение часового пояса от UTC (часы). |
| TITLE_DEFAULT_LEN | Длина автозаголовка из первой строки текста. |

---

## 20. Известные согласования поведения (важные детали)

1. **`srch` регистронезависим и для кириллицы** — реализован сравнением в Python
   (`lower()`), так как SQLite не сворачивает регистр кириллицы.
2. **`arch` и `archive` принимаются** — исторически команда называлась `archive`,
   в коде сохранён и короткий синоним `arch`.
3. **Создание напоминания «отменяется» только удалением** — в создающем пикере
   пункт `0` называется «Удалить» и удаляет запись (без создания меню).
   Редактирование существующего — пункт `0. Выход` просто возвращает в меню.
4. **Меню созданного напоминания не может остаться в состоянии `creating`**:
   после `7. Сохранить`/`3. Сохранить` (relative) флаг `creating` обнуляется,
   поэтому выход из меню не удаляет сохранённое напоминание.
5. **`check_reminders` не трогает `state`/`last_view`** — диалоги пользователей
   не прерываются фоновой доставкой.
6. **Срок хранения (expires_at)**: если пользователь ставит `remind_at` позже
   текущего `expires_at`, последний автоматически сдвигается на +7 дней
   (`_fix_expires`).
7. **UDP-проверка port** — «открыто» трактуется и при отсутствии ответа, но без
   ICMP-ошибки.

---

*Конец спецификации. При любых расхождениях между документом и кодом источником
истины является код.*