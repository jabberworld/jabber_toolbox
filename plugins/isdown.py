"""isdown bot: simplified "is it down" checker for hosts and URLs."""

import re
from urllib.parse import urlsplit

from plugins import Bot, check_tcp, valid_host

_SCHEME_PORTS = {'http': 80, 'https': 443}
_BRACKET_RE = re.compile(r'^\[(.+?)\](?::(\d+))?$')


class IsDown(Bot):
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

    async def handle(self, text, ctx):
        target = text.strip()
        if not target:
            return self.HELP

        if '://' in target:
            parts = urlsplit(target)
            scheme = parts.scheme.lower()
            if scheme not in _SCHEME_PORTS:
                return "Unsupported URL scheme '%s'." % parts.scheme
            host = parts.hostname
            port = parts.port or _SCHEME_PORTS[scheme]
        elif target.startswith('['):
            match = _BRACKET_RE.match(target)
            if not match:
                return self.HELP
            host = match.group(1)
            port = int(match.group(2) or 443)
        else:
            head, _, tail = target.rpartition(':')
            if head and tail.isdigit():
                host, port = head, int(tail)
            else:
                host, port = target, 443

        host = host.strip('[]')
        if not host or not valid_host(host):
            return 'Invalid host.'
        if not 1 <= port <= 65535:
            return 'Invalid port.'

        ok, elapsed, err = await check_tcp(host, port, timeout=5.0)
        if ok:
            return '%s:%d is UP (%d ms)' % (host, port, elapsed)
        return '%s:%d is DOWN (%s)' % (host, port, err)
