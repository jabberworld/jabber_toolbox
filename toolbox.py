#!/usr/bin/env python3
"""Jabber Toolbox - an XMPP component.

The transport loads bot plugins from the ./plugins directory, exposes
them through service discovery (disco#items) and one ad-hoc command
per bot, handles subscription exchanges so that bots appear online/offline by
mirroring the user's own presence state, supports in-band registration
(XEP-0077) for the transport itself, and answers version (XEP-0092),
uptime (XEP-0012) and vCard (XEP-0054) queries. Bot conversations are
enhanced with delivery receipts (XEP-0184) and typing notifications
(XEP-0085): every bot message is acknowledged, shown as "composing"
while being processed, and answered with an active state plus its own
receipt request.
"""

import asyncio
import configparser
import logging
import os
import platform
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from functools import partial

import slixmpp
from slixmpp import JID
from slixmpp.componentxmpp import ComponentXMPP
from slixmpp.plugins.xep_0054.stanza import VCardTemp
from slixmpp.xmlstream import ET
from slixmpp.xmlstream.handler import CoroutineCallback
from slixmpp.xmlstream.matcher.base import MatcherBase

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from plugins import load_plugins  # noqa: E402
import plugins as plugins_mod  # noqa: E402

TOOLBOX_NAME = 'Jabber Toolbox'
TOOLBOX_VERSION = '1.0.0'
TOOLBOX_URL = 'https://jabberworld.info'
TOOLBOX_BDAY = '2026-08-24'
TOOLBOX_PHOTO = (
    'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEQUlEQVR42rWXA3B0SRSF'
    'J2vbtr3DdeG3bdu2bdu2jcJv27FtO7l7z9vct686GOxOqr6gp7vPaZ+YvPVlt9sfY7ox'
    'p5k8ppC5ygxlnlArP1VS8TDz4f8g/jZzrVevXrRq1Srau3cv7d+/n8aOHUtcHse8oTY4'
    'LRWrV6+exX8PZh7xUPwJ5sa0adPowIEDOosWLaJff/0V4t+W1HsTdaXREYhHR0eTr68v'
    'oTGX3WZsHhjo269fP2fi3zL4e6g0+hAjhzhMgFOnTlHLli1hZJ6bBm6tW7euQnH83bt3'
    'b8IyGRsOnTp1KsR1pk+fjkq73BD3cTgcENZp3rw5+phiFIeppUuXorxIb2yz2R7hgjsn'
    'T54UA7Jplv8XA9u3b6eGDRtqJkQc5bNnz0ZZgdqBHdM+Y8YMTTw4ONgTE5Fbt25VTWgz'
    'IeI7d+6k/v37o9+zZXWwgNnFLHPTBNq+z8TiRIm4yr59+2jWrFkyK92ddeiqCdT9gAmb'
    'P39+ueK7du3Spr5Tp07o6zrzmCtTulw14Uwcl87ixYu1Tbxs2TJauXIl4bNBgwZRkyZN'
    'CCeFeUfae2xCpl0VX7hwIfXo0YOwHEwUNib/BPeZAcyTqoZHJmTkqnifPn3weTg+l9MB'
    'nKu4byLLKI7TM2DAgOLRo0d3wcx4IOG+iZEjR2ri48ePp871LdS9kRnGmnpF3LDhsowm'
    'WrVqRd0amilmx/O0dMC3MDDYW+L6hsPIIb57925dfOe4LyF+mnnKq+Ky5trIa/8jnnnQ'
    'RGfnfwQDScxZZiXzndfEsebdapgpavbzlLHeBAOAzTxLvmvfoKPTP6W/frMlcLsfPBH0'
    'AeWJY7d3qm7RxLf3+pLODP9ITOjE7niari5912UTkmT6MrdwcQA8LEyses5x1LrXNNOS'
    '9t/KmmfHzX1UN5G89zFqW8dM19iAKyYkw91AkjGGCbxq8rCI+JAhQ4onTJjQmus3ZgaV'
    '5Mm46DmPU/oiEyWteVQzEbzxFWpVy+LUhKTXa2qGU1myZIlcr2HMs0offudHvcdL8gy1'
    'rWqm4KWvuG4C0RnpVRVUkHQUq9xw0sf3f/5qS7g8+l26POZdal3FUr6JX8WEkohFCK9Y'
    'ixYtECKMBvCqwUCUtHPJxCI2sedfE1dn4DPdxEfSMA+J2BAg47lsKgKDwQSeVNmYPu6Y'
    'CJr2irY3Avlnq8oWCpz6Kh0f/An62SmNCrHBykivUxCjxABOApfpBpyZgAHVROSs57Wf'
    'CfMeI5wcaXAVV6oi/o0ESEkyCBN4zxU9pzMBYCJq1nOaOEia/yj6ytfjOFOuODIcYlTj'
    'xo3RaICi5dJMBPC0izg4NUy7sg8aL6A3lNwu6RUBUjLcLXeTDEzwhovHmifMexwj18Rr'
    '/GnLxEDVym8w+I8F/zRg1IjOkl6vqRnODRMfMTux5kwec0AVN87EMOYmU8wUMGeZ7pJe'
    'vfH1N3Jwwo5CYCpWAAAAAElFTkSuQmCC')
VCARD_NS = 'vcard-temp'

log = logging.getLogger('toolbox')


class MatchRegisterQuery(MatcherBase):
    """Match iqs carrying a jabber:iq:register query child."""

    def match(self, stanza):
        return stanza.xml.find('{jabber:iq:register}query') is not None


class MatchTimeQuery(MatcherBase):
    """Match iqs carrying an urn:xmpp:time query child."""

    def match(self, stanza):
        return stanza.xml.find('{urn:xmpp:time}time') is not None


class Toolbox(ComponentXMPP):

    def __init__(self, jid, secret, host, port):
        super().__init__(jid=jid, secret=secret, host=host, port=port)
        self.start_time = time.time()
        self.bots = {}  # local part (lowercase) -> Bot instance

        self._setup_plugins()
        self._load_bots()
        self._setup_disco()
        self._setup_handlers()
        self._setup_register()

    # ------------------------------------------------------------------
    # Setup

    def _setup_plugins(self):
        self.register_plugin('xep_0030')  # Service Discovery
        self.register_plugin('xep_0004')  # Data Forms
        self.register_plugin('xep_0050')  # Ad-Hoc Commands
        self.register_plugin('xep_0092')  # Software Version
        self.register_plugin('xep_0012')  # Last Activity (uptime)
        self.register_plugin('xep_0054')  # vCard-Temp
        self.register_plugin('xep_0085')  # Chat State Notifications
        self.register_plugin('xep_0184')  # Message Delivery Receipts
        self['xep_0184'].auto_ack = False

        version = self['xep_0092']
        version.software_name = TOOLBOX_NAME
        version.version = TOOLBOX_VERSION
        version.os = 'Python %s / slixmpp %s' % (
            platform.python_version(), slixmpp.__version__)

        self['xep_0012'].api.register(self._get_uptime,
                                      'get_last_activity', default=True)
        self['xep_0054'].api.register(self._get_vcard,
                                      'get_vcard', default=True)

    def _load_bots(self):
        for bot in load_plugins(os.path.join(BASE_DIR, 'plugins')):
            name = (bot.NAME or '').strip().lower()
            if not name or not all(c.isalnum() or c in '-_' for c in name):
                log.warning('Skipping plugin with invalid NAME=%r',
                            getattr(bot, 'NAME', ''))
                continue
            bot.NAME = name
            bot.jid = '%s@%s' % (name, self.boundjid.domain)
            self.bots[name] = bot

    @staticmethod
    def _display_name(bot):
        return '%s - %s' % (bot.NAME, bot.DESCRIPTION)

    def _setup_disco(self):
        disco = self['xep_0030']
        root = str(self.boundjid)
        disco.add_identity(category='component', itype='generic',
                           name=TOOLBOX_NAME)
        for feature in ('jabber:iq:version', 'jabber:iq:last', 'vcard-temp',
                        'urn:xmpp:time', 'jabber:iq:register',
                        'http://jabber.org/protocol/commands'):
            disco.add_feature(feature)

        for bot in sorted(self.bots.values(), key=lambda b: b.NAME):
            name = self._display_name(bot)
            disco.add_identity(category='account', itype='generic',
                               name=name, jid=bot.jid)
            for feature in ('jabber:iq:version', 'jabber:iq:last',
                            'vcard-temp', 'urn:xmpp:time',
                            'urn:xmpp:receipts',
                            'http://jabber.org/protocol/chatstates'):
                disco.add_feature(feature, jid=bot.jid)
            disco.add_item(jid=bot.jid, name=name, ijid=root)

    def _setup_adhoc(self):
        # Must run after the session bind: XEP-0050's session_bind hook
        # resets the disco items of the commands node.
        for bot in sorted(self.bots.values(), key=lambda b: b.NAME):
            self['xep_0050'].add_command(
                jid=str(self.boundjid), node=bot.NAME,
                name=self._display_name(bot),
                handler=partial(self._cmd_add_bot, bot=bot))

    def _setup_handlers(self):
        self.add_event_handler('session_start', self._on_session_start)
        self.add_event_handler('message', self._on_message)
        self.add_event_handler('presence_available', self._mirror_presence)
        self.add_event_handler('presence_unavailable', self._mirror_presence)
        self.add_event_handler('presence_subscribe', self._on_subscribe)
        self.add_event_handler('presence_subscribed', self._on_subscribed)
        self.add_event_handler('presence_unsubscribe', self._on_unsubscribe)
        self.add_event_handler('presence_unsubscribed', self._on_unsubscribed)

    # ------------------------------------------------------------------
    # Ad-hoc commands: one per bot, adds the bot to the roster

    async def _cmd_add_bot(self, iq, session, bot):
        self._request_subscription(bot, session['from'])
        session['payload'] = None
        session['next'] = None
        session['has_next'] = False
        session['notes'] = [
            ('info', '%s has sent you a subscription request. Approve it '
                     'and the bot will show up as online.' % bot.jid)]
        return session

    def _request_subscription(self, bot, user):
        pres = self.Presence(sto=str(user.bare), sfrom=bot.jid)
        pres['type'] = 'subscribe'
        pres.send()

    # ------------------------------------------------------------------
    # In-band registration (XEP-0077)

    def _setup_register(self):
        self.register_handler(CoroutineCallback(
            'registration', MatchRegisterQuery('jabber:iq:register'),
            self._handle_register))
        self.register_handler(CoroutineCallback(
            'time', MatchTimeQuery('urn:xmpp:time'), self._handle_time))

    async def _handle_time(self, iq):
        reply = iq.reply()
        tz = timezone(timedelta(hours=plugins_mod.TZ_OFFSET))
        now = datetime.now(timezone.utc)
        local = now.astimezone(tz)
        el = reply.xml.find('{urn:xmpp:time}time')
        if el is None:
            el = ET.SubElement(reply.xml, '{urn:xmpp:time}time')
        utc = el.find('{urn:xmpp:time}utc')
        if utc is None:
            utc = ET.SubElement(el, '{urn:xmpp:time}utc')
        utc.text = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        tzo = el.find('{urn:xmpp:time}tzo')
        if tzo is None:
            tzo = ET.SubElement(el, '{urn:xmpp:time}tzo')
        offset = int(plugins_mod.TZ_OFFSET)
        tzo.text = '%s%02d:%02d' % ('+' if offset >= 0 else '-',
                                     abs(offset), 0)
        disp = el.find('{urn:xmpp:time}display')
        if disp is None:
            disp = ET.SubElement(el, '{urn:xmpp:time}display')
        disp.text = local.strftime('%Y-%m-%d %H:%M:%S')
        reply.send()

    def _build_register_reply(self, iq):
        reply = iq.reply()
        query = reply.xml.find('{jabber:iq:register}query')
        if query is None:
            query = ET.SubElement(reply.xml, '{jabber:iq:register}query')
        instructions = query.find('{jabber:iq:register}instructions')
        if instructions is None:
            instructions = ET.SubElement(query,
                                         '{jabber:iq:register}instructions')
        instructions.text = (
            'Submitting this form adds %s to your roster. Approve the '
            'following subscription request and it will come online.'
            % TOOLBOX_NAME)
        return reply

    async def _handle_register(self, iq):
        if iq['type'] == 'get':
            self._build_register_reply(iq).send()
        elif iq['type'] == 'set':
            user = iq['from'].bare
            self._send_subscription_request(str(self.boundjid), user)
            self._send_entity_presence(
                str(self.boundjid), user, ptype='available',
                status='%s online. Send "help" for usage.' % TOOLBOX_NAME)
            self._build_register_reply(iq).send()

    # ------------------------------------------------------------------
    # Presence: subscription exchange and status mirroring

    def _bot_for(self, jid):
        if jid is None:
            return None
        return self.bots.get(jid.user.lower())

    def _entity_info(self, jid):
        """Map a target JID to (source_jid, display_label): a bot, or the
        transport itself when addressed without a local part."""
        if jid is None:
            return None
        bot = self._bot_for(jid)
        if bot is not None:
            return bot.jid, '%s bot' % bot.NAME
        if JID(jid).bare == str(self.boundjid):
            return str(self.boundjid), TOOLBOX_NAME
        return None

    def _send_subscription_request(self, from_jid, user):
        pres = self.Presence(sto=str(getattr(user, 'bare', user)),
                             sfrom=str(from_jid))
        pres['type'] = 'subscribe'
        pres.send()

    def _request_subscription(self, bot, user):
        self._send_subscription_request(bot.jid, user)

    def _send_entity_presence(self, source_jid, to, ptype=None, status=None):
        pres = self.Presence(sto=str(to), sfrom=str(source_jid))
        if ptype and ptype != 'available':
            pres['type'] = ptype
        if status:
            pres['status'] = status
        pres.send()

    async def _on_session_start(self, event):
        self._setup_adhoc()
        log.info('%s %s connected as %s', TOOLBOX_NAME, TOOLBOX_VERSION,
                 self.boundjid)
        for bot in sorted(self.bots.values(), key=lambda b: b.NAME):
            log.info('Service online: %s (%s)', bot.jid, bot.DESCRIPTION)
        asyncio.ensure_future(self._check_reminders_loop())

    async def _mirror_presence(self, pres):
        """Echo the user's available/unavailable state back from the bot."""
        info = self._entity_info(pres['to'])
        if info is None:
            return
        bot = self._bot_for(pres['to'])
        status = (bot.DESCRIPTION or bot.HELP) if bot else (
            '%s online. Send "help" for usage.' % info[1])
        self._send_entity_presence(info[0], pres['from'],
                                   ptype=pres['type'] or 'available',
                                   status=status)

    async def _on_subscribe(self, pres):
        """User wants to subscribe to a bot: auto-approve and reciprocate."""
        info = self._entity_info(pres['to'])
        if info is None:
            return
        source, label = info
        user = pres['from'].bare
        bot = self._bot_for(pres['to'])
        status = (bot.DESCRIPTION or bot.HELP) if bot else (
            '%s online. Send "help" for usage.' % label)
        self._send_entity_presence(source, user, ptype='subscribed')
        self._send_entity_presence(source, user, ptype='subscribe')
        self._send_entity_presence(source, user, ptype='available',
                                   status=status)

    async def _on_subscribed(self, pres):
        """User approved our subscription request: go online for them."""
        info = self._entity_info(pres['to'])
        if info is None:
            return
        bot = self._bot_for(pres['to'])
        status = (bot.DESCRIPTION or bot.HELP) if bot else (
            '%s online. Send "help" for usage.' % info[1])
        self._send_entity_presence(
            info[0], pres['from'].bare, ptype='available',
            status=status)

    async def _on_unsubscribe(self, pres):
        info = self._entity_info(pres['to'])
        if info is None:
            return
        user = pres['from'].bare
        self._send_entity_presence(info[0], user, ptype='unsubscribed')
        self._send_entity_presence(info[0], user, ptype='unavailable')

    async def _on_unsubscribed(self, pres):
        info = self._entity_info(pres['to'])
        if info is None:
            return
        self._send_entity_presence(info[0], pres['from'].bare,
                                   ptype='unavailable')

    def _handle_probe(self, pres):
        """Answer presence probes with an available presence."""
        info = self._entity_info(pres['to'])
        if info is not None:
            bot = self._bot_for(pres['to'])
            status = (bot.DESCRIPTION or bot.HELP) if bot else (
                '%s online. Send "help" for usage.' % info[1])
            self._send_entity_presence(info[0], pres['from'],
                                       ptype='available',
                                       status=status)

    # ------------------------------------------------------------------
    # Messages -> bots

    def _ack_receipt(self, bot, msg):
        """Confirm receipt of a user's message on behalf of the bot."""
        if not msg['id'] or not msg['request_receipt']:
            return
        ack = self.Message(sto=str(msg['from']), sfrom=bot.jid)
        ack['type'] = msg['type'] if msg['type'] in ('chat', 'normal') \
            else 'chat'
        ack['receipt'] = msg['id']
        ack.send()

    def _send_chat_state(self, bot, to, state):
        st = self.Message(sto=str(to), sfrom=bot.jid)
        st['type'] = 'chat'
        st['chat_state'] = state
        st.send()

    def _from_self(self, jid):
        """True for messages echoed back from the transport or its bots."""
        try:
            return JID(jid).bare == str(self.boundjid) \
                or self._bot_for(jid) is not None
        except Exception:
            return False

    async def _on_message(self, msg):
        if msg['type'] in ('groupchat', 'error'):
            return
        if self._from_self(msg['from']):
            return
        if msg['receipt']:
            log.debug('Delivery confirmed by %s (receipt %s)',
                      msg['from'], msg['receipt'])
            return
        body = (msg['body'] or '').strip()
        if not body and msg['chat_state']:
            return
        bot = self._bot_for(msg['to'])

        if bot is None:
            reply = msg.reply()
            lines = ['%s v%s' % (TOOLBOX_NAME, TOOLBOX_VERSION),
                     'Available services:']
            for name in sorted(self.bots):
                bot_i = self.bots[name]
                lines.append('  %s - %s' % (bot_i.jid, bot_i.DESCRIPTION))
            lines.append('Run one of my ad-hoc commands to add a bot to '
                         'your roster, or add a bot JID directly.')
            reply['body'] = '\n'.join(lines)
            reply.send()
            return

        self._ack_receipt(bot, msg)
        ctx = {'from': msg['from'], 'to': msg['to']}
        try:
            if not body:
                out = bot.HELP
            else:
                self._send_chat_state(bot, msg['from'], 'composing')
                out = await bot.handle(body, ctx)
        except Exception as exc:
            log.exception('Bot %s failed to handle message', bot.NAME)
            out = 'Internal error: %s' % (str(exc) or
                                          type(exc).__name__)
        reply = msg.reply()
        reply['body'] = out or bot.HELP
        reply['chat_state'] = 'active'
        reply['request_receipt'] = True
        reply.send()

    # ------------------------------------------------------------------
    # Background tasks

    def _send_bot_message(self, bot, to, body):
        msg = self.Message(sto=str(to), sfrom=bot.jid)
        msg['type'] = 'chat'
        msg['body'] = body
        msg['chat_state'] = 'active'
        msg['request_receipt'] = True
        msg.send()

    async def _check_reminders_loop(self):
        while True:
            await asyncio.sleep(60)
            for bot in self.bots.values():
                if hasattr(bot, 'check_reminders'):
                    try:
                        due = bot.check_reminders()
                        for owner_jid, msg_text in due:
                            self._send_bot_message(bot, owner_jid, msg_text)
                    except Exception:
                        log.exception('check_reminders failed for %s',
                                      bot.NAME)

    # ------------------------------------------------------------------
    # Version / uptime / vCard responders

    def _get_uptime(self, jid, node, ifrom, iq):
        seconds = int(time.time() - self.start_time)
        if isinstance(iq, slixmpp.stanza.Iq):
            reply = iq.reply()
        else:
            reply = self.Iq(stype='result')
        reply['last_activity']['seconds'] = seconds
        return reply

    def _get_vcard(self, jid, node, ifrom, args):
        """Serve a vCard for any entity under the component domain.

        XEP-0054 (slixmpp 1.10) calls this with (target_bare_jid, None,
        requester_jid, None) and expects a VCardTemp stanza back; it
        performs the actual reply/send by itself.
        """
        try:
            bot = self._bot_for(JID(jid)) if jid else None
        except Exception:
            bot = None
        vcard = VCardTemp()

        def put(tag, text):
            el = vcard.xml.find('{%s}%s' % (VCARD_NS, tag))
            if el is None:
                el = ET.SubElement(vcard.xml, '{%s}%s' % (VCARD_NS, tag))
            el.text = text

        def put_photo():
            photo = vcard.xml.find('{%s}PHOTO' % VCARD_NS)
            if photo is None:
                photo = ET.SubElement(vcard.xml, '{%s}PHOTO' % VCARD_NS)
            ptype = photo.find('{%s}TYPE' % VCARD_NS)
            if ptype is None:
                ptype = ET.SubElement(photo, '{%s}TYPE' % VCARD_NS)
            ptype.text = 'image/png'
            pbin = photo.find('{%s}BINVAL' % VCARD_NS)
            if pbin is None:
                pbin = ET.SubElement(photo, '{%s}BINVAL' % VCARD_NS)
            pbin.text = TOOLBOX_PHOTO

        put('BDAY', TOOLBOX_BDAY)
        put('URL', TOOLBOX_URL)
        put_photo()

        if bot is not None:
            put('FN', bot.NAME)
            put('NICKNAME', bot.NAME)
            put('DESC', bot.DESCRIPTION or bot.HELP)
        else:
            services = ', '.join(sorted(self.bots)) or 'none loaded'
            put('FN', TOOLBOX_NAME)
            put('NICKNAME', TOOLBOX_NAME)
            put('DESC', '%s v%s. Services: %s' %
                (TOOLBOX_NAME, TOOLBOX_VERSION, services))
        return vcard


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise SystemExit('Cannot read config file: %s' % path)
    section = parser['server']
    settings = parser['settings'] if 'settings' in parser else {}
    return {
        'jid': section.get('jid'),
        'host': section.get('host'),
        'port': section.getint('port'),
        'secret': section.get('password'),
        'timezone': settings.getint('timezone', fallback=0),
        'title_default_length': settings.getint('titledefaultlength',
                                               fallback=20),
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-8s %(name)s: %(message)s')

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    cfg = load_config(os.path.join(BASE_DIR, 'config.ini'))
    plugins_mod.TZ_OFFSET = cfg['timezone']
    plugins_mod.TITLE_DEFAULT_LEN = cfg['title_default_length']
    xmpp = Toolbox(jid=cfg['jid'], secret=cfg['secret'],
                   host=cfg['host'], port=cfg['port'])

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, xmpp.disconnect)
        except NotImplementedError:
            pass

    log.info('Starting %s %s at %s:%d (component %s)',
             TOOLBOX_NAME, TOOLBOX_VERSION, cfg['host'], cfg['port'],
             cfg['jid'])
    xmpp.connect()
    try:
        loop.run_until_complete(xmpp.disconnected)
    finally:
        log.info('%s stopped; bots are offline.', TOOLBOX_NAME)


if __name__ == '__main__':
    main()
