"""Plugin system for the Jabber Toolbox transport.

Every plugin module placed into this directory (except files starting
with "_") may define one or more subclasses of :class:`Bot`.  Each of
them is instantiated by the loader and turned into a separate bot with
a JID of ``NAME@<transport-domain>``.
"""

import asyncio
import importlib.util
import logging
import os
import re
import socket
import sys

log = logging.getLogger(__name__)

# Directory that hosts the transport itself (parent of "plugins/").
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hours offset from UTC, set from config.ini [settings] timezone.
TZ_OFFSET = 0

# Length of first line used for auto-generated title, set from config.ini.
TITLE_DEFAULT_LEN = 20

# XEP-0439 Quick Response namespace.
QUICK_RESPONSE_NS = 'urn:xmpp:tmp:quick-response'

# Maximum number of numbered entries rendered as quick-response buttons.
QUICK_MAX_ITEMS = 10

# Short, menu-independent quick-response labels keyed by stable command
# tokens (see XEP-0439).  Numeric tokens are never translated.
QUICK_LABELS = {
    'ru': {
        'list': 'Список', 'arch': 'Архив', 'srch': 'Поиск',
        'help': 'Справка', 'title': 'Заголовок', 'add': 'Добавить',
        'del': 'Удалить', 'delete': 'Удалить', 'body': 'Текст',
        'remind': 'Время', 'old': 'Срок', 'host': 'Адрес',
        'user': 'Логин', 'pass': 'Пароль', 'save': 'Сохранить',
        'reg': 'Регистрация', 'unreg': 'Отвязать', 'conf': 'Настройки',
        'name': 'Файл', 'album': 'Альбом', 'exit': 'Выход',
        'cancel': 'Отмена', 'back': 'Назад', 'yes': 'Да',
        'year': 'Год', 'month': 'Месяц', 'day': 'День',
        'hour': 'Час', 'minute': 'Минута', 'relative': 'Через',
        'value': 'Значение', 'unit': 'Единица', 'week': 'Неделя',
        '1h': '1 ч', '6h': '6 ч', '12h': '12 ч',
        '1d': '1 д', '3d': '3 д', '1w': '1 нед', '2w': '2 нед',
        '1mo': '1 мес', '3mo': '3 мес', '6mo': '6 мес',
        '1y': '1 год', 'never': 'Никогда',
    },
    'en': {
        'list': 'List', 'arch': 'Archive', 'srch': 'Search',
        'help': 'Help', 'title': 'Title', 'add': 'Add',
        'del': 'Delete', 'delete': 'Delete', 'body': 'Text',
        'remind': 'Time', 'old': 'Expiry', 'host': 'Host',
        'user': 'User', 'pass': 'Password', 'save': 'Save',
        'reg': 'Register', 'unreg': 'Unregister', 'conf': 'Engine',
        'name': 'Name', 'album': 'Album', 'exit': 'Exit',
        'cancel': 'Cancel', 'back': 'Back', 'yes': 'Yes',
        'year': 'Year', 'month': 'Month', 'day': 'Day',
        'hour': 'Hour', 'minute': 'Minute', 'relative': 'Relative',
        'value': 'Value', 'unit': 'Unit', 'week': 'Week',
        '1h': '1 h', '6h': '6 h', '12h': '12 h',
        '1d': '1 d', '3d': '3 d', '1w': '1 w', '2w': '2 w',
        '1mo': '1 mo', '3mo': '3 mo', '6mo': '6 mo',
        '1y': '1 y', 'never': 'Never',
    },
}

# Hostname or IPv4/IPv6 literal, safe to pass to external tools/sockets.
_HOSTNAME_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?'
                          r'(\.[A-Za-z0-9]([A-Za-z0-9\-_]*[A-Za-z0-9])?)*$')
_IPV6_RE = re.compile(r'^((?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,7}:'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[A-Fa-f0-9]{1,4}){1,4}'
                      r'|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}'
                      r'|[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}'
                      r'|:(?:(?::[0-9A-Fa-f]{1,4}){1,7}|:)'
                      r'|(?:fe80|FE80):(?::[0-9A-Fa-f]{0,4}){0,4}%[0-9a-zA-Z]+'
                      r'|::(?:ffff|FFFF)?(?::0{1,4})?:(?:25[0-5]|2[0-4]\d|'
                      r'1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})$')


def valid_host(token):
    """True if *token* looks like a hostname or IP literal."""
    if not token:
        return False
    return bool(_HOSTNAME_RE.match(token) or _IPV6_RE.match(token))


def split_args(text):
    """Split a message body into arguments (shlex-style with fallback)."""
    try:
        import shlex
        return shlex.split(text)
    except ValueError:
        return text.split()


def parse_flags(args):
    """Split argument list into a set of flags ("-6", "-u", ...) and
    positional arguments."""
    flags = set()
    positional = []
    for arg in args:
        if arg.startswith('-'):
            flags.add(arg)
        else:
            positional.append(arg)
    return flags, positional


async def check_tcp(host, port, family=socket.AF_UNSPEC, timeout=5.0):
    """Try to open a TCP connection.

    Returns a tuple ``(ok, elapsed_ms, error_string)``.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, family=family), timeout)
        elapsed = int((loop.time() - started) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, elapsed, None
    except Exception as exc:
        elapsed = int((loop.time() - started) * 1000)
        err = str(exc) or type(exc).__name__
        if isinstance(exc, asyncio.TimeoutError):
            err = 'timed out after %.0f ms' % (timeout * 1000)
        elif isinstance(exc, ConnectionRefusedError):
            err = 'connection refused'
        elif isinstance(exc, socket.gaierror):
            err = 'name resolution failed'
        return False, elapsed, err


class Reply(str):
    """Reply text that may carry XEP-0439 quick responses.

    Subclasses :class:`str` so existing code that treats a bot reply as
    plain text keeps working unchanged.  ``responses`` is a list of
    ``(value, label)`` tuples rendered as ``<response/>`` elements.
    """

    def __new__(cls, body, responses=()):
        obj = super().__new__(cls, body)
        obj.responses = list(responses)
        return obj


def numbered(count, limit=QUICK_MAX_ITEMS):
    """Quick responses for a numbered list: first *limit* items."""
    return [(str(i), str(i)) for i in range(1, min(count, limit) + 1)]


def quick_label(token, lang='ru'):
    """Short menu-independent label for a quick-response command token."""
    token = str(token)
    if token.isdigit():
        return token
    return QUICK_LABELS.get(lang, {}).get(token, token)


def resolve_responses(responses, lang='ru'):
    """Resolve ``(value, label)`` pairs into final, deduplicated labels.

    A label equal to its value is replaced by :func:`quick_label`; any
    explicitly different label is kept as an override.
    """
    result = []
    seen_values = set()
    seen_labels = set()
    for value, label in responses:
        value = str(value)
        label = str(label)
        if label == value:
            label = quick_label(value, lang)
        if not value or value in seen_values or label in seen_labels:
            continue
        seen_values.add(value)
        seen_labels.add(label)
        result.append((value, label))
    return result


class Bot:
    """Base class for all Toolbox bots."""

    NAME = ''          # local part of the bot JID and its nick
    DESCRIPTION = ''   # short description (vCard, service lists)
    HELP = ''          # usage hint sent on empty input
    QUICK_RESPONSES = False  # emits XEP-0439 <response/> buttons
    QUICK_LANG = 'ru'  # label language for quick responses

    def __init__(self):
        self.jid = ''

    @property
    def name(self):
        return self.NAME

    def help_reply(self, jid):
        """Return the help text (optionally a :class:`Reply` with buttons)."""
        return self.HELP

    async def handle(self, text, ctx):
        """Process an incoming message body and return reply text."""
        raise NotImplementedError()


def load_plugins(directory):
    """Import every *.py file from *directory* and collect Bot classes."""
    bots = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        path = os.path.join(directory, fname)
        modname = 'toolbox_plugin_' + fname[:-3]
        spec = importlib.util.spec_from_file_location(modname, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            log.exception('Failed to load plugin %s', fname)
            continue
        found = False
        for obj in vars(module).values():
            if (isinstance(obj, type) and issubclass(obj, Bot)
                    and obj.__module__ == modname):
                bots.append(obj())
                found = True
        if not found:
            log.warning('No Bot subclass found in %s', fname)
    return bots
