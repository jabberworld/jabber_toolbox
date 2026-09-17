"""shorty bot: link shortener with several pluggable engines."""

import asyncio
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request

from plugins import Bot, BASE_DIR

USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/139.0 Safari/537.36 JabberToolbox')
DEFAULT_ENGINE = 'u.to'


def _http(url, data=None, headers=None, method=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    req.add_header('User-Agent', USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


class Uto:
    label = 'u.to'

    @staticmethod
    async def shorten(url):
        body = json.dumps({'url': url}).encode('utf-8')
        raw = await asyncio.to_thread(
            _http, 'https://u.to/api/shorten/', data=body,
            headers={'Content-Type': 'application/json',
                     'Accept': 'application/json',
                     'Referer': 'https://u.to/',
                     'Origin': 'https://u.to'},
            method='POST')
        doc = json.loads(raw)
        if doc.get('success') and doc.get('shortUrl'):
            return doc['shortUrl']
        raise RuntimeError(doc.get('message') or 'service returned an error')


class Clck:
    label = 'clck.ru'

    @staticmethod
    async def shorten(url):
        api = 'https://clck.ru/--?url=' + urllib.parse.quote(url, safe='')
        raw = (await asyncio.to_thread(_http, api)).strip()
        if raw.startswith('http'):
            return raw
        raise RuntimeError(raw[:200] or 'service returned an error')


class Isgd:
    label = 'is.gd'

    @staticmethod
    async def shorten(url):
        api = ('https://is.gd/create.php?format=simple&url='
               + urllib.parse.quote(url, safe=''))
        raw = (await asyncio.to_thread(_http, api)).strip()
        if raw.startswith('http'):
            return raw
        raise RuntimeError(raw[:200] or 'service returned an error')


ENGINES = {'u.to': Uto, 'clck.ru': Clck, 'is.gd': Isgd}
_URL_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$')
_DOMAIN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.\-_]+(:\d+)?(/\S*)?$')


class Shorty(Bot):
    NAME = 'shorty'
    DESCRIPTION = ('URL shortener. Send a link to shorten it or send '
                   '"conf" to choose a shortening engine.')
    HELP = ('Shorty bot - link shortener.\n'
            'Commands:\n'
            '  <link>      shorten the link\n'
            '  conf        list available engines and select one\n'
            'Default engine: %s.\n'
            'Example:\n'
            '  https://example.com/very/long/link' % DEFAULT_ENGINE)

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(os.path.join(BASE_DIR, 'shorty.sqlite3'))
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS settings ('
            ' jid TEXT PRIMARY KEY, engine TEXT NOT NULL)')
        self.db.commit()
        self.pending = set()

    def get_engine(self, jid):
        row = self.db.execute(
            'SELECT engine FROM settings WHERE jid=?', (jid,)).fetchone()
        if row and row[0] in ENGINES:
            return row[0]
        return DEFAULT_ENGINE

    async def handle(self, text, ctx):
        jid = ctx['from'].bare
        body = text.strip()
        lowered = body.lower()

        if lowered == 'conf':
            self.pending.add(jid)
            lines = ['Available engines:']
            for idx, key in enumerate(ENGINES, 1):
                mark = ''
                if key == self.get_engine(jid):
                    mark = ' <- current'
                lines.append('%d. %s%s' % (idx, ENGINES[key].label, mark))
            lines.append("Reply with a number to select an engine.")
            return '\n'.join(lines)

        names = list(ENGINES)
        if jid in self.pending:
            self.pending.discard(jid)
            if body.isdigit():
                num = int(body)
                if not 1 <= num <= len(names):
                    return ("No such engine number. Send 'conf' to see "
                            'the list.')
                chosen = names[num - 1]
                self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                (jid, chosen))
                self.db.commit()
                return 'Engine set to %s.' % ENGINES[chosen].label
            # anything else falls through to normal shortening

        url = body if _URL_RE.match(body) else None
        if url is None and _DOMAIN_RE.match(body):
            url = 'https://' + body
        if url is None:
            return self.HELP

        engine_key = self.get_engine(jid)
        engine = ENGINES.get(engine_key)
        try:
            short = await engine.shorten(url)
        except Exception as exc:
            return 'Shortening failed (%s): %s' % (
                engine.label, str(exc) or type(exc).__name__)
        return short
