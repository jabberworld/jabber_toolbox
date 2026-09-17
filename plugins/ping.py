"""ping bot: runs the system ping utility (4 packets) and returns its output."""

import asyncio

from plugins import Bot, parse_flags, split_args, valid_host


class Ping(Bot):
    NAME = 'ping'
    DESCRIPTION = 'ICMP ping. Send me a hostname or IP address.'
    HELP = ('Ping bot.\n'
            'Usage: <host|IP>\n'
            'Options:\n'
            '  -4    force IPv4\n'
            '  -6    force IPv6\n'
            'Example:\n'
            '  linuxoid.in')

    async def handle(self, text, ctx):
        args = split_args(text)
        flags, positional = parse_flags(args)
        if len(positional) != 1 or not valid_host(positional[0]):
            return self.HELP
        target = positional[0]

        cmd = ['ping', '-c', '4', '-w', '20']
        if '-6' in flags:
            cmd.append('-6')
        elif '-4' in flags:
            cmd.append('-4')
        cmd.append(target)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 'Ping timed out.'
        output = out.decode('utf-8', errors='replace').strip()
        return output or 'No output from ping.'
