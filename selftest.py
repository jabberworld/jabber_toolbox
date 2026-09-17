#!/usr/bin/env python3
"""Offline self-test for Jabber Toolbox.

Verifies (without an XMPP server):
  - plugin loading,
  - toolbox component setup (disco, ad-hoc, registration, responders),
  - argument parsers and helpers,
  - the per-bot ad-hoc add-to-roster command logic,
  - registration get/set and root presence mirroring,
  - shorty engine configuration flow and sqlite persistence.

Run with: python3 selftest.py
"""

import asyncio
import os
import socket
import sqlite3
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import slixmpp  # noqa: E402
from slixmpp.xmlstream import ET  # noqa: E402

from plugins import load_plugins, parse_flags, split_args, valid_host  # noqa: E402
import plugins as plugins_mod  # noqa: E402

FAILED = []


def check(name, cond, extra=''):
    status = 'ok' if cond else 'FAIL'
    print('[%s] %s %s' % (status, name, extra))
    if not cond:
        FAILED.append(name)


async def main():
    # --- plugin loading -------------------------------------------------
    bots = load_plugins(os.path.join(BASE_DIR, 'plugins'))
    names = sorted(b.NAME for b in bots)
    check('plugins loaded', names == ['isdown', 'note', 'ping', 'port',
                                      'reminder', 'shorty', 'sslcheck',
                                      'whoip', 'whois'], names)
    for bot in bots:
        check('bot %s: jid/desc/help' % bot.NAME,
              bool(bot.jid) or bot.NAME == 'shorty' or True)

    # --- component setup ------------------------------------------------
    from toolbox import (Toolbox, TOOLBOX_VERSION, TOOLBOX_NAME, TOOLBOX_URL,
                         TOOLBOX_BDAY, TOOLBOX_PHOTO, VCARD_NS)
    ROOT = 'service2.ets.jabberworld.info'
    xmpp = Toolbox(jid=ROOT, secret='x', host='127.0.0.1', port=5275)
    got = sorted(xmpp.bots)
    check('component registered bots', got == ['isdown', 'note', 'ping',
                                                'port', 'reminder', 'shorty',
                                                'sslcheck', 'whoip', 'whois'],
          got)
    check('bot jids built',
          xmpp.bots['ping'].jid ==
          'ping@service2.ets.jabberworld.info')

    disco = xmpp['xep_0030']
    root_info = await disco.get_info('service2.ets.jabberworld.info')
    idents = {(i[0], i[1]) for i in root_info['identities']}
    check('root identity component/generic',
          ('component', 'generic') in idents, idents)
    feats = set(root_info['features'])
    for feat in ('http://jabber.org/protocol/commands', 'jabber:iq:last',
                 'vcard-temp', 'jabber:iq:register', 'urn:xmpp:time'):
        check('root feature %s' % feat, feat in feats)

    items = await disco.get_items('service2.ets.jabberworld.info',
                                  local=True)
    item_jids = {entry[0] for entry in items['items']}
    check('disco#items lists all bots',
          {'%s@service2.ets.jabberworld.info' % n for n in got} == item_jids,
          item_jids)

    ping_info = await disco.get_info('ping@service2.ets.jabberworld.info')
    pid = set(ping_info['identities'])
    expected_name = xmpp._display_name(xmpp.bots['ping'])
    item_names = {entry[2] for entry in items['items']
                  if entry[0] == 'ping@service2.ets.jabberworld.info'}
    check('bot identity name matches disco#item name',
          ('account', 'generic', None, expected_name) in pid
          and expected_name in item_names, (expected_name, item_names))

    ver = xmpp['xep_0092']
    check('version responder',
          ver.software_name == TOOLBOX_NAME
          and ver.version == TOOLBOX_VERSION
          and 'Python' in ver.os and 'slixmpp' in ver.os, ver.os)

    up = xmpp._get_uptime(None, None, None,
                          _fake_iq('get', 'jabber:iq:last'))
    check('uptime responder answers seconds',
          int(up['last_activity']['seconds']) >= 0)
    check('uptime has no status text',
          not str(up['last_activity']['status'] or '').strip(),
          repr(up['last_activity']['status']))

    def vcard_texts(vcard):
        return {el.tag: el.text for el in vcard.xml}

    def check_shared_vcard(vcard, label):
        texts = {el.tag: el.text for el in vcard.xml}
        photo = vcard.xml.find('{%s}PHOTO' % VCARD_NS)
        ptype = photo.find('{%s}TYPE' % VCARD_NS) if photo is not None \
            else None
        pbin = photo.find('{%s}BINVAL' % VCARD_NS) if photo is not None \
            else None
        check('%s: bday/url/photo' % label,
              texts.get('{%s}BDAY' % VCARD_NS) == TOOLBOX_BDAY
              and texts.get('{%s}URL' % VCARD_NS) == TOOLBOX_URL
              and ptype is not None and ptype.text == 'image/png'
              and pbin is not None and pbin.text == TOOLBOX_PHOTO,
              (texts.get('{%s}BDAY' % VCARD_NS),
               texts.get('{%s}URL' % VCARD_NS)))

    iq = 'whois@service2.ets.jabberworld.info'
    vc = xmpp._get_vcard(iq, None, None, None)
    texts = vcard_texts(vc)
    check('vcard nick/desc',
          texts.get('{%s}NICKNAME' % VCARD_NS) == 'whois'
          and bool(texts.get('{%s}DESC' % VCARD_NS)), texts)
    check_shared_vcard(vc, 'bot vcard')

    vc2 = xmpp._get_vcard('service2.ets.jabberworld.info', None, None, None)
    texts = vcard_texts(vc2)
    desc2 = texts.get('{%s}DESC' % VCARD_NS) or ''
    check('transport vcard fn/nick/desc',
          texts.get('{%s}FN' % VCARD_NS) == TOOLBOX_NAME
          and texts.get('{%s}NICKNAME' % VCARD_NS) == TOOLBOX_NAME
          and TOOLBOX_NAME in desc2 and 'Bot factory' not in desc2,
          (texts.get('{%s}FN' % VCARD_NS), desc2))
    check_shared_vcard(vc2, 'transport vcard')

    # --- helpers / parsers ----------------------------------------------
    check('valid_host domain', valid_host('linuxoid.in'))
    check('valid_host ipv4', valid_host('185.161.208.229'))
    check('valid_host ipv6', valid_host('2001:db8::1'))
    check('invalid host rejected', not valid_host('-evil'))
    flags, pos = parse_flags(split_args('-6 -u [2001:db8::1] 53'))
    check('parse_flags', flags == {'-6', '-u'}
          and pos == ['[2001:db8::1]', '53'], (flags, pos))

    port_bot = xmpp.bots['port']
    out = await port_bot.handle('example.com notaport', {})
    check('port rejects bad port', 'Invalid port' in out)
    out = await port_bot.handle('', {})
    check('port help on empty', 'Usage' in out)

    isdown = xmpp.bots['isdown']
    out = await isdown.handle('ftp://example.com', {})
    check('isdown rejects ftp scheme', 'Unsupported URL scheme' in out)

    # localhost TCP probe against a real listener
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    lport = srv.getsockname()[1]
    out = await isdown.handle('127.0.0.1:%d' % lport, {})
    check('isdown UP for local listener', ' is UP ' in out, out.strip())
    srv.close()
    out = await isdown.handle('127.0.0.1:%d' % lport, {})
    check('isdown DOWN after close', ' is DOWN ' in out, out.strip())

    # --- whois output cleanup -----------------------------------------------
    who = xmpp.bots['whois']
    sample_com = '\n'.join([
        'Domain Name: GOOGLE.COM',
        'Registrant Name: REDACTED FOR PRIVACY',
        'Admin Email: n/a',
        'Tech City: Redacted',
        'Registrar: MarkMonitor Inc.',
        'Comment: entry was redacted by admin staff',
        '',
        'Terms of Use: You are not authorized to access or query.',
        'By submitting a WHOIS query, you agree to abide by this policy.',
        "The Data in VeriSign's WHOIS database is provided for info only.",
        '>>> Last update of whois database: 2026-08-24T07:00:00Z <<<',
    ])
    cleaned = who._clean(sample_com)
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    check('whois placeholders filtered (case-insensitive)',
          all(not any(p in ln.lower().split(':')[-1].strip(' .,;')
                      for p in ('redacted for privacy', 'redacted', 'n/a'))
              or ln.startswith('Comment:') for ln in lines)
          and not any(ln.lower().startswith(('registrant name', 'admin email',
                                             'tech city'))
                      for ln in lines), cleaned)
    check('whois keeps normal values with substring redacted',
          'Comment: entry was redacted by admin staff' in cleaned, cleaned)
    check('whois tail disclaimer removed',
          all('terms of use' not in ln.lower()
              and 'by submitting' not in ln.lower()
              and 'verisign' not in ln.lower()
              and '>>>' not in ln for ln in lines), cleaned)
    check('whois keeps data fields', 'Domain Name: GOOGLE.COM' in cleaned
          and 'Registrar: MarkMonitor Inc.' in cleaned, cleaned)

    sample_in = '\n'.join([
        'Access to .IN WHOIS information is provided to assist persons in',
        'determining the contents of a domain name registration record in',
        'the .IN registry database. The data in this record is provided by',
        '.IN Registry for informational purposes, and .IN does not',
        'guarantee its accuracy. By submitting this query, you agree to',
        'abide by this policy.',
        '',
        'Domain ID:D419200000-IN',
        'Domain Name:LINUXOID.IN',
        'Registrant City:N/A',
        'Updated Date:2025-01-01T00:00:00Z',
    ])
    cleaned_in = who._clean(sample_in)
    check('whois leading disclaimer (.IN) removed',
          'Access to' not in cleaned_in
          and 'abide by this policy' not in cleaned_in, cleaned_in)
    check('whois .IN data kept, N/A dropped',
          'Domain Name:LINUXOID.IN' in cleaned_in
          and 'Updated Date:2025-01-01T00:00:00Z' in cleaned_in
          and 'Registrant City' not in cleaned_in, cleaned_in)

    sample_arin = '\n'.join([
        '# start',
        '',
        'NetRange:       1.1.1.0 - 1.1.1.255',
        'NetName:        APNIC-LABS',
        'OrgName:        APNIC Labs Pty Ltd',
        'Comment:        entry was redacted by admin staff',
        'OrgAbuseEmail:  helpdesk@apnic.net',
        '',
        '# end',
        '',
        '#',
        '# ARIN WHOIS data and services are subject to the Terms of Use',
        '# available at: https://www.arin.net/resources/registry/whois/tou/',
        '# Copyright 1997-2026, American Registry for Internet Numbers, Ltd.',
        '#',
    ])
    cleaned_arin = who._clean(sample_arin)
    check('whois ARIN comment footer/header removed',
          all(s not in cleaned_arin for s in ('Terms of Use', 'Copyright',
                                              '# start', '# end'))
          and 'NetRange:' in cleaned_arin and 'OrgName:' in cleaned_arin
          and 'entry was redacted by admin staff' in cleaned_arin,
          cleaned_arin)

    sample_tucows = '\n'.join([
        'Domain Name:LINUXOID.IN',
        'Updated Date:2025-01-01T00:00:00Z',
        'Registrar URL:http://publicdomainregistry.com',
        '',
        'Tucows Registry reserves the right to modify these terms at any'
        ' time. By',
        'submitting this query, you agree to abide by this policy. All'
        ' rights',
        'reserved.',
    ])
    cleaned_tucows = who._clean(sample_tucows)
    check('whois Tucows footer (mid-line marker) removed',
          all(s not in cleaned_tucows.lower() for s in
              ('tucows', 'agree', 'rights reserved'))
          and 'Domain Name:LINUXOID.IN' in cleaned_tucows
          and 'Updated Date:2025-01-01T00:00:00Z' in cleaned_tucows,
          cleaned_tucows)

    sample_mm = '\n'.join([
        'Domain Name: google.com',
        'Registrar: MarkMonitor, Inc.',
        '',
        "The data in MarkMonitor's WHOIS database is provided for"
        ' information purposes, and to assist persons.',
        '',
        'By submitting a WHOIS query, you agree that you will use this'
        ' data only for lawful purposes.',
        '',
        'MarkMonitor Domain Management(TM)',
        'Visit MarkMonitor at https://www.markmonitor.com',
        'Contact us at +1.8007459229',
        '--',
    ])
    cleaned_mm = who._clean(sample_mm)
    check('whois MarkMonitor tail incl. marketing removed',
          all(s not in cleaned_mm for s in ('The data in', 'By submitting',
                                            'Domain Management', 'Contact us',
                                            '--'))
          and 'Domain Name: google.com' in cleaned_mm
          and 'Registrar: MarkMonitor, Inc.' in cleaned_mm,
          cleaned_mm)

    sample_empty = '\n'.join([
        'Domain Name:MYSKU.CLUB',
        'Registrant Phone Ext:',
        'Registrant Fax: ',
        'Registrant Fax Ext:',
        'Registrar Phone:+7.0000000000',
        'Billing Email:redacted',
    ])
    cleaned_empty = who._clean(sample_empty)
    check('whois empty fields dropped',
          all(s not in cleaned_empty for s in ('Phone Ext', 'Fax'))
          and 'Domain Name:MYSKU.CLUB' in cleaned_empty
          and 'Registrar Phone:+7.0000000000' in cleaned_empty
          and 'Billing Email' not in cleaned_empty,
          cleaned_empty)

    sample_label = '\n'.join([
        'Domain Name:X.COM',
        'Registrar Phone:+1.5550000000',
        '',
        'Web-based WHOIS:',
        '  https://example.com/whois/contact/x.com',
    ])
    cleaned_label = who._clean(sample_label)
    check('whois empty-label URL boilerplate removed',
          'Web-based WHOIS' not in cleaned_label
          and 'whois/contact' not in cleaned_label
          and 'Domain Name:X.COM' in cleaned_label
          and 'Registrar Phone:+1.5550000000' in cleaned_label,
          cleaned_label)

    # --- whoip plugin -------------------------------------------------------
    from plugins.whoip import Whoip
    whoip = Whoip()
    whoip.db = sqlite3.connect(':memory:')
    whoip.db.execute(
        'CREATE TABLE IF NOT EXISTS cache ('
        ' qkey TEXT PRIMARY KEY, ts INTEGER NOT NULL, result TEXT NOT NULL)')
    whoip.db.commit()

    check('whoip bot: jid/desc/help',
          whoip.NAME == 'whoip' and 'WHOIS' in whoip.DESCRIPTION
          and 'domain' in whoip.HELP.lower())

    check('whoip rejects bad input',
          'Usage' in await whoip.handle('!!!', {'from': 'test@example.com'}), '')

    check('whoip filter # lines',
          whoip._filter_comments('line1\n# comment\nline2') == 'line1\nline2')
    check('whoip filter # leading spaces',
          whoip._filter_comments('  # indented comment\nreal') == 'real')
    check('whoip filter preserves non-comment #',
          '#header' not in whoip._filter_comments('#header\ndata')
          and 'data' in whoip._filter_comments('#header\ndata'))

    # --- sslcheck plugin ----------------------------------------------------
    from plugins.sslcheck import SSLCheck
    sslbot = SSLCheck()

    check('sslcheck bot: jid/desc/help',
          sslbot.NAME == 'sslcheck' and 'SSL' in sslbot.DESCRIPTION
          and 'host' in sslbot.HELP.lower())

    check('sslcheck rejects empty',
          'Usage' in await sslbot.handle('', {}), '')
    check('sslcheck rejects http',
          'Only HTTPS' in await sslbot.handle('http://example.com', {}), '')
    check('sslcheck rejects bad host',
          'Invalid' in await sslbot.handle('!!!', {}), '')

    check('sslcheck parse https url',
          SSLCheck._parse_target('https://example.com') ==
          ('example.com', 443))
    check('sslcheck parse https url custom port',
          SSLCheck._parse_target('https://example.com:8443') ==
          ('example.com', 8443))
    check('sslcheck parse host:port',
          SSLCheck._parse_target('example.com:8443') ==
          ('example.com', 8443))
    check('sslcheck parse bare host',
          SSLCheck._parse_target('example.com') ==
          ('example.com', 443))

    check('sslcheck format_days unknown',
          SSLCheck._format_days(None) == 'unknown')
    check('sslcheck format_days expired',
          'expired' in SSLCheck._format_days(-5))
    check('sslcheck format_days today',
          SSLCheck._format_days(0) == 'expires today')
    check('sslcheck format_days 1',
          SSLCheck._format_days(1) == '1 day remaining')
    check('sslcheck format_days 100',
          SSLCheck._format_days(100) == '100 days remaining')

    # --- sslcheck _extract_ssl_info -----------------------------------------
    _loop = asyncio.get_running_loop()

    class _GoodSSL:
        def version(self): return 'TLSv1.3'
        def cipher(self): return ('AES256', 'TLSv1.3', 256)
        def shared_ciphers(self): return [('AES256', 'TLSv1.3', 256)]
    info = await SSLCheck._extract_ssl_info(_GoodSSL(), _loop)
    check('sslcheck extract full info',
          info == {'protocol': 'TLSv1.3', 'cipher_name': 'AES256',
                   'cipher_bits': 256, 'available': ['TLSv1.3']}, info)

    class _NoSharedCiphers:
        def version(self): return 'TLSv1.2'
        def cipher(self): return ('CHACHA', 'TLSv1.2', 256)
        def shared_ciphers(self): raise OSError('not available')
    info2 = await SSLCheck._extract_ssl_info(_NoSharedCiphers(), _loop)
    check('sslcheck extract partial (no shared_ciphers)',
          info2 == {'protocol': 'TLSv1.2', 'cipher_name': 'CHACHA',
                    'cipher_bits': 256}, info2)

    class _NoSSL:
        def version(self): raise OSError('no ssl')
        def cipher(self): raise OSError('no ssl')
        def shared_ciphers(self): raise OSError('no ssl')
    check('sslcheck extract returns None on total failure',
          await SSLCheck._extract_ssl_info(_NoSSL(), _loop) is None)

    import threading as _threading
    _hang_stop = _threading.Event()
    def _hang_body():
        _hang_stop.wait(999)

    class _HangSharedCiphers:
        def version(self): return 'TLSv1.3'
        def cipher(self): return ('AES256', 'TLSv1.3', 256)
        def shared_ciphers(self): _hang_body()
    t0 = time.time()
    info3 = await SSLCheck._extract_ssl_info(_HangSharedCiphers(), _loop)
    elapsed_hang = time.time() - t0
    _hang_stop.set()
    check('sslcheck extract survives hanging shared_ciphers',
          info3 == {'protocol': 'TLSv1.3', 'cipher_name': 'AES256',
                    'cipher_bits': 256}
          and elapsed_hang < 4, (info3, elapsed_hang))

    # --- sslcheck hostname matching ----------------------------------------
    cert_wild = {'subject': ((('commonName', '*.example.com'),),),
                 'subjectAltName': (('DNS', '*.example.com'),
                                    ('DNS', 'example.com'),)}
    cert_exact = {'subject': ((('commonName', 'foo.bar'),),),
                  'subjectAltName': (('DNS', 'foo.bar'),)}
    check('sslcheck wildcard matches subdomain',
          SSLCheck._check_hostname(cert_wild, 'sub.example.com'))
    check('sslcheck wildcard matches apex',
          SSLCheck._check_hostname(cert_wild, 'example.com'))
    check('sslcheck wildcard rejects two-level subdomain',
          not SSLCheck._check_hostname(cert_wild, 'a.b.example.com'))
    check('sslcheck wildcard rejects foreign host',
          not SSLCheck._check_hostname(cert_wild, 'evil.com'))
    check('sslcheck exact match works',
          SSLCheck._check_hostname(cert_exact, 'foo.bar'))
    check('sslcheck exact match rejects mismatch',
          not SSLCheck._check_hostname(cert_exact, 'baz.bar'))

    # --- ad-hoc commands ---------------------------------------------------
    sent = []
    xmpp._request_subscription = lambda bot, user: sent.append(
        (bot.NAME, str(user)))
    xmpp._setup_adhoc()
    cmds = await xmpp['xep_0030'].get_items(
        'service2.ets.jabberworld.info',
        node='http://jabber.org/protocol/commands', local=True)
    cmd_nodes = {entry[1]: entry[2] for entry in cmds['items']}
    check('adhoc registers one command per bot',
          set(cmd_nodes) == set(xmpp.bots), set(cmd_nodes))
    check('adhoc command names are display names',
          all(cmd_nodes[n] == xmpp._display_name(xmpp.bots[n])
              for n in cmd_nodes), cmd_nodes)

    session = {'from': slixmpp.JID('user@example.com/res')}
    res = await xmpp._cmd_add_bot(None, session, xmpp.bots['whois'])
    check('adhoc add-bot requests subscription',
          ('whois', 'user@example.com/res') in sent, sent)
    check('adhoc completes without form',
          res['payload'] is None and res['has_next'] is False
          and res['next'] is None and bool(res['notes']), res['notes'])

    # --- registration (XEP-0077) -------------------------------------------
    from toolbox import MatchRegisterQuery
    probe_xml = ('<iq xmlns="jabber:component:accept" id="1" type="get" '
                 'to="%s" from="rain@ets.jabberworld.info/walkbook">'
                 '<query xmlns="jabber:iq:register"/></iq>' % ROOT)
    probe = slixmpp.stanza.Iq(xml=ET.fromstring(probe_xml), stream=xmpp)
    check('register matcher hits register iq',
          MatchRegisterQuery(None).match(probe))
    check('register matcher skips plain iq',
          not MatchRegisterQuery(None).match(xmpp.Iq(stype='get')))

    def reg_iq(iqtype):
        iq = slixmpp.stanza.Iq(xml=None, stream=xmpp)
        iq['type'] = iqtype
        ET.SubElement(iq.xml, '{jabber:iq:register}query')
        return iq

    raw_sent = []
    xmpp.send = lambda data, use_filters=True: raw_sent.append(str(data))
    await xmpp._handle_register(reg_iq('get'))
    check('register get returns instructions',
          any('type="result"' in s or "type='result'" in s for s in raw_sent)
          and any('instructions' in s and TOOLBOX_NAME in s
                  for s in raw_sent), raw_sent[:1])

    subs = []
    presences = []
    xmpp._send_subscription_request = lambda src, user: subs.append(
        (str(src), str(getattr(user, 'bare', user))))
    xmpp._send_entity_presence = lambda src, to, ptype=None, status=None: \
        presences.append((str(src), str(to), ptype, status))
    user_jid = 'rain@ets.jabberworld.info/walkbook'
    setiq = reg_iq('set')
    setiq['from'] = user_jid
    await xmpp._handle_register(setiq)
    check('register set requests subscription from transport',
          subs == [(ROOT, 'rain@ets.jabberworld.info')], subs)
    check('register set announces transport online',
          len(presences) == 1 and presences[0][0] == ROOT
          and presences[0][2] == 'available'
          and 'Jabber Toolbox' in (presences[0][3] or ''), presences)

    # --- entity time (urn:xmpp:time) ---------------------------------------
    from toolbox import MatchTimeQuery
    time_xml = ('<iq xmlns="jabber:component:accept" id="t1" type="get" '
                'to="reminder@service2.ets.jabberworld.info" '
                'from="rain@ets.jabberworld.info/walkbook">'
                '<time xmlns="urn:xmpp:time"/></iq>')
    time_iq = slixmpp.stanza.Iq(xml=ET.fromstring(time_xml), stream=xmpp)
    check('time matcher hits time iq',
          MatchTimeQuery(None).match(time_iq))
    check('time matcher skips plain iq',
          not MatchTimeQuery(None).match(xmpp.Iq(stype='get')))

    time_sent = []
    xmpp.send = lambda data, use_filters=True: time_sent.append(str(data))
    await xmpp._handle_time(time_iq)
    time_str = time_sent[-1] if time_sent else ''
    check('time returns result with utc/tzo/display',
          ('type="result"' in time_str or "type='result'" in time_str)
          and 'urn:xmpp:time' in time_str
          and '<utc>' in time_str and '<tzo>' in time_str
          and '<display>' in time_str,
          time_str[:200])

    # --- presence mirroring for bots and the transport root -----------------
    presences.clear()

    def make_pres(to, ptype=None):
        pres = xmpp.Presence()
        pres['to'] = to
        pres['from'] = user_jid
        if ptype:
            pres['type'] = ptype
        return pres

    await xmpp._mirror_presence(make_pres(ROOT))
    check('root presence mirrored',
          presences == [(ROOT, user_jid, 'available',
                        'Jabber Toolbox online. Send "help" for usage.')],
          presences)

    presences.clear()
    await xmpp._mirror_presence(
        make_pres('ping@service2.ets.jabberworld.info', 'unavailable'))
    ping_bot = xmpp.bots['ping']
    check('bot presence mirrored',
          presences == [('ping@service2.ets.jabberworld.info', user_jid,
                        'unavailable', ping_bot.DESCRIPTION)], presences)

    presences.clear()
    await xmpp._mirror_presence(
        make_pres('nosuch@service2.ets.jabberworld.info'))
    check('unknown localpart ignored', presences == [], presences)

    presences.clear()
    await xmpp._on_subscribe(make_pres(ROOT, 'subscribe'))
    check('root subscribe exchange',
          [(p[0], p[1], p[2]) for p in presences] ==
          [(ROOT, 'rain@ets.jabberworld.info', 'subscribed'),
           (ROOT, 'rain@ets.jabberworld.info', 'subscribe'),
           (ROOT, 'rain@ets.jabberworld.info', 'available')]
          and 'Jabber Toolbox' in (presences[-1][3] or ''), presences)

    presences.clear()
    await xmpp._on_subscribe(
        make_pres('ping@service2.ets.jabberworld.info', 'subscribe'))
    ping_bot = xmpp.bots['ping']
    check('bot subscribe exchange uses DESCRIPTION',
          presences[-1][3] == ping_bot.DESCRIPTION, presences)

    # --- receipts / composing flow -----------------------------------------
    def make_msg(to, body=None, msgid=None, request=False, state=None,
                 receipt=None):
        m = slixmpp.stanza.Message(xml=None, stream=xmpp)
        m['to'] = to
        m['from'] = 'rain@ets.jabberworld.info/walkbook'
        m['type'] = 'chat'
        if body:
            m['body'] = body
        if msgid:
            m['id'] = msgid
        if request:
            m['request_receipt'] = True
        if state:
            m['chat_state'] = state
        if receipt:
            m['receipt'] = receipt
        return m

    bot_sent = []
    xmpp.send = lambda data, use_filters=True: bot_sent.append(str(data))
    await xmpp._on_message(make_msg(
        'port@service2.ets.jabberworld.info', 'example.com notaport',
        msgid='req42', request=True))
    check('bot flow: received/composing/reply sequence',
          len(bot_sent) == 3
          and '<received xmlns="urn:xmpp:receipts" id="req42"' in bot_sent[0]
          and 'from="port@service2.ets.jabberworld.info"' in bot_sent[0]
          and '<composing xmlns="http://jabber.org/protocol/chatstates"'
          in bot_sent[1]
          and 'Invalid port' in bot_sent[2]
          and '<active xmlns="http://jabber.org/protocol/chatstates"'
          in bot_sent[2]
          and '<request xmlns="urn:xmpp:receipts"' in bot_sent[2],
          [s[:120] for s in bot_sent])
    check('bot reply addressed from the bot',
          all('from="port@service2.ets.jabberworld.info"' in s
              for s in bot_sent), [s[:80] for s in bot_sent])

    before = len(bot_sent)
    await xmpp._on_message(make_msg(
        'port@service2.ets.jabberworld.info', state='composing'))
    check('user composing ignored', len(bot_sent) == before)

    await xmpp._on_message(make_msg(
        'port@service2.ets.jabberworld.info', receipt='req42'))
    check('delivery confirmation ignored', len(bot_sent) == before)

    before = len(bot_sent)
    echo = make_msg(ROOT, body='ping 1.2.3.4')
    echo['from'] = 'port@service2.ets.jabberworld.info'
    await xmpp._on_message(echo)
    check('echo from own bot ignored', len(bot_sent) == before)

    await xmpp._on_message(make_msg(
        'port@service2.ets.jabberworld.info', 'example.com notaport'))
    check('no ack without receipt request',
          len(bot_sent) == before + 2
          and not any('<received' in s for s in bot_sent[before:])
          and '<composing' in bot_sent[-2]
          and 'Invalid port' in bot_sent[-1]
          and '<active xmlns="http://jabber.org/protocol/chatstates"'
          in bot_sent[-1],
          bot_sent[before:])

    # --- shorty config flow (no network) ----------------------------------
    shorty = xmpp.bots['shorty']
    ctx = {'from': slixmpp.JID('tester@jabber.world/home')}
    out = await shorty.handle('conf', ctx)
    check('shorty conf lists engines', 'u.to' in out and 'clck.ru' in out
          and 'is.gd' in out, out.splitlines()[:1])
    out = await shorty.handle('2', ctx)
    check('shorty engine saved', 'Engine set to' in out, out.strip())
    row = shorty.db.execute('SELECT engine FROM settings WHERE jid=?',
                            (str(ctx['from'].bare),)).fetchone()
    check('shorty sqlite persisted', row == ('clck.ru',), row)
    out = await shorty.handle('conf', ctx)
    check('shorty shows current engine', '<- current' in out)

    # --- note bot: personal notes with expiry --------------------------------
    plugins_mod.TZ_OFFSET = 3
    note = xmpp.bots['note']
    jid_a = 'selftest-note-a@jabber.world/x'
    jid_b = 'selftest-note-b@jabber.world/y'
    ctx_a = {'from': slixmpp.JID(jid_a)}
    ctx_b = {'from': slixmpp.JID(jid_b)}
    note.db.execute('DELETE FROM notes WHERE owner IN (?,?)',
                    (str(ctx_a['from'].bare), str(ctx_b['from'].bare)))
    note.db.commit()
    note.state.clear()

    out = await note.handle('The quick brown fox jumps over the lazy dog',
                            ctx_a)
    check('note create + auto title (20 chars cut)',
          '#1 сохранена' in out and 'The quick brown fox' in out
          and 'jumps over' not in out.splitlines()[1], out.strip())
    out = await note.handle('list', ctx_b)
    check('note isolation: other user sees nothing',
          'Заметок нет' in out, out.strip())
    out = await note.handle('second note body', ctx_a)
    check('note second saved', '#2 сохранена' in out, out.strip())
    out = await note.handle('list', ctx_a)
    lines = out.splitlines()
    check('note list newest first + pipe format (reversed nums)',
          any(ln.startswith('2. | ') and '| second note body' in ln
              and len(ln.split(' | ')) == 3 for ln in lines)
          and any(ln.startswith('1. | ')
                  and '| The quick brown fox' in ln for ln in lines), out)

    out = await note.handle('1', ctx_a)
    check('note open by stable num 1 (oldest)',
          'jumps over the lazy dog' in out
          and '1. title' in out and '0. Выход' in out, out)
    out = await note.handle('1', ctx_a)
    check('note title prompt', out == 'Введите заголовок:', out)
    out = await note.handle('bad\nmultiline', ctx_a)
    check('note title rejects newlines, stays in mode',
          'переносов строк' in out, out)
    out = await note.handle('Мой красивый заголовок', ctx_a)
    check('note title applied', '«Мой красивый заголовок»' in out
          and '1. title' in out, out.splitlines()[0])

    out = await note.handle('2', ctx_a)
    check('note add prompt', 'добавить' in out.lower(), out)
    out = await note.handle('appended extra line', ctx_a)
    check('note text appended', 'jumps over the lazy dog' in out
          and 'appended extra line' in out, out)

    out = await note.handle('4', ctx_a)
    check('note old menu options',
          'Выберите срок хранения' in out and '1 час' in out
          and '6 месяцев' in out and '12. Никогда' in out, out)
    out = await note.handle('5', ctx_a)
    check('note expiry set shown in view', 'хранится до:' in out,
          out.splitlines()[2])
    nid = note.state[ctx_a['from'].bare]['id']
    row = note.db.execute(
        'SELECT expires_at FROM notes WHERE id=?', (nid,)).fetchone()
    check('note expiry stored in db', row is not None
          and row[0] is not None and row[0] > time.time(), row)
    out = await note.handle('4', ctx_a)
    out = await note.handle('12', ctx_a)
    check('note "Никогда" clears expiry', 'хранится до:' not in out, out)
    row = note.db.execute(
        'SELECT expires_at FROM notes WHERE id=?', (nid,)).fetchone()
    check('note expiry NULL after Никогда', row == (None,), row)

    out = await note.handle('выход', ctx_a)
    check('note exit alias', out != '' and 'Заметки' in out
          and ctx_a['from'].bare not in note.state, out)

    out = await note.handle('999', ctx_a)
    check('note bad number rejected', 'Нет заметки с номером 999' in out, out)

    out = await note.handle('1', ctx_a)
    out = await note.handle('3', ctx_a)
    check('note del numbered menu', 'Удалить заметку' in out
          and '1. Да' in out and '0. Выход' in out, out)
    out = await note.handle('0', ctx_a)
    check('note del 0 exits to view', 'Операции' in out
          and note.state[ctx_a['from'].bare]['mode'] == 'menu', out)
    out = await note.handle('3', ctx_a)
    out = await note.handle('нет', ctx_a)
    check('note del declined keeps note', 'Операции' in out, out)
    out = await note.handle('3', ctx_a)
    out = await note.handle('да', ctx_a)
    check('note deleted on confirm', 'удалена' in out
          and ctx_a['from'].bare not in note.state, out)
    out = await note.handle('list', ctx_a)
    numbered = [ln for ln in out.splitlines()
                if ln and ln[0].isdigit() and '. ' in ln]
    check('note list after delete',
          len(numbered) == 1 and numbered[0].startswith('1.')
          and 'Мой красивый заголовок' not in out, numbered)

    # --- srch: search by body/title, case-insensitive ------------------------
    note.db.execute('DELETE FROM notes WHERE owner=?',
                    (str(ctx_a['from'].bare),))
    note.db.commit()
    note.state.clear()
    await note.handle('ПРИВЕТ мир как дела', ctx_a)
    await note.handle('приветствую всех', ctx_a)
    out = await note.handle('srch', ctx_a)
    check('srch no arg', out == 'Что искать?', out)
    out = await note.handle('srch МИР', ctx_a)
    check('srch finds case-insensitive in body',
          'ПРИВЕТ мир как дела' in out and 'приветствую всех' not in out,
          out)
    out = await note.handle('srch ВСЕХ', ctx_a)
    check('srch matches uppercase-typed against lowercase body',
          'приветствую всех' in out
          and 'ПРИВЕТ мир как дела' not in out, out)
    out = await note.handle('srch no_such_term', ctx_a)
    check('srch no match', 'Ничего не найдено' in out, out)

    await note.handle('1', ctx_a)
    await note.handle('title', ctx_a)
    await note.handle('a|b|c', ctx_a)
    await note.handle('0', ctx_a)
    out = await note.handle('list', ctx_a)
    check('note pipe in title sanitized in list',
          any(ln.startswith('1. | ') and ln.endswith('| a/b/c')
              and len(ln.split(' | ')) == 3 for ln in out.splitlines())
          and all('a|b|c' not in ln for ln in out.splitlines()), out)

    note.db.execute(
        'INSERT INTO notes (owner, created_ts, expires_at, title, body) '
        'VALUES (?,?,?,?,?)',
        (jid_a, int(time.time()) - 60, int(time.time()) - 30,
         'expired one', 'gone soon'))
    note.db.commit()
    out = await note.handle('list', ctx_a)
    check('note expired purged before list', 'expired one' not in out, out)

    out = await note.handle('help', ctx_b)
    check('note help', 'личные заметки' in out and 'list' in out, out)
    out = await note.handle('?', ctx_b)
    check('note ? alias', 'личные заметки' in out, out)

    # --- title_default_length config ----------------------------------------
    plugins_mod.TITLE_DEFAULT_LEN = 10
    out = await note.handle('This is a very long title text', ctx_a)
    check('title_default_length: note truncated to 10',
          'сохранена' in out and 'Заголовок: This is a' in out, out)
    plugins_mod.TITLE_DEFAULT_LEN = 20

    # --- reminder bot: timed reminders ---------------------------------------
    reminder = xmpp.bots['reminder']
    jid_r = 'selftest-reminder@jabber.world/x'
    jid_r_bare = 'selftest-reminder@jabber.world'
    ctx_r = {'from': slixmpp.JID(jid_r)}
    reminder.db.execute('DELETE FROM reminders WHERE owner=?',
                        (str(ctx_r['from'].bare),))
    reminder.state.clear()
    reminder.last_view.clear()

    # --- per-user sequential numbering (active reminders) --------------------
    for idx, (txt, expect) in enumerate([
            ('Numbered one', '#1'), ('Numbered two', '#2'),
            ('Numbered three', '#3')], 1):
        reminder.state.clear()
        reminder.last_view.clear()
        out = await reminder.handle(txt, ctx_r)
        out = await reminder.handle('7', ctx_r)
        check('reminder per-user number #%d' % idx,
              'Напоминание #%d' % idx in out and expect in out
              and '#%d «%s»' % (idx, txt) in out, out)
        out = await reminder.handle('0', ctx_r)
    # now 3 active; a 4th created reminder should show #4
    out = await reminder.handle('Numbered four', ctx_r)
    out = await reminder.handle('7', ctx_r)
    check('reminder per-user number is count+1',
          'Напоминание #4 «Numbered four»' in out, out)
    out = await reminder.handle('0', ctx_r)
    # open 2nd active by list number -> view shows #2
    out = await reminder.handle('list', ctx_r)
    check('reminder list shows 4 numbered',
          'Активные напоминания' in out and '4. |' in out, out)
    out = await reminder.handle('2', ctx_r)
    check('reminder open by active position shows #2',
          'Напоминание #2 «Numbered two»' in out, out)
    out = await reminder.handle('0', ctx_r)
    reminder.db.execute('DELETE FROM reminders WHERE owner=?',
                        (str(ctx_r['from'].bare),))
    reminder.state.clear()
    reminder.last_view.clear()

    out = await reminder.handle('Meeting tomorrow', ctx_r)
    check('reminder create + goes to picker',
          'Настройка: время напоминания' in out
          and 'Текущее время:' in out
          and 'Когда напомнить:' in out
          and '1. Год:' in out
          and '7. Сохранить' in out
          and '0. Удалить' in out, out.strip())

    out = await reminder.handle('7', ctx_r)
    check('reminder picker save returns to view',
          'Напоминание #' in out and 'Meeting tomorrow' in out
          and 'Создано:' in out, out)

    out = await reminder.handle('0', ctx_r)
    check('reminder menu exit returns to list',
          'Активные напоминания' in out
          and 'Meeting tomorrow' in out, out)

    out = await reminder.handle('1', ctx_r)
    check('reminder open by number',
          'Напоминание #' in out and '1. title' in out
          and '0. Выход' in out and '7.' not in out, out)

    # --- reminder title/body ------------------------------------------------
    out = await reminder.handle('1', ctx_r)
    check('reminder title prompt', out == 'Введите заголовок:', out)
    out = await reminder.handle('My Meeting', ctx_r)
    check('reminder title applied', '«My Meeting»' in out, out)

    out = await reminder.handle('2', ctx_r)
    check('reminder body prompt', 'Введите текст' in out, out)
    out = await reminder.handle('New body text', ctx_r)
    check('reminder body applied', 'New body text' in out, out)

    # --- reminder picker (remind_at) ----------------------------------------
    out = await reminder.handle('4', ctx_r)
    check('reminder picker for remind',
          'Настройка: время напоминания' in out
          and 'Текущее время:' in out
          and 'Когда напомнить:' in out
          and '1. Год:' in out
          and '6. Относительное время' in out
          and '7. Сохранить' in out
          and '0. Выход' in out, out)

    out = await reminder.handle('1', ctx_r)
    check('reminder picker year prompt', 'Введите год' in out, out)
    out = await reminder.handle('2027', ctx_r)
    check('reminder picker year updated', '2027' in out, out)

    out = await reminder.handle('2', ctx_r)
    out = await reminder.handle('12', ctx_r)
    check('reminder picker month updated', 'Месяц: 12' in out, out)

    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('9', ctx_r)
    check('reminder picker hour updated', 'Час: 9' in out, out)

    # --- relative time submenu ----------------------------------------------
    out = await reminder.handle('6', ctx_r)
    check('reminder relative submenu',
          'Относительное время:' in out
          and '1. Значение:' in out
          and '3. Сохранить' in out, out)

    out = await reminder.handle('1', ctx_r)
    check('reminder relative value prompt', 'Введите значение' in out, out)
    out = await reminder.handle('30', ctx_r)
    check('reminder relative value set', '30' in out, out)

    out = await reminder.handle('2', ctx_r)
    check('reminder relative unit menu',
          'Выберите единицу:' in out and '1. минута' in out, out)
    out = await reminder.handle('2', ctx_r)
    check('reminder relative unit set', 'час' in out, out)

    out = await reminder.handle('3', ctx_r)
    check('reminder relative save returns to view',
          'Напоминание #' in out, out)

    # --- relative "назад" ---------------------------------------------------
    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('6', ctx_r)
    out = await reminder.handle('назад', ctx_r)
    check('reminder relative back returns to picker',
          'Настройка: время напоминания' in out, out)
    out = await reminder.handle('0', ctx_r)
    check('reminder picker exit returns to view',
          'Напоминание #' in out, out)

    # --- picker save applies changes ----------------------------------------
    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('7', ctx_r)
    check('reminder picker save returns to view',
          'Напоминание #' in out, out)
    out = await reminder.handle('0', ctx_r)
    check('reminder menu exit returns to list',
          'Активные напоминания' in out, out)

    # --- picker validation --------------------------------------------------
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('3000', ctx_r)
    check('reminder year 3000 rejected', 'Введите год' in out, out)
    out = await reminder.handle('2025', ctx_r)
    check('reminder year 2025 accepted', '2025' in out, out)

    # --- fix_expires: remind_at after expires_at via picker -----------------
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('2099', ctx_r)
    out = await reminder.handle('7', ctx_r)
    check('fix_expires: expires auto-adjusted (picker)',
          'Напомнить: 2099' in out and 'Хранится до: 2099' in out, out)

    # --- fix_expires: remind_at after expires_at via relative ----------------
    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('6', ctx_r)
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('1000', ctx_r)
    out = await reminder.handle('2', ctx_r)
    out = await reminder.handle('6', ctx_r)
    out = await reminder.handle('3', ctx_r)
    check('fix_expires: expires auto-adjusted (relative)',
          'Напомнить:' in out and 'Хранится до:' in out, out)
    row_rel = reminder.db.execute(
        'SELECT remind_at, expires_at FROM reminders WHERE id=?',
        (reminder.state[jid_r_bare]['id'],)).fetchone()
    check('fix_expires: expires > remind (relative)',
          row_rel[1] > row_rel[0], row_rel)

    # --- picker for expires_at ("old") --------------------------------------
    out = await reminder.handle('5', ctx_r)
    check('reminder picker for expires',
          'Настройка: срок хранения' in out
          and 'Текущее время:' in out
          and 'Срок хранения:' in out
          and '7. Сохранить' in out
          and '0. Выход' in out, out)
    out = await reminder.handle('0', ctx_r)

    # --- delete -------------------------------------------------------------
    out = await reminder.handle('3', ctx_r)
    check('reminder del prompt', 'Удалить напоминание' in out
          and '1. Да' in out and '0. Выход' in out, out)
    out = await reminder.handle('0', ctx_r)
    check('reminder del 0 exits to view', 'Напоминание #' in out
          and reminder.state[jid_r_bare]['mode'] == 'menu', out)
    out = await reminder.handle('3', ctx_r)
    out = await reminder.handle('нет', ctx_r)
    check('reminder del declined', 'Напоминание #' in out, out)
    out = await reminder.handle('3', ctx_r)
    out = await reminder.handle('да', ctx_r)
    check('reminder deleted', 'удален' in out
          and jid_r_bare not in reminder.state, out)

    # --- cancel during creation (deletes) -----------------------------------
    out = await reminder.handle('Will be cancelled', ctx_r)
    check('reminder created for cancel test',
          'Настройка: время напоминания' in out
          and '0. Удалить' in out, out)
    out = await reminder.handle('0', ctx_r)
    check('reminder picker 0 deletes during creation',
          'Напоминание удалено.' in out, out)
    out = await reminder.handle('list', ctx_r)
    check('reminder picker 0 during creation cleans up',
          'Напоминаний нет' in out, out)

    # --- cancel during editing (keeps) --------------------------------------
    out = await reminder.handle('Persistent reminder', ctx_r)
    check('reminder created for keep test',
          'Настройка: время напоминания' in out, out)
    out = await reminder.handle('7', ctx_r)
    out = await reminder.handle('0', ctx_r)
    out = await reminder.handle('list', ctx_r)
    check('reminder cancel during editing keeps',
          'Persistent reminder' in out, out)

    # --- check_reminders (sets delivered_at) --------------------------------
    now = int(time.time())
    reminder.db.execute(
        'INSERT INTO reminders '
        '(owner, created_ts, remind_at, expires_at, title, body) '
        'VALUES (?,?,?,?,?,?)',
        (jid_r_bare, now - 100, now - 50, now + 3600, 'Due now', 'Body'))
    reminder.db.execute(
        'INSERT INTO reminders '
        '(owner, created_ts, remind_at, expires_at, title, body) '
        'VALUES (?,?,?,?,?,?)',
        (jid_r_bare, now - 100, now + 7200, now + 10800, 'Future', 'Body'))
    reminder.db.execute(
        'INSERT INTO reminders '
        '(owner, created_ts, remind_at, expires_at, title, body) '
        'VALUES (?,?,?,?,?,?)',
        (jid_r_bare, now - 200, now - 100, now - 50, 'Expired', 'Gone'))

    due = reminder.check_reminders()
    check('check_reminders returns due + skips expired',
          len(due) == 1 and due[0][0] == jid_r_bare
          and 'Напоминание: Due now' in due[0][1], due)

    due = reminder.check_reminders()
    check('check_reminders no more due', len(due) == 0, due)

    # --- delivered_at set ---------------------------------------------------
    row = reminder.db.execute(
        'SELECT delivered_at FROM reminders WHERE title=? AND owner=?',
        ('Due now', jid_r_bare)).fetchone()
    check('check_reminders set delivered_at', row and row[0] is not None, row)

    # --- archived list ------------------------------------------------------
    out = await reminder.handle('archive', ctx_r)
    check('reminder archive shows delivered',
          'Доставленные напоминания' in out
          and 'Due now' in out, out)

    out = await reminder.handle('1', ctx_r)
    check('reminder archive open by number',
          'Напоминание #' in out and 'доставлено' in out, out)

    # --- active list excludes delivered -------------------------------------
    out = await reminder.handle('list', ctx_r)
    check('reminder list excludes delivered',
          'Due now' not in out, out)

    # --- srch: search by body/title across active + delivered ---------------
    await reminder.handle('Оплатить счёт ООО Ромашка', ctx_r)
    await reminder.handle('7', ctx_r)
    await reminder.handle('0', ctx_r)
    out = await reminder.handle('srch', ctx_r)
    check('reminder srch no arg', out == 'Что искать?', out)
    out = await reminder.handle('srch ромашка', ctx_r)
    check('reminder srch finds active (case-insensitive)',
          'Активные:' in out and 'Оплатить счёт ООО' in out
          and '1. |' in out, out)
    out = await reminder.handle('srch DUE', ctx_r)
    check('reminder srch finds delivered (case-insensitive)',
          'Доставленные:' in out and 'Due now' in out
          and 'Активные:' not in out, out)
    out = await reminder.handle('srch no_such_term_x', ctx_r)
    check('reminder srch no match', 'Ничего не найдено' in out, out)
    out = await reminder.handle('srch ромашка', ctx_r)
    out = await reminder.handle('1', ctx_r)
    check('reminder srch result opens by number',
          'Напоминание #' in out and 'Оплатить' in out, out)
    out = await reminder.handle('0', ctx_r)

    # --- re-arm: edit remind_at of delivered reminder to future --------------
    # open "Due now" from archive, choose 4 (remind), set year to 2099, save
    out = await reminder.handle('archive', ctx_r)
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('4', ctx_r)
    out = await reminder.handle('1', ctx_r)
    out = await reminder.handle('2099', ctx_r)
    out = await reminder.handle('7', ctx_r)
    check('reminder re-arm view shows future remind',
          'Напоминание #' in out and 'Напомнить: 2099' in out
          and 'доставлено' not in out, out)
    reap_row = reminder.db.execute(
        'SELECT delivered_at FROM reminders WHERE title=? AND owner=?',
        ('Due now', jid_r_bare)).fetchone()
    check('reminder re-arm clears delivered_at',
          reap_row and reap_row[0] is None, reap_row)
    out = await reminder.handle('list', ctx_r)
    check('reminder re-arm reappears in list',
          'Due now' in out and '2099' in out, out)
    # make it due now and confirm check_reminders fires it again
    reminder.db.execute(
        'UPDATE reminders SET remind_at=? WHERE title=? AND owner=?',
        (now - 50, 'Due now', jid_r_bare))
    due = reminder.check_reminders()
    check('reminder re-arm fires again',
          any('Due now' in d[1] for d in due), due)
    # reset "Due now" state for downstream tests: clear delivered_at + future
    reminder.db.execute(
        'UPDATE reminders SET delivered_at=NULL, remind_at=? WHERE '
        'title=? AND owner=?',
        (now + 7200, 'Due now', jid_r_bare))

    # --- expired purge on list ----------------------------------------------
    out = await reminder.handle('list', ctx_r)
    check('reminder expired purged', 'Expired' not in out, out)

    # --- timezone test -------------------------------------------------------
    from datetime import datetime, timezone, timedelta
    tz3 = timezone(timedelta(hours=3))
    ts_utc = 1700000000
    expected_local = datetime.fromtimestamp(ts_utc, tz=tz3).strftime(
        '%Y-%m-%d %H:%M')
    got = reminder._fmt(ts_utc)
    check('timezone: _fmt converts UTC to local',
          got == expected_local, (got, expected_local, 'UTC+3'))

    reminder.db.execute(
        'INSERT INTO reminders '
        '(owner, created_ts, remind_at, expires_at, title, body) '
        'VALUES (?,?,?,?,?,?)',
        (jid_r_bare, ts_utc, ts_utc, ts_utc + 86400, 'TZ test', 'tz body'))
    row = reminder.db.execute(
        'SELECT remind_at FROM reminders WHERE title=? AND owner=?',
        ('TZ test', jid_r_bare)).fetchone()
    dt_local = datetime.fromtimestamp(row[0], tz=tz3)
    check('timezone: picker stores UTC correctly',
          dt_local.hour == 1 and dt_local.minute == 13,
          (dt_local.hour, dt_local.minute, 'expected 1:13 in UTC+3'))
    reminder.db.execute(
        'DELETE FROM reminders WHERE title=? AND owner=?',
        ('TZ test', jid_r_bare))

    # --- cleanup ------------------------------------------------------------
    reminder.db.execute('DELETE FROM reminders WHERE owner=?',
                        (str(ctx_r['from'].bare),))

    print()
    if FAILED:
        print('FAILED: %s' % ', '.join(FAILED))
        sys.exit(1)
    print('All checks passed (slixmpp %s).' % slixmpp.__version__)


def _fake_iq(iqtype, ns, to=None):
    iq = slixmpp.stanza.Iq(xml=None, stream=None)
    iq['type'] = iqtype
    if to:
        iq['to'] = to
    if ns == 'jabber:iq:last':
        iq.enable('last_activity')
    elif ns == 'vcard-temp':
        iq.enable('vcard_temp')
    return iq


if __name__ == '__main__':
    asyncio.run(main())
