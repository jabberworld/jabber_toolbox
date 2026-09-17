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


class Bot:
    """Base class for all Toolbox bots."""

    NAME = ''          # local part of the bot JID and its nick
    DESCRIPTION = ''   # short description (vCard, service lists)
    HELP = ''          # usage hint sent on empty input

    def __init__(self):
        self.jid = ''

    @property
    def name(self):
        return self.NAME

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
