"""piwigo bot: upload images sent as links (e.g. XEP-0363 HTTP Upload
files) into a user's Piwigo gallery using the Piwigo Web API."""

import asyncio
import http.cookiejar
import json
import mimetypes
import os
import re
import sqlite3
import urllib.parse
import urllib.request

from plugins import Bot, BASE_DIR, valid_host, Reply, numbered

USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/139.0 Safari/537.36 JabberToolbox')
API_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 120.0
MAX_NAME_LEN = 180

FIELDS = {'host', 'user', 'pass'}

DEL_YES = ('да', 'yes', 'y', '1')
DEL_NO = ('0', 'нет', 'no', 'n', 'выход', 'exit')

REG_RESPONSES = (('host', 'host'), ('user', 'user'), ('pass', 'pass'),
                 ('save', 'save'), ('0', '0'))
UPLOAD_RESPONSES = (('name', 'name'), ('album', 'album'), ('save', 'save'),
                    ('0', '0'))
CONFIRM_RESPONSES = (('1', '1'), ('0', '0'))

_URI_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)


class PiwigoError(Exception):
    pass


def _load_json(raw):
    try:
        return json.loads(raw.decode('utf-8', errors='replace'))
    except (ValueError, TypeError):
        return None


def _multipart(fields, files):
    import uuid
    boundary = '----JabberToolbox%s' % uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append('--%s\r\n'
                     'Content-Disposition: form-data; name="%s"\r\n\r\n'
                     '%s\r\n' % (boundary, key, value))
    for key, (fname, data, ctype) in files.items():
        parts.append('--%s\r\n'
                     'Content-Disposition: form-data; name="%s"; '
                     'filename="%s"\r\n'
                     'Content-Type: %s\r\n\r\n'
                     % (boundary, key, fname, ctype))
    body = ''.join(parts).encode('utf-8')
    body += ('--%s--\r\n' % boundary).encode('utf-8')
    return body, 'multipart/form-data; boundary=%s' % boundary


def _clean_filename(text):
    name = ' '.join(text.split())
    if not name:
        return ''
    name = re.sub(r'[\x00-\x1f"<>|:*?]', '_', name)
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    if len(name) > MAX_NAME_LEN:
        stem, ext = os.path.splitext(name)
        name = (stem[:MAX_NAME_LEN - len(ext)] + ext) if ext \
            else name[:MAX_NAME_LEN]
    return name


def _default_filename(url):
    path = urllib.parse.urlsplit(url).path
    name = os.path.basename(path) or 'image'
    name = urllib.parse.unquote(name)
    name = _clean_filename(name) or 'image'
    return name


def _flatten_categories(items):
    out = []
    seen = set()

    def walk(container, depth):
        if isinstance(container, dict):
            values = container.values()
        elif isinstance(container, list):
            values = container
        else:
            return
        for node in values:
            if not isinstance(node, dict):
                continue
            cid = node.get('id')
            if cid is None:
                children = node.get('children')
                walk(children, depth)
                continue
            label = node.get('full_name') or node.get('name') or ''
            key = str(cid)
            if key == '0' and not label:
                children = node.get('children')
                walk(children, depth)
                continue
            if key not in seen:
                seen.add(key)
                out.append((key, str(label).strip() or '?', depth))
            children = node.get('children')
            if isinstance(children, dict):
                direct = children.get('direct')
                walk(direct if direct is not None else children, depth + 1)
            elif isinstance(children, list):
                walk(children, depth + 1)

    walk(items, 0)
    return out


class _PiwigoSession:
    def __init__(self, host):
        self.base = (host or '').rstrip('/')
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.token = None

    def _post(self, fields, files=None, timeout=API_TIMEOUT):
        if files:
            body, ctype = _multipart(fields, files)
        else:
            body = urllib.parse.urlencode(fields).encode('utf-8')
            ctype = 'application/x-www-form-urlencoded'
        req = urllib.request.Request(self.base + '/ws.php?format=json',
                                     data=body,
                                     headers={'Content-Type': ctype})
        req.add_header('User-Agent', USER_AGENT)
        with self.opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
        return _load_json(raw)

    def _call(self, method, fields=None, files=None, timeout=API_TIMEOUT):
        data = dict(fields or {})
        data['method'] = method
        doc = self._post(data, files, timeout)
        if not isinstance(doc, dict):
            raise PiwigoError('Неожиданный ответ сервера.')
        if doc.get('stat') != 'ok':
            msg = doc.get('message') or doc.get('err') or ''
            raise PiwigoError(msg or 'Piwigo: ошибка %s' % method)
        return doc.get('result')

    def login(self, username, password):
        self._call('pwg.session.login',
                   {'username': username, 'password': password})

    def _download(self, url):
        req = urllib.request.Request(url)
        req.add_header('User-Agent', USER_AGENT)
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            return resp.read()

    def fetch_albums(self, username, password):
        self.login(username, password)
        res = self._call('pwg.categories.getList',
                         {'recursive': '1', 'full_name': '1'})
        if isinstance(res, dict) and 'categories' in res:
            items = res['categories']
        elif isinstance(res, dict):
            items = res
        elif isinstance(res, list):
            items = res
        else:
            items = []
        return _flatten_categories(items)

    def upload_from_url(self, url, filename, category, username, password):
        self.login(username, password)
        data = self._download(url)
        fields = {'name': filename} if filename else {}
        if category:
            fields['category'] = str(category)
        mime = mimetypes.guess_type(filename or '')[0] \
            or 'application/octet-stream'
        res = self._call('pwg.images.addSimple', fields,
                         {'image': (filename or 'image', data, mime)},
                         timeout=UPLOAD_TIMEOUT)
        res = res if isinstance(res, dict) else {}
        return res.get('image_id'), res.get('url')


class Piwigo(Bot):
    NAME = 'piwigo'
    QUICK_RESPONSES = True
    DESCRIPTION = ('Загрузка изображений в галерею Piwigo. '
                   'Отправьте ссылку на картинку (например, из HTTP '
                   'Upload) - бот предложит имя файла и альбом.')
    HELP = ('Piwigo bot - загрузка изображений в галерею Piwigo.\n'
            'Отправьте ссылку на изображение (например, файл, '
            'переданный jabber-клиентом через HTTP Upload).\n'
            'Команды:\n'
            '  reg       зарегистрировать учётную запись Piwigo\n'
            '  help / ?  эта справка')

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(
            os.path.join(BASE_DIR, 'piwigo.sqlite3'),
            timeout=15, isolation_level=None)
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS accounts ('
            ' jid TEXT PRIMARY KEY,'
            ' host TEXT NOT NULL,'
            ' user TEXT NOT NULL,'
            ' pass TEXT NOT NULL)')
        self.state = {}

    def _make_session(self, host):
        return _PiwigoSession(host)

    # --- helpers ------------------------------------------------------------

    def _account(self, jid):
        return self.db.execute(
            'SELECT host, user, pass FROM accounts WHERE jid=?',
            (jid,)).fetchone()

    def _help(self, jid):
        if self._account(jid) is not None:
            reg_line = '  unreg     удалить учётную запись Piwigo\n'
            responses = (('unreg', 'unreg'), ('help', 'help'))
        else:
            reg_line = '  reg       зарегистрировать учётную запись Piwigo\n'
            responses = (('reg', 'reg'), ('help', 'help'))
        text = ('Piwigo bot - загрузка изображений в галерею Piwigo.\n'
                'Отправьте ссылку на изображение (например, файл, '
                'переданный jabber-клиентом через HTTP Upload) - бот\n'
                'предложит имя файла и альбом и загрузит картинку.\n'
                'Команды:\n'
                '%s'
                '  help / ?  эта справка' % reg_line)
        return Reply(text, responses)

    def help_reply(self, jid):
        return self._help(jid)

    def _err(self, exc):
        if isinstance(exc, PiwigoError):
            return str(exc) or 'ошибка Piwigo'
        return str(exc) or type(exc).__name__

    def _normalize_host(self, raw):
        text = raw.strip()
        if not text:
            return None
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', text):
            text = 'https://' + text
        try:
            parts = urllib.parse.urlsplit(text)
        except ValueError:
            return None
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return None
        if not valid_host(parts.hostname):
            return None
        return '%s://%s%s' % (parts.scheme, parts.netloc, parts.path.rstrip('/'))

    # --- main entry ----------------------------------------------------------

    async def handle(self, text, ctx):
        jid = ctx['from'].bare
        body = text.strip()
        if not body:
            return self.help_reply(jid)

        lowered = body.lower()
        st = self.state.get(jid)
        if st is not None:
            if lowered in ('0', 'отмена', 'cancel', 'выход', 'exit'):
                return self._cancel(jid, st)
            handler = getattr(self, '_mode_' + st['mode'], None)
            if handler is not None:
                result = handler(jid, st, body)
                if asyncio.iscoroutine(result):
                    result = await result
                return result

        if lowered in ('help', '?'):
            return self._help(jid)
        if lowered == 'reg':
            return self._reg_start(jid)
        if lowered == 'unreg':
            return self._unreg_start(jid)
        return self._start_upload(jid, body)

    def _cancel(self, jid, st):
        if st['mode'] in ('album', 'filename'):
            st['mode'] = 'menu'
            return self._fmt_upload(st)
        del self.state[jid]
        return 'Отменено.'

    # --- registration ---------------------------------------------------------

    def _fmt_reg(self, st):
        host = st['host'] or '<не задан>'
        user = st['user'] or '<не задан>'
        pw = ('****' if st['pass'] else '<не задан>')
        return Reply('Регистрация в Piwigo\n'
                     '  Host: %s\n'
                     '  Пользователь: %s\n'
                     '  Пароль: %s\n'
                     '%s\n'
                     '1. host - задать адрес галереи\n'
                     '2. user - задать имя пользователя\n'
                     '3. pass - задать пароль\n'
                     '4. Сохранить\n'
                     '0. Отмена' % (host, user, pw, '-' * 22),
                     REG_RESPONSES)

    def _reg_start(self, jid):
        if self._account(jid) is not None:
            return Reply('Учётная запись уже есть. Отправьте unreg, '
                         'чтобы её удалить.', (('unreg', 'unreg'),))
        self.state[jid] = {'mode': 'reg', 'host': '', 'user': '', 'pass': '',
                           'field': ''}
        return self._fmt_reg(self.state[jid])

    def _mode_reg(self, jid, st, body):
        lowered = body.lower()
        if lowered in ('1', 'host'):
            st['field'] = 'host'
            st['mode'] = 'reg_val'
            return ('Введите адрес галереи Piwigo, например '
                    'https://gallery.example.com:')
        if lowered in ('2', 'user'):
            st['field'] = 'user'
            st['mode'] = 'reg_val'
            return 'Введите имя пользователя:'
        if lowered in ('3', 'pass'):
            st['field'] = 'pass'
            st['mode'] = 'reg_val'
            return 'Введите пароль:'
        if lowered in ('4', 'save', 'сохранить'):
            return self._save_account(jid, st)
        return Reply('Не понял. Доступно: 1|host, 2|user, 3|pass, '
                     '4 - сохранить, 0 - отмена.', REG_RESPONSES)

    def _mode_reg_val(self, jid, st, body):
        field = st.get('field') or 'host'
        if field == 'host':
            host = self._normalize_host(body)
            if host is None:
                return ('Некорректный адрес. Введите host (например '
                        'https://gallery.example.com):')
            st['host'] = host
        elif field == 'user':
            user = ' '.join(body.split())
            if not user:
                return 'Имя пользователя не может быть пустым. Введите user:'
            st['user'] = user
        else:
            st['pass'] = body
        st['field'] = ''
        st['mode'] = 'reg'
        return self._fmt_reg(st)

    def _save_account(self, jid, st):
        if not st['host'] or not st['user'] or not st['pass']:
            form = self._fmt_reg(st)
            return Reply('Заполните все поля (1. host, 2. user, 3. pass).\n'
                         + form, form.responses)
        session = self._make_session(st['host'])

        async def verify():
            try:
                await asyncio.to_thread(session.login, st['user'], st['pass'])
            except Exception as exc:
                form = self._fmt_reg(st)
                return Reply('Не удалось проверить учётную запись: %s\n%s'
                             % (self._err(exc), form), form.responses)
            self.db.execute(
                'INSERT OR REPLACE INTO accounts (jid, host, user, pass) '
                'VALUES (?,?,?,?)', (jid, st['host'], st['user'], st['pass']))
            del self.state[jid]
            return Reply('Учётная запись сохранена: %s (%s).\n'
                         'Отправьте help для справки.'
                         % (st['host'], st['user']), (('help', 'help'),))

        return verify()

    def _unreg_start(self, jid):
        acc = self._account(jid)
        if acc is None:
            return Reply('У вас нет учётной записи Piwigo. Отправьте: reg',
                         (('reg', 'reg'),))
        self.state[jid] = {'mode': 'unreg'}
        return Reply('Удалить учётную запись Piwigo (%s, %s)?\n'
                     '1. Да\n'
                     '0. Отмена' % (acc[0], acc[1]), CONFIRM_RESPONSES)

    def _mode_unreg(self, jid, st, body):
        lowered = body.lower()
        if lowered in DEL_YES:
            self.db.execute('DELETE FROM accounts WHERE jid=?', (jid,))
            del self.state[jid]
            return 'Учётная запись удалена.'
        if lowered in DEL_NO:
            del self.state[jid]
            return 'Отменено.'
        return Reply('Ответьте 1 (Да) или 0 (Отмена).', CONFIRM_RESPONSES)

    # --- image upload flow -----------------------------------------------------

    def _fmt_upload(self, st):
        album = st.get('album_name') or '<не выбран>'
        return Reply('Загрузка изображения в Piwigo\n'
                     'Имя файла: %s\n'
                     'Альбом: %s\n'
                     '%s\n'
                     '1. Имя файла\n'
                     '2. Альбом\n'
                     '3. Сохранить\n'
                     '0. Отмена' % (st['filename'], album, '-' * 22),
                     UPLOAD_RESPONSES)

    def _start_upload(self, jid, body):
        first = body.split(None, 1)[0] if body else ''
        if not _URI_RE.match(first):
            return self._help(jid)
        if self._account(jid) is None:
            return Reply('У вас нет учётной записи Piwigo. Выполните: reg\n'
                         '(бот загружает изображения из ссылок в вашу '
                         'галерею Piwigo).', (('reg', 'reg'),))
        filename = _default_filename(first)
        self.state[jid] = {'mode': 'menu', 'url': first, 'filename': filename,
                           'album_id': None, 'album_name': None}
        return self._fmt_upload(self.state[jid])

    def _mode_menu(self, jid, st, body):
        lowered = body.lower()
        if lowered in ('1', 'имя файла', 'file', 'name'):
            st['mode'] = 'filename'
            return 'Введите имя файла:'
        if lowered in ('2', 'альбом', 'album'):
            return self._pick_album(jid, st)
        if lowered in ('3', 'save', 'сохранить'):
            st['mode'] = 'saving'
            return self._do_upload(jid, st)
        return self._fmt_upload(st)

    def _mode_filename(self, jid, st, body):
        if '\n' in body or '\r' in body:
            return ('В имени файла не должно быть переносов строк. '
                    'Введите имя файла:')
        name = _clean_filename(body)
        if not name:
            return 'Имя файла не может быть пустым. Введите имя файла:'
        st['filename'] = name
        st['mode'] = 'menu'
        return self._fmt_upload(st)

    def _fmt_albums(self, cats):
        lines = ['Альбомы:']
        for idx, (_, label, depth) in enumerate(cats, 1):
            lines.append('%d. %s%s' % (idx, '  ' * depth,
                                       label.replace('|', '/')))
        lines.append('0. Назад')
        return Reply('\n'.join(lines), numbered(len(cats)) + [('0', '0')])

    def _pick_album(self, jid, st):
        acc = self._account(jid)
        if acc is None:
            del self.state[jid]
            return Reply('Учётная запись не найдена. Выполните: reg',
                         (('reg', 'reg'),))
        host, user, pw = acc
        session = self._make_session(host)

        async def load():
            form = self._fmt_upload(st)
            try:
                cats = await asyncio.to_thread(session.fetch_albums, user, pw)
            except Exception as exc:
                return Reply('Ошибка получения списка альбомов: %s\n%s'
                             % (self._err(exc), form), form.responses)
            if not cats:
                return Reply('В галерее нет доступных альбомов.\n%s' % form,
                             form.responses)
            st['cats'] = cats
            st['mode'] = 'album'
            return self._fmt_albums(cats)

        return load()

    def _mode_album(self, jid, st, body):
        cats = st.get('cats') or []
        num = body.strip()
        if not num.isdigit() or not 1 <= int(num) <= len(cats):
            listing = self._fmt_albums(cats)
            return Reply('Нет альбома с номером %s (доступно 1-%d, 0 - назад).'
                         '\n%s' % (num or '?', len(cats), listing),
                         listing.responses)
        cid, label, _ = cats[int(num) - 1]
        st['album_id'], st['album_name'] = cid, label
        st['mode'] = 'menu'
        return self._fmt_upload(st)

    def _mode_saving(self, jid, st, body):
        return 'Загрузка выполняется, подождите...'

    def _do_upload(self, jid, st):
        acc = self._account(jid)
        if acc is None:
            del self.state[jid]
            return 'Учётная запись не найдена. Выполните: reg'
        host, user, pw = acc
        session = self._make_session(host)

        async def run():
            try:
                image_id, link = await asyncio.to_thread(
                    session.upload_from_url, st['url'], st['filename'],
                    st.get('album_id'), user, pw)
            except Exception as exc:
                st['mode'] = 'menu'
                form = self._fmt_upload(st)
                return Reply('Ошибка загрузки: %s\n%s' % (self._err(exc), form),
                             form.responses)
            del self.state[jid]
            if not link:
                base = host.rstrip('/')
                link = '%s/picture.php?id=%s' % (
                    base, image_id if image_id is not None else '')
            in_album = ''
            if st.get('album_name'):
                in_album = ' в альбом «%s»' % st['album_name']
            return Reply('Изображение «%s» загружено%s.\n%s\n'
                         'Отправьте help для справки.'
                         % (st['filename'], in_album, link),
                         (('help', 'help'),))

        return run()