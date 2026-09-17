"""sslcheck bot: SSL/TLS certificate validation for hosts and URLs."""

import asyncio
import os
import re
import ssl
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

from plugins import Bot, valid_host

_BRACKET_RE = re.compile(r'^\[(.+?)\](?::(\d+))?$')

HTTPS_PORT = 443
WARN_DAYS = 30


class SSLCheck(Bot):
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

    async def handle(self, text, ctx):
        target = text.strip()
        if not target:
            return self.HELP

        parsed = self._parse_target(target)
        if isinstance(parsed, str):
            return parsed
        host, port = parsed

        try:
            cert_data, elapsed, trusted, ssl_info = await asyncio.wait_for(
                self._get_cert(host, port), timeout=15)
        except asyncio.TimeoutError:
            return 'Connection to %s:%d timed out.' % (host, port)
        except ssl.SSLError as exc:
            return 'SSL error for %s:%d: %s' % (host, port, exc)
        except OSError as exc:
            return 'Connection to %s:%d failed: %s' % (host, port, exc)

        return self._format(host, port, cert_data, elapsed, trusted,
                            ssl_info)

    @staticmethod
    def _parse_target(target):
        if '://' in target:
            parts = urlsplit(target)
            if parts.scheme.lower() != 'https':
                return 'Only HTTPS URLs are supported.'
            host = parts.hostname
            port = parts.port or HTTPS_PORT
        elif target.startswith('['):
            match = _BRACKET_RE.match(target)
            if not match:
                return 'Invalid bracket notation.'
            host = match.group(1)
            port = int(match.group(2) or HTTPS_PORT)
        else:
            head, _, tail = target.rpartition(':')
            if head and tail.isdigit():
                host, port = head, int(tail)
            else:
                host, port = target, HTTPS_PORT

        host = host.strip('[]')
        if not host or not valid_host(host):
            return 'Invalid host.'
        if not 1 <= port <= 65535:
            return 'Invalid port.'
        return host, port

    @staticmethod
    async def _get_cert(host, port):
        loop = asyncio.get_running_loop()
        started = loop.time()

        # Try with CERT_REQUIRED (no hostname check) to get full cert details
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED

        try:
            reader, writer = await asyncio.open_connection(
                host, port, ssl=context, server_hostname=host)
            trusted = True
        except (ssl.SSLCertVerificationError, ssl.SSLError):
            # Fallback: connect without verification, decode cert from DER
            context2 = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context2.check_hostname = False
            context2.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.open_connection(
                host, port, ssl=context2, server_hostname=host)
            trusted = False
            sslsock = writer.transport.get_extra_info('ssl_object')
            cert = SSLCheck._decode_cert(sslsock)
            ssl_info = await SSLCheck._extract_ssl_info(sslsock, loop)
            if ssl_info and 'available' not in ssl_info:
                ssl_info['available'] = await SSLCheck._probe_versions(
                    host, port)
            elapsed = int((loop.time() - started) * 1000)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=3)
            except Exception:
                pass
            return cert, elapsed, trusted, ssl_info

        elapsed = int((loop.time() - started) * 1000)
        sslsock = writer.transport.get_extra_info('ssl_object')
        cert = sslsock.getpeercert()
        ssl_info = await SSLCheck._extract_ssl_info(sslsock, loop)
        if ssl_info and 'available' not in ssl_info:
            ssl_info['available'] = await SSLCheck._probe_versions(host, port)
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=3)
        except Exception:
            pass
        return cert, elapsed, trusted, ssl_info

    @staticmethod
    def _decode_cert(sslsock):
        cert_der = sslsock.getpeercert(binary_form=True)
        if not cert_der:
            return {}
        cert_pem = ssl.DER_cert_to_PEM_cert(cert_der)
        fd, path = tempfile.mkstemp(suffix='.pem')
        try:
            os.write(fd, cert_pem.encode())
            os.close(fd)
            return ssl._ssl._test_decode_cert(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    async def _extract_ssl_info(sslsock, loop):
        info = {}
        try:
            info['protocol'] = sslsock.version()
        except Exception:
            pass
        try:
            name, _, bits = sslsock.cipher()
            info['cipher_name'] = name
            info['cipher_bits'] = bits
        except Exception:
            pass
        try:
            ciphers = await asyncio.wait_for(
                loop.run_in_executor(None, sslsock.shared_ciphers),
                timeout=2)
            info['available'] = sorted(
                set(c[1] for c in ciphers), reverse=True)
        except Exception:
            pass
        return info or None

    @staticmethod
    async def _probe_versions(host, port):
        versions = ['TLSv1', 'TLSv1_1', 'TLSv1_2', 'TLSv1_3']

        async def _try_one(ver_name):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_default_certs()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                ver = getattr(ssl.TLSVersion, ver_name)
                ctx.minimum_version = ver
                ctx.maximum_version = ver
            except Exception:
                return None
            try:
                r, w = await asyncio.wait_for(
                    asyncio.open_connection(
                        host, port, ssl=ctx, server_hostname=host),
                    timeout=1.5)
                w.close()
                await asyncio.wait_for(w.wait_closed(), timeout=1)
                return ver_name
            except Exception:
                return None

        results = await asyncio.gather(
            *[_try_one(v) for v in versions])
        return sorted([r for r in results if r is not None])

    @staticmethod
    def _format(host, port, cert, elapsed_ms, trusted=True, ssl_info=None):
        if not cert:
            return 'SSL %s:%d - could not retrieve certificate.' % (host, port)

        # --- hostname match ---
        matched = SSLCheck._check_hostname(cert, host)

        # --- subject CN + SAN ---
        subject_cn = ''
        san_names = []
        for entry in cert.get('subject', ()):
            for rtype, value in entry:
                if rtype == 'commonName':
                    subject_cn = value
        for san_type, san_value in cert.get('subjectAltName', ()):
            if san_type == 'DNS':
                san_names.append(san_value)

        # --- issuer ---
        issuer_parts = []
        issuer_cn = ''
        for entry in cert.get('issuer', ()):
            for rtype, value in entry:
                if rtype in ('organizationName', 'commonName'):
                    issuer_parts.append(value)
                if rtype == 'commonName':
                    issuer_cn = value
        issuer_str = issuer_parts[0] if issuer_parts else 'unknown'

        # --- self-signed check ---
        is_self_signed = (not trusted and subject_cn and issuer_cn
                          and subject_cn == issuer_cn)

        # --- expiry ---
        not_after_str = cert.get('notAfter', '')
        days_left = None
        if not_after_str:
            not_after = datetime.strptime(
                not_after_str, '%b %d %H:%M:%S %Y %Z').replace(
                    tzinfo=timezone.utc)
            days_left = (not_after - datetime.now(timezone.utc)).days
            expires_fmt = not_after.strftime('%Y-%m-%d')
        else:
            expires_fmt = 'unknown'

        # --- warnings ---
        warnings = []
        if not matched:
            expected = host
            got = subject_cn or ', '.join(san_names) or 'unknown'
            warnings.append(
                'Certificate does NOT match host '
                '(expected %s, got %s)' % (expected, got))
        if days_left is not None and days_left < 0:
            warnings.append('Certificate has EXPIRED!')
        elif days_left is not None and days_left < WARN_DAYS:
            warnings.append(
                'Certificate expires in less than %d days!' % WARN_DAYS)
        if is_self_signed:
            warnings.append('Certificate is SELF-SIGNED (not trusted).')
        elif not trusted:
            warnings.append('Certificate is NOT trusted (chain validation failed).')

        # --- output ---
        icon = '✓' if (matched and not warnings) else '⚠'
        lines = ['SSL %s:%d %s' % (host, port, icon)]
        lines.append('Host: %s' % host)
        lines.append('Valid: %s' % ('yes' if matched else 'NO'))
        lines.append('Expires: %s (%s)' % (
            expires_fmt, SSLCheck._format_days(days_left)))
        lines.append('Issuer: %s' % issuer_str)
        if ssl_info:
            proto = ssl_info.get('protocol')
            avail = ssl_info.get('available')
            if proto:
                if avail:
                    lines.append('Protocol: %s ✓ (available: %s)' % (
                        proto, ', '.join(avail)))
                else:
                    lines.append('Protocol: %s' % proto)
            cipher_name = ssl_info.get('cipher_name')
            cipher_bits = ssl_info.get('cipher_bits')
            if cipher_name:
                if cipher_bits:
                    lines.append('Cipher: %s (%d bit)' % (
                        cipher_name, cipher_bits))
                else:
                    lines.append('Cipher: %s' % cipher_name)
        if elapsed_ms is not None:
            lines.append('Handshake: %d ms' % elapsed_ms)
        for w in warnings:
            lines.append('WARNING: %s' % w)
        return '\n'.join(lines)

    @staticmethod
    def _check_hostname(cert, host):
        host = host.lower()
        san_names = []
        for san_type, san_value in cert.get('subjectAltName', ()):
            if san_type == 'DNS':
                san_names.append(san_value.lower())
        if san_names:
            for name in san_names:
                if name.startswith('*.'):
                    suffix = name[1:]  # e.g. ".badssl.com"
                    if host.endswith(suffix):
                        prefix = host[:-len(suffix)]
                        if prefix and '.' not in prefix:
                            return True
                elif name == host:
                    return True
            return False
        for entry in cert.get('subject', ()):
            for rtype, value in entry:
                if rtype == 'commonName':
                    cn = value.lower()
                    if cn.startswith('*.'):
                        suffix = cn[1:]
                        if host.endswith(suffix):
                            prefix = host[:-len(suffix)]
                            if prefix and '.' not in prefix:
                                return True
                    elif cn == host:
                        return True
        return False

    @staticmethod
    def _format_days(days):
        if days is None:
            return 'unknown'
        if days < 0:
            return 'expired %d day(s) ago' % abs(days)
        if days == 0:
            return 'expires today'
        if days == 1:
            return '1 day remaining'
        return '%d days remaining' % days
