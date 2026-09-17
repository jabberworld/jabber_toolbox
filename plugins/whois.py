"""whois bot: raw WHOIS queries with a 24-hour sqlite cache."""

import asyncio
import os
import re
import sqlite3
import time

from plugins import Bot, BASE_DIR, valid_host

CACHE_TTL = 24 * 3600
IANA_SERVER = 'whois.iana.org'
ARIN_SERVER = 'whois.arin.net'

_IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')
_REFERRAL_RE = re.compile(
    r'(?im)^\s*(?:registrar\s+whois\s+server|whois\s+server|'
    r'referralserver|whois)\s*:\s*(\S+)\s*$')

_PLACEHOLDER_VALUES = frozenset((
    'redacted for privacy',
    'redacted',
    'n/a',
))

_DISCLAIMER_TAIL_RE = re.compile(
    r'(?im)^(?:'
    r'terms[ _-]?(?:of[ _-]?use|and[ _-]?conditions?)\s*:'
    r'|(?:legal[ _-])?disclaimer\b'
    r'|by (?:submitting|using|querying)\b[^\n]*?(?:whois|quer(?:y|ies)|service)\b'
    r'|the (?:data|information) (?:contained|in|presented|appearing)[^\n]*whois'
    r'|notice\s*:\s*(?:the|access|any|use)'
    r'|access to (?:the )?\S{0,20}[ ]?whois'
    r'|registration data[^\n]*provided'
    r'|whois (?:data|output|information)[^\n]*(?:provided|subject)'
    r'|the arin \S+[^\n]*(?:provided|subject|terms)'
    r')')

_LEGAL_PROSE_RE = re.compile(
    r'(?i)\b(?:agree|lawful purposes?|all rights reserved|'
    r'modify these terms|terms of (?:use|service)|disclaimer|guarantee|'
    r'temporary specification|\bicann\b|web[ -]based whois|non-public|'
    r'legitimate interest|\bspam\b|\babuse\b)')

_DISCLAIMER_SENTENCE_RE = re.compile(
    r'(?i)^(?:access to |the data (?:contained|in)|by submitting|'
    r'by using the |terms of use|legal disclaimer|notice:)')

_FIELD_RE = re.compile(r'^\s*[A-Za-z][A-Za-z0-9 _.-]{0,40}:\s*(?!/)\S')
_TRAILER_RE = re.compile(r'^\s*>>>.*<<<\s*$')


class Whois(Bot):
    NAME = 'whois'
    DESCRIPTION = 'WHOIS lookup for domains and IP addresses.'
    HELP = ('Whois bot.\n'
            'Usage: <domain|IP>\n'
            'Results are cached for 24 hours.\n'
            'Example:\n'
            '  linuxoid.in')

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(
            os.path.join(BASE_DIR, 'whois_cache.sqlite3'))
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS cache ('
            ' qkey TEXT PRIMARY KEY, ts INTEGER NOT NULL, result TEXT NOT NULL)')
        self.db.commit()

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

        try:
            result = await asyncio.wait_for(self._lookup(target), timeout=30)
        except Exception as exc:
            return 'Whois lookup failed: %s' % (str(exc) or type(exc).__name__)
        result = self._clean(result).strip()
        if not result:
            return 'Empty whois response.'
        self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                        ('v4:' + key, now, result))
        self.db.commit()
        return result

    @classmethod
    def _clean(cls, text):
        return cls._filter_placeholders(cls._strip_disclaimers(text))

    @staticmethod
    def _filter_placeholders(text):
        kept = []
        for line in text.splitlines():
            stripped = line.strip()
            _, sep, value = stripped.partition(':')
            if sep:
                value = value.strip()
                if not value:
                    continue
                candidate = value.rstrip('.,;')
            else:
                candidate = stripped.rstrip('.,;')
            if candidate.lower() in _PLACEHOLDER_VALUES:
                continue
            kept.append(line)
        return '\n'.join(kept)

    @staticmethod
    def _strip_disclaimers(text):
        lines = [ln for ln in text.splitlines()
                 if not _TRAILER_RE.match(ln)]

        for i, line in enumerate(lines):
            if not _DISCLAIMER_TAIL_RE.match(line):
                continue
            tail = lines[i:]
            nonempty = sum(1 for t in tail if t.strip())
            fields = sum(1 for t in tail if _FIELD_RE.match(t))
            if nonempty and i > 0 and fields <= max(1, nonempty // 5):
                lines = lines[:i]
                break

        first_blank = next(
            (i for i, ln in enumerate(lines) if not ln.strip()), -1)
        if first_blank > 0:
            head = [ln for ln in lines[:first_blank] if ln.strip()]
            if head and all(not _FIELD_RE.match(ln) for ln in head) \
                    and any(_DISCLAIMER_SENTENCE_RE.match(ln)
                            for ln in head):
                lines = lines[first_blank + 1:]

        while True:
            last_blank = next(
                (i for i in range(len(lines) - 1, -1, -1)
                 if not lines[i].strip()), None)
            if last_blank is None:
                break
            chunk = [ln for ln in lines[last_blank + 1:] if ln.strip()]
            if not chunk:
                lines = lines[:last_blank]
                continue
            joined = ' '.join(chunk)
            if all(not _FIELD_RE.match(ln) for ln in chunk) \
                    and _LEGAL_PROSE_RE.search(joined):
                lines = lines[:last_blank]
            else:
                break

        while lines and (not lines[-1].strip()
                         or lines[-1].lstrip()[:1] in ('#', '%')):
            lines.pop()
        while lines and (not lines[0].strip()
                         or lines[0].lstrip()[:1] in ('#', '%')):
            lines.pop(0)

        return '\n'.join(lines).strip('\n')

    async def _lookup(self, target):
        if _IPV4_RE.match(target) or ':' in target:
            server, port = ARIN_SERVER, 43
        else:
            server, port = IANA_SERVER, 43
        text = await self._query(server, port, target)
        hops = 0
        while hops < 2:
            referral = self._referral(text)
            if not referral or referral[0] == server:
                break
            server, port = referral
            newer = await self._query(server, port, target)
            if newer.strip():
                text = newer
            hops += 1
        return text

    @staticmethod
    def _referral(text):
        match = _REFERRAL_RE.search(text)
        if not match:
            return None
        url = match.group(1).strip()
        for prefix in ('rwhois://', 'whois://', 'http://', 'https://'):
            if url.lower().startswith(prefix):
                url = url[len(prefix):]
                break
        host, _, port = url.partition('/')
        host = host.strip()
        if ':' in host:
            host, _, port_s = host.partition(':')
            if port_s.isdigit():
                return host, int(port_s)
        if not host:
            return None
        return host, 43

    @staticmethod
    async def _query(server, port, query):
        reader, writer = await asyncio.open_connection(server, port)
        writer.write(('%s\r\n' % query).encode('utf-8'))
        await writer.drain()
        chunks = []
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break
            chunks.append(chunk)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return b''.join(chunks).decode('utf-8', errors='replace')
