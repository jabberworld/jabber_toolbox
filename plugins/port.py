"""port bot: TCP and UDP port availability checks."""

import asyncio
import socket

from plugins import Bot, check_tcp, parse_flags, split_args, valid_host


class Port(Bot):
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

    async def handle(self, text, ctx):
        args = split_args(text)
        flags, positional = parse_flags(args)
        if len(positional) != 2:
            return self.HELP
        host = positional[0]
        if not valid_host(host):
            return 'Invalid host.'
        try:
            port = int(positional[1])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            return 'Invalid port (must be an integer 1-65535).'

        family = socket.AF_INET6 if '-6' in flags else socket.AF_UNSPEC
        if '-u' in flags:
            return await self._check_udp(host, port, family)
        ok, elapsed, err = await check_tcp(host, port, family=family,
                                           timeout=5.0)
        if ok:
            return '%s:%d/tcp is OPEN (%d ms)' % (host, port, elapsed)
        return '%s:%d/tcp is CLOSED/FILTERED (%s)' % (host, port, err)

    @staticmethod
    async def _check_udp(host, port, family):
        loop = asyncio.get_running_loop()
        result = loop.create_future()

        class Proto(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                if not result.done():
                    result.set_result(('reply', addr))

            def error_received(self, exc):
                if not result.done():
                    result.set_result(('error', exc))

        try:
            transport, _ = await loop.create_datagram_endpoint(
                Proto, remote_addr=(host, port), family=family)
        except Exception as exc:
            return '%s:%d/udp is CLOSED/FILTERED (%s)' % (
                host, port, str(exc) or type(exc).__name__)

        started = loop.time()
        try:
            transport.sendto(b'')
            kind, val = await asyncio.wait_for(result, timeout=3.0)
            elapsed = int((loop.time() - started) * 1000)
            if kind == 'reply':
                return ('%s:%d/udp is OPEN (reply received from %s '
                        'in %d ms)' % (host, port, val[0], elapsed))
            return '%s:%d/udp is CLOSED/FILTERED (%s)' % (host, port, val)
        except asyncio.TimeoutError:
            elapsed = int((loop.time() - started) * 1000)
            return ('%s:%d/udp is OPEN (no reply and no ICMP error in %d '
                    'ms - assuming open)' % (host, port, elapsed))
        finally:
            transport.close()
