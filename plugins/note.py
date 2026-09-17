"""note bot: personal text notes with optional expiry time."""

import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta

from plugins import Bot, BASE_DIR

TITLE_MAX = 100

EXPIRY_OPTIONS = (
    ('1 час', 3600),
    ('6 часов', 6 * 3600),
    ('12 часов', 12 * 3600),
    ('1 день', 24 * 3600),
    ('3 дня', 3 * 24 * 3600),
    ('1 неделя', 7 * 24 * 3600),
    ('2 недели', 14 * 24 * 3600),
    ('1 месяц', 30 * 24 * 3600),
    ('3 месяца', 90 * 24 * 3600),
    ('6 месяцев', 180 * 24 * 3600),
    ('1 год', 365 * 24 * 3600),
    ('Никогда', None),
)

MENU_HINT = ('Операции:\n'
             '1. title - задать заголовок заметки\n'
             '2. add - добавить текст к заметке\n'
             '3. del - удалить заметку\n'
             '4. old - установить срок хранения\n'
             '0. Выход')

MENU_KEYS = {
    '1': 'title', 'title': 'title',
    '2': 'add', 'add': 'add',
    '3': 'del', 'del': 'del',
    '4': 'old', 'old': 'old',
    '0': 'exit', 'exit': 'exit', 'выход': 'exit', 'quit': 'exit',
}

DEL_YES = ('да', 'yes', 'y', '1')
DEL_NO = ('0', 'нет', 'no', 'n', 'выход', 'exit')
CANCEL = ('0', 'отмена', 'cancel')


class Note(Bot):
    NAME = 'note'
    DESCRIPTION = ('Personal notes. Send any text to save it as a note, '
                   '"list" to see your notes.')
    HELP = ('Note bot - личные заметки.\n'
            'Отправьте любой текст - он будет сохранён как ваша заметка.\n'
            'Команды:\n'
            '  list      список заметок (новые сверху)\n'
            '  srch <текст>  поиск по заголовкам и содержимому\n'
            '  <номер>   открыть заметку\n'
            '  в открытой заметке: 1|title, 2|add, 3|del, 4|old, 0 - выход\n'
            '  help / ?  эта справка\n'
            'Срок хранения задаётся через old; по умолчанию заметка '
            'хранится бессрочно.')

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(
            os.path.join(BASE_DIR, 'note.sqlite3'),
            timeout=15, isolation_level=None)
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS notes ('
            ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' owner TEXT NOT NULL,'
            ' created_ts INTEGER NOT NULL,'
            ' expires_at INTEGER,'
            ' title TEXT NOT NULL,'
            ' body TEXT NOT NULL)')
        self.db.execute(
            'CREATE INDEX IF NOT EXISTS idx_notes_owner '
            'ON notes (owner, created_ts)')
        self.state = {}

    async def handle(self, text, ctx):
        jid = ctx['from'].bare
        body = text.strip()
        if not body:
            return self.HELP

        st = self.state.get(jid)
        if st is not None:
            lowered = body.lower()
            if lowered in CANCEL and st['mode'] != 'del':
                del self.state[jid]
                if st['mode'] == 'menu':
                    return self._list(jid)
                return 'Отменено.'
            handler = getattr(self, '_mode_' + st['mode'], None)
            if handler is not None:
                return handler(jid, st, body)

        lowered = body.lower()
        if lowered in ('help', '?'):
            return self.HELP
        if lowered == 'list':
            return self._list(jid)
        if lowered == 'srch':
            return 'Что искать?'
        if lowered.startswith('srch '):
            return self._search(jid, body[len('srch '):].strip())
        if body.isdigit():
            return self._open_by_number(jid, int(body))
        return self._create(jid, body)

    # --- idle actions -------------------------------------------------------

    def _purge(self, jid):
        cur = self.db.execute(
            'DELETE FROM notes WHERE owner=? '
            'AND expires_at IS NOT NULL AND expires_at < ?',
            (jid, int(time.time())))

    def _rows(self, jid):
        return self.db.execute(
            'SELECT id, title, created_ts, expires_at FROM notes '
            'WHERE owner=? ORDER BY created_ts DESC, id DESC',
            (jid,)).fetchall()

    def _list(self, jid):
        self._purge(jid)
        rows = self._rows(jid)
        if not rows:
            return ('Заметок нет. Отправьте мне любой текст, чтобы '
                    'сохранить заметку.')
        lines = ['Заметки (новые сверху):']
        for pos, (nid, title, created_ts, expires_at) in enumerate(rows, 1):
            stable_num = len(rows) - pos + 1
            line = '%d. | %s | %s' % (stable_num,
                                      self._fmt(created_ts),
                                      title.replace('|', '/'))
            if expires_at is not None:
                line += ' | до %s' % self._fmt(expires_at)
            lines.append(line)
        lines.append('Отправьте номер, чтобы открыть заметку.')
        return '\n'.join(lines)

    def _search(self, jid, query):
        self._purge(jid)
        rows = self._rows(jid)
        q = query.lower()
        matched = []
        for pos, (nid, title, created_ts, expires_at) in enumerate(rows, 1):
            row = self._load(jid, nid)
            if row is None:
                continue
            note_body = row[1]
            if q in title.lower() or q in note_body.lower():
                stable_num = len(rows) - pos + 1
                matched.append((stable_num, created_ts, title, expires_at))
        if not matched:
            return 'Ничего не найдено.'
        lines = ['Заметки с «%s»:' % query]
        for stable_num, created_ts, title, expires_at in matched:
            line = '%d. | %s | %s' % (stable_num,
                                      self._fmt(created_ts),
                                      title.replace('|', '/'))
            if expires_at is not None:
                line += ' | до %s' % self._fmt(expires_at)
            lines.append(line)
        lines.append('Отправьте номер, чтобы открыть заметку.')
        return '\n'.join(lines)

    def _open_by_number(self, jid, num):
        self._purge(jid)
        rows = self._rows(jid)
        if not 1 <= num <= len(rows):
            if not rows:
                return 'У вас пока нет заметок.'
            return 'Нет заметки с номером %d (доступно 1-%d).' % (
                num, len(rows))
        nid = rows[len(rows) - num][0]
        return self._view(jid, nid)

    def _create(self, jid, body):
        from plugins import TITLE_DEFAULT_LEN
        first_line = body.splitlines()[0].strip()
        title = first_line[:TITLE_DEFAULT_LEN].rstrip() or '(без заголовка)'
        cur = self.db.execute(
            'INSERT INTO notes (owner, created_ts, expires_at, title, body) '
            'VALUES (?,?,NULL,?,?)', (jid, int(time.time()), title, body))
        nid = cur.lastrowid
        pos = self.db.execute(
            'SELECT COUNT(*) FROM notes WHERE owner=?',
            (jid,)).fetchone()[0]
        return ('Заметка #%d сохранена.\nЗаголовок: %s\n'
                'Отправьте list для списка или номер, чтобы открыть.'
                % (pos, title))

    # --- note view / menu ---------------------------------------------------

    def _load(self, jid, nid):
        return self.db.execute(
            'SELECT title, body, created_ts, expires_at FROM notes '
            'WHERE id=? AND owner=?', (nid, jid)).fetchone()

    def _view(self, jid, nid):
        row = self._load(jid, nid)
        if row is None:
            self.state.pop(jid, None)
            return 'Заметка не найдена.'
        title, note_body, created_ts, expires_at = row
        meta = 'создана: %s' % self._fmt(created_ts)
        if expires_at is not None:
            meta += ', хранится до: %s' % self._fmt(expires_at)
        self.state[jid] = {'mode': 'menu', 'id': nid}
        return ('Заметка #%s «%s»\n%s\n%s\n%s\n%s\n---\n%s'
                % (nid, title, meta, '-' * 30,
                   note_body, '-' * 30, MENU_HINT))

    def _mode_menu(self, jid, st, body):
        action = MENU_KEYS.get(body.lower())
        nid = st['id']
        if action == 'title':
            self.state[jid] = {'mode': 'title', 'id': nid}
            return 'Введите заголовок:'
        if action == 'add':
            self.state[jid] = {'mode': 'add', 'id': nid}
            return 'Введите текст, который нужно добавить к заметке:'
        if action == 'del':
            row = self._load(jid, nid)
            if row is None:
                del self.state[jid]
                return 'Заметка не найдена.'
            self.state[jid] = {'mode': 'del', 'id': nid}
            return ('Удалить заметку «%s»?\n'
                    '1. Да\n'
                    '0. Выход' % row[0].replace('|', '/'))
        if action == 'old':
            self.state[jid] = {'mode': 'old', 'id': nid}
            lines = ['Выберите срок хранения:']
            for idx, (label, _) in enumerate(EXPIRY_OPTIONS, 1):
                lines.append('%d. %s' % (idx, label))
            return '\n'.join(lines)
        if action == 'exit':
            del self.state[jid]
            return self._list(jid)
        return ('Не понял. Доступно: 1|title, 2|add, 3|del, 4|old, '
                '0 - выход.')

    # --- sub-dialogs ----------------------------------------------------------

    def _mode_title(self, jid, st, body):
        if '\n' in body or '\r' in body:
            return ('В заголовке не должно быть переносов строк. '
                    'Введите заголовок:')
        title = ' '.join(body.split())
        if not title:
            return 'Заголовок не может быть пустым. Введите заголовок:'
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX]
        cur = self.db.execute(
            'UPDATE notes SET title=? WHERE id=? AND owner=?',
            (title, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Заметка не найдена.'
        return self._view(jid, st['id'])

    def _mode_add(self, jid, st, body):
        row = self._load(jid, st['id'])
        if row is None:
            del self.state[jid]
            return 'Заметка не найдена.'
        merged = row[1].rstrip('\n') + '\n' + body
        self.db.execute('UPDATE notes SET body=? WHERE id=? AND owner=?',
                        (merged, st['id'], jid))
        return self._view(jid, st['id'])

    def _mode_del(self, jid, st, body):
        lowered = body.lower()
        if lowered in DEL_YES:
            cur = self.db.execute(
                'DELETE FROM notes WHERE id=? AND owner=?',
                (st['id'], jid))
            del self.state[jid]
            if not cur.rowcount:
                return 'Заметка не найдена.'
            return 'Заметка удалена.'
        if lowered in DEL_NO:
            return self._view(jid, st['id'])
        return ('Ответьте 1 (Да) или 0 (Выход): удалить заметку?')

    def _mode_old(self, jid, st, body):
        if not body.isdigit() or not 1 <= int(body) <= len(EXPIRY_OPTIONS):
            return 'Выберите номер варианта от 1 до %d.' % len(EXPIRY_OPTIONS)
        label, delta = EXPIRY_OPTIONS[int(body) - 1]
        expires_at = None if delta is None else int(time.time()) + delta
        cur = self.db.execute(
            'UPDATE notes SET expires_at=? WHERE id=? AND owner=?',
            (expires_at, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Заметка не найдена.'
        return self._view(jid, st['id'])

    @staticmethod
    def _fmt(ts):
        from plugins import TZ_OFFSET
        tz = timezone(timedelta(hours=TZ_OFFSET))
        return datetime.fromtimestamp(ts, tz=tz).strftime('%Y-%m-%d %H:%M')
