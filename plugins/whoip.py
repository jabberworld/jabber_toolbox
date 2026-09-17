"""whoip bot: resolve domain → IP then WHOIS on the resulting IP."""

import asyncio
import os
import re
import socket
import sqlite3
import time

from plugins import Bot, BASE_DIR, valid_host
from plugins.whois import Whois, CACHE_TTL, IANA_SERVER, ARIN_SERVER

_IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')


class Whoip(Bot):
    NAME = 'whoip'
    DESCRIPTION = 'Resolves a domain to IP, then WHOIS lookup on that IP.'
    HELP = ('Whoip bot.\n'
            'Usage: <domain|IP>\n'
            'Domains are resolved to IP first, then WHOIS is performed.\n'
            'Results are cached for 24 hours.\n'
            'Example:\n'
            '  google.com')

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(
            os.path.join(BASE_DIR, 'whois_cache.sqlite3'))
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS cache ('
            ' qkey TEXT PRIMARY KEY, ts INTEGER NOT NULL, result TEXT NOT NULL)')
        self.db.commit()
        self._whois = Whois()

    async def handle(self, text, ctx):
        target = text.strip()
        if not valid_host(target):
            return self.HELP

        key = target.lower().rstrip('.')
        now = int(time.time())

        row = self.db.execute(
            'SELECT ts, result FROM cache WHERE qkey=?',
            ('v4:' + key,)).fetchone()
        if row and now - row[0] < CACHE_TTL:
            age_h = max(1, (now - row[0]) // 3600)
            return '%s\n[cached %d hour(s) ago]' % (row[1], age_h)

        is_ip = bool(_IPV4_RE.match(target) or ':' in target)
        if is_ip:
            ip = target
        else:
            ip = await self._resolve(target)
            if not ip:
                return 'Could not resolve %s to an IP address.' % target

        try:
            result = await asyncio.wait_for(
                self._whois._lookup(ip), timeout=30)
        except Exception as exc:
            return 'Whois lookup failed: %s' % (str(exc) or type(exc).__name__)

        result = Whois._clean(result).strip()
        result = self._filter_comments(result)
        if not result:
            return 'Empty whois response.'

        if is_ip:
            output = result
        else:
            output = '%s → %s\n%s' % (target, ip, result)

        self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                        ('v4:' + key, now, output))
        self.db.commit()
        return output

    @staticmethod
    async def _resolve(host):
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None, family=socket.AF_UNSPEC)
            if not infos:
                return None
            for info in infos:
                if info[0] == socket.AF_INET:
                    return info[4][0]
            return infos[0][4][0]
        except Exception:
            return None

    @staticmethod
    def _filter_comments(text):
        return '\n'.join(ln for ln in text.splitlines()
                         if not ln.strip().startswith('#'))
