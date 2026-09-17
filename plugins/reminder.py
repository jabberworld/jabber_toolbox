"""reminder bot: timed reminders with date/time picker."""

import calendar
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta

from plugins import Bot, BASE_DIR

TITLE_MAX = 100

RELATIVE_UNITS = (
    ('минута', 60),
    ('час', 3600),
    ('день', 86400),
    ('неделя', 604800),
    ('месяц', 2592000),
    ('год', 31536000),
)

MENU_HINT = ('Операции:\n'
             '1. title - задать заголовок\n'
             '2. body - редактировать текст\n'
             '3. del - удалить\n'
             '4. remind - изменить время напоминания\n'
             '5. old - изменить срок хранения\n'
             '\n'
             '0. Выход')

MENU_KEYS = {
    '1': 'title', 'title': 'title',
    '2': 'body', 'body': 'body',
    '3': 'del', 'del': 'del',
    '4': 'remind', 'remind': 'remind',
    '5': 'old', 'old': 'old',
    '0': 'cancel', 'cancel': 'cancel', 'выход': 'exit',
}

DEL_YES = ('да', 'yes', 'y', '1')
DEL_NO = ('0', 'нет', 'no', 'n', 'выход', 'exit')


class Reminder(Bot):
    NAME = 'reminder'
    DESCRIPTION = ('Timed reminders. Send any text to save it as a '
                   'reminder, "list" to see your reminders, '
                   '"arch" for delivered ones.')
    HELP = ('Reminder bot - напоминания.\n'
            'Отправьте текст - он будет сохранён как напоминание.\n'
        'Команды:\n'
        '  list      активные напоминания (ближайшие первые)\n'
        '  arch   доставленные напоминания\n'
        '  srch <текст>  поиск по заголовкам и тексту\n'
        '  <номер>   открыть напоминание\n'
        '  help / ?  эта справка\n'
            'По умолчанию напоминание через 1 час, хранится 7 дней.')

    def __init__(self):
        super().__init__()
        self.db = sqlite3.connect(
            os.path.join(BASE_DIR, 'reminder.sqlite3'),
            timeout=15, isolation_level=None)
        self.db.execute(
            'CREATE TABLE IF NOT EXISTS reminders ('
            ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' owner TEXT NOT NULL,'
            ' created_ts INTEGER NOT NULL,'
            ' remind_at INTEGER NOT NULL,'
            ' expires_at INTEGER NOT NULL,'
            ' title TEXT NOT NULL,'
            ' body TEXT NOT NULL)')
        self.db.execute(
            'CREATE INDEX IF NOT EXISTS idx_reminders_owner '
            'ON reminders (owner, remind_at)')
        self.db.execute(
            'CREATE INDEX IF NOT EXISTS idx_reminders_due '
            'ON reminders (remind_at, expires_at)')
        try:
            self.db.execute(
                'ALTER TABLE reminders ADD COLUMN delivered_at INTEGER')
        except sqlite3.OperationalError:
            pass
        self.state = {}
        self.last_view = {}
        self.last_search = {}

    async def handle(self, text, ctx):
        jid = ctx['from'].bare
        body = text.strip()
        if not body:
            return self.HELP

        lowered = body.lower()

        if lowered in ('help', '?'):
            return self.HELP
        if lowered == 'list':
            self.state.pop(jid, None)
            return self._list(jid)
        if lowered in ('arch', 'archive'):
            self.state.pop(jid, None)
            return self._archive(jid)
        if lowered == 'srch':
            self.state.pop(jid, None)
            return 'Что искать?'
        if lowered.startswith('srch '):
            self.state.pop(jid, None)
            return self._search(jid, body[len('srch '):].strip())

        st = self.state.get(jid)
        if st is not None:
            if lowered in ('0', 'отмена', 'cancel', 'выход', 'exit'):
                return self._handle_cancel(jid, st)
            if lowered in ('назад', 'back') and st['mode'] in (
                    'relative', 'relative_value', 'relative_unit'):
                return self._restore_picker(jid, st)
            handler = getattr(self, '_mode_' + st['mode'], None)
            if handler is not None:
                return handler(jid, st, body)

        if body.isdigit():
            return self._open_by_number(jid, int(body))
        return self._create(jid, body)

    # --- cancel / back ------------------------------------------------------

    def _handle_cancel(self, jid, st):
        mode = st['mode']
        if mode == 'menu':
            if st.get('creating'):
                self.db.execute(
                    'DELETE FROM reminders WHERE id=? AND owner=?',
                    (st['id'], jid))
            del self.state[jid]
            return self._list(jid)
        if mode in ('relative', 'relative_value', 'relative_unit'):
            return self._restore_picker(jid, st)
        if st.get('creating'):
            self.db.execute(
                'DELETE FROM reminders WHERE id=? AND owner=?',
                (st['id'], jid))
            del self.state[jid]
            return 'Напоминание удалено.'
        return self._view(jid, st['id'])

    def _restore_picker(self, jid, st):
        self.state[jid] = {
            'mode': 'picker', 'id': st['id'],
            'creating': st.get('creating', False),
            'target': st['target'],
            'year': st['year'],
            'month': st['month'],
            'day': st['day'],
            'hour': st['hour'],
            'minute': st['minute'],
        }
        return self._fmt_picker(st['id'], st['target'],
                                st['year'], st['month'], st['day'],
                                st['hour'], st['minute'],
                                creating=st.get('creating', False))

    # --- list / create ------------------------------------------------------

    def _purge(self, jid):
        self.db.execute(
            'DELETE FROM reminders WHERE owner=? AND expires_at < ?',
            (jid, int(time.time())))

    def _rows(self, jid):
        return self.db.execute(
            'SELECT id, title, created_ts, remind_at, expires_at '
            'FROM reminders WHERE owner=? AND delivered_at IS NULL '
            'ORDER BY remind_at ASC, id ASC',
            (jid,)).fetchall()

    def _archive_rows(self, jid):
        return self.db.execute(
            'SELECT id, title, created_ts, remind_at, expires_at, '
            'delivered_at '
            'FROM reminders WHERE owner=? AND delivered_at IS NOT NULL '
            'ORDER BY delivered_at DESC, id ASC',
            (jid,)).fetchall()

    def _user_number(self, jid, nid):
        row = self.db.execute(
            'SELECT remind_at, delivered_at FROM reminders '
            'WHERE id=? AND owner=?',
            (nid, jid)).fetchone()
        if row is None:
            return 0
        remind_at, delivered_at = row
        if delivered_at is None:
            count = self.db.execute(
                'SELECT COUNT(*) FROM reminders '
                'WHERE owner=? AND delivered_at IS NULL '
                'AND (remind_at < ? OR (remind_at = ? AND id < ?))',
                (jid, remind_at, remind_at, nid)).fetchone()[0]
        else:
            count = self.db.execute(
                'SELECT COUNT(*) FROM reminders '
                'WHERE owner=? AND delivered_at IS NOT NULL '
                'AND (delivered_at > ? OR (delivered_at = ? AND id < ?))',
                (jid, delivered_at, delivered_at, nid)).fetchone()[0]
        return count + 1


    def _list(self, jid):
        self._purge(jid)
        rows = self._rows(jid)
        self.last_view[jid] = 'list'
        if not rows:
            return ('Напоминаний нет. Отправьте мне любой текст, чтобы '
                    'создать напоминание.')
        lines = ['Активные напоминания (ближайшие первые):']
        for pos, (nid, title, created_ts, remind_at, expires_at) in \
                enumerate(rows, 1):
            line = '%d. | %s | %s' % (pos,
                                       self._fmt(remind_at),
                                       title.replace('|', '/'))
            lines.append(line)
        lines.append('Отправьте номер, чтобы открыть напоминание.')
        return '\n'.join(lines)

    def _open_by_number(self, jid, num):
        self._purge(jid)
        view = self.last_view.get(jid, 'list')
        if view == 'srch':
            rows = self.last_search.get(jid, [])
            if not 1 <= num <= len(rows):
                if not rows:
                    return 'Ничего не найдено.'
                return ('Нет совпадения с номером %d '
                        '(доступно 1-%d).' % (num, len(rows)))
            nid = rows[num - 1]
            return self._view(jid, nid)
        if view == 'arch':
            rows = self._archive_rows(jid)
        else:
            rows = self._rows(jid)
        if not 1 <= num <= len(rows):
            if not rows:
                if view == 'arch':
                    return 'Доставленных напоминаний нет.'
                return 'У вас нет напоминаний.'
            return 'Нет напоминания с номером %d (доступно 1-%d).' % (
                num, len(rows))
        nid = rows[num - 1][0]
        return self._view(jid, nid)

    def _archive(self, jid):
        self._purge(jid)
        rows = self._archive_rows(jid)
        self.last_view[jid] = 'arch'
        if not rows:
            return 'Доставленных напоминаний нет.'
        lines = ['Доставленные напоминания (новые первые):']
        for pos, (nid, title, created_ts, remind_at, expires_at,
                   delivered_at) in enumerate(rows, 1):
            line = '%d. | %s | %s' % (pos,
                                       self._fmt(delivered_at),
                                       title.replace('|', '/'))
            lines.append(line)
        lines.append('Отправьте номер, чтобы открыть напоминание.')
        return '\n'.join(lines)

    def _search(self, jid, query):
        self._purge(jid)
        q = query.lower()

        active = self._rows(jid)
        active_ids = []
        active_lines = []
        for nid, title, created_ts, remind_at, expires_at in active:
            row = self._load(jid, nid)
            if row is None:
                continue
            if q in row[0].lower() or q in row[1].lower():
                active_ids.append(nid)
                active_lines.append('%d. | %s | %s'
                                    % (len(active_lines) + 1,
                                       self._fmt(remind_at),
                                       title.replace('|', '/')))

        delivered = self._archive_rows(jid)
        delivered_ids = []
        delivered_lines = []
        for nid, title, created_ts, remind_at, expires_at, \
                delivered_at in delivered:
            row = self._load(jid, nid)
            if row is None:
                continue
            if q in row[0].lower() or q in row[1].lower():
                delivered_ids.append(nid)
                delivered_lines.append('%d. | %s | %s'
                                       % (len(active_lines) +
                                          len(delivered_lines) + 1,
                                          self._fmt(delivered_at),
                                          title.replace('|', '/')))

        if not active_ids and not delivered_ids:
            return 'Ничего не найдено.'

        lines = ['Найдено по «%s»:' % query]
        if active_lines:
            lines.append('Активные:')
            lines.extend(active_lines)
        if delivered_lines:
            lines.append('Доставленные:')
            lines.extend(delivered_lines)
        lines.append('Отправьте номер, чтобы открыть напоминание.')
        self.last_view[jid] = 'srch'
        self.last_search[jid] = active_ids + delivered_ids
        return '\n'.join(lines)

    def _create(self, jid, body):
        from plugins import TITLE_DEFAULT_LEN
        now = int(time.time())
        first_line = body.splitlines()[0].strip()
        title = first_line[:TITLE_DEFAULT_LEN].rstrip() or '(без заголовка)'
        remind_at = now + 3600
        expires_at = remind_at + 7 * 86400
        cur = self.db.execute(
            'INSERT INTO reminders '
            '(owner, created_ts, remind_at, expires_at, title, body) '
            'VALUES (?,?,?,?,?,?)',
            (jid, now, remind_at, expires_at, title, body))
        nid = cur.lastrowid
        tz = self._local_tz()
        dt = datetime.fromtimestamp(remind_at, tz=tz)
        self.state[jid] = {
            'mode': 'picker', 'id': nid, 'creating': True,
            'target': 'remind_at',
            'year': dt.year, 'month': dt.month, 'day': dt.day,
            'hour': dt.hour, 'minute': dt.minute,
        }
        return self._fmt_picker(nid, 'remind_at',
                                dt.year, dt.month, dt.day,
                                dt.hour, dt.minute, creating=True)

    # --- view / menu --------------------------------------------------------

    def _load(self, jid, nid):
        return self.db.execute(
            'SELECT title, body, created_ts, remind_at, expires_at, '
            'delivered_at '
            'FROM reminders WHERE id=? AND owner=?',
            (nid, jid)).fetchone()

    def _view(self, jid, nid):
        row = self._load(jid, nid)
        if row is None:
            self.state.pop(jid, None)
            return 'Напоминание не найдено.'
        title, body, created_ts, remind_at, expires_at, delivered_at = row
        self.state[jid] = {
            'mode': 'menu', 'id': nid,
            'creating': self.state.get(jid, {}).get('creating', False),
        }
        status = ''
        if delivered_at:
            status = '\nСтатус: доставлено %s' % self._fmt(delivered_at)
        return ('Напоминание #%d «%s»\n'
                'Создано: %s\n'
                'Напомнить: %s\n'
                'Хранится до: %s%s\n'
                '%s\n'
                '%s\n'
                '%s\n'
                '%s'
                % (self._user_number(jid, nid), title.replace('|', '/'),
                   self._fmt(created_ts),
                   self._fmt(remind_at),
                   self._fmt(expires_at), status,
                   '-' * 30,
                   body,
                   '-' * 30,
                   MENU_HINT))

    def _mode_menu(self, jid, st, body):
        action = MENU_KEYS.get(body.lower())
        nid = st['id']
        if action == 'title':
            self.state[jid] = {'mode': 'title', 'id': nid,
                               'creating': st.get('creating', False)}
            return 'Введите заголовок:'
        if action == 'body':
            self.state[jid] = {'mode': 'body', 'id': nid,
                               'creating': st.get('creating', False)}
            return 'Введите текст напоминания:'
        if action == 'del':
            row = self._load(jid, nid)
            if row is None:
                del self.state[jid]
                return 'Напоминание не найдено.'
            self.state[jid] = {'mode': 'del', 'id': nid,
                               'creating': st.get('creating', False)}
            return ('Удалить напоминание «%s»?\n'
                    '1. Да\n'
                    '0. Выход' % row[0].replace('|', '/'))
        if action == 'remind':
            return self._enter_picker(jid, st, 'remind_at')
        if action == 'old':
            return self._enter_picker(jid, st, 'expires_at')
        return ('Не понял. Доступно: 1|title, 2|body, 3|del, '
                '4|remind, 5|old, 0 - выход.')

    # --- title / body / del -------------------------------------------------

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
            'UPDATE reminders SET title=? WHERE id=? AND owner=?',
            (title, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Напоминание не найдено.'
        return self._view(jid, st['id'])

    def _mode_body(self, jid, st, body):
        cur = self.db.execute(
            'UPDATE reminders SET body=? WHERE id=? AND owner=?',
            (body, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Напоминание не найдено.'
        return self._view(jid, st['id'])

    def _mode_del(self, jid, st, body):
        lowered = body.lower()
        if lowered in DEL_YES:
            cur = self.db.execute(
                'DELETE FROM reminders WHERE id=? AND owner=?',
                (st['id'], jid))
            del self.state[jid]
            if not cur.rowcount:
                return 'Напоминание не найдено.'
            return 'Напоминание удалено.'
        if lowered in DEL_NO:
            return self._view(jid, st['id'])
        return 'Ответьте 1 (Да) или 0 (Выход): удалить напоминание?'

    # --- date/time picker ---------------------------------------------------

    def _enter_picker(self, jid, st, target):
        row = self._load(jid, st['id'])
        if row is None:
            del self.state[jid]
            return 'Напоминание не найдено.'
        _, _, _, remind_at, expires_at, _ = row
        ts = remind_at if target == 'remind_at' else expires_at
        tz = self._local_tz()
        dt = datetime.fromtimestamp(ts, tz=tz)
        self.state[jid] = {
            'mode': 'picker', 'id': st['id'],
            'creating': st.get('creating', False),
            'target': target,
            'year': dt.year, 'month': dt.month, 'day': dt.day,
            'hour': dt.hour, 'minute': dt.minute,
        }
        return self._fmt_picker(st['id'], target,
                                dt.year, dt.month, dt.day,
                                dt.hour, dt.minute,
                                creating=st.get('creating', False))

    def _fmt_picker(self, nid, target, year, month, day, hour, minute,
                    creating=False):
        now = int(time.time())
        now_str = self._fmt(now)
        if target == 'remind_at':
            label = 'Когда напомнить'
        else:
            label = 'Срок хранения'
        title = ('Настройка: время напоминания' if target == 'remind_at'
                 else 'Настройка: срок хранения')
        return ('%s\n'
                'Текущее время: %s\n'
                '%s: %04d-%02d-%02d %02d:%02d\n'
                '%s\n'
                '1. Год: %d\n'
                '2. Месяц: %d\n'
                '3. День: %d\n'
                '4. Час: %d\n'
                '5. Минута: %d\n'
                '\n'
                '6. Относительное время\n'
                '\n'
                '7. Сохранить\n'
                '0. %s'
                % (title, now_str, label, year, month, day, hour, minute,
                   '-' * 30,
                   year, month, day, hour, minute,
                   'Удалить' if creating else 'Выход'))

    def _mode_picker(self, jid, st, body):
        if body == '1':
            self.state[jid] = {**st, 'mode': 'picker_value',
                               'field': 'year'}
            return 'Введите год (2024-2099):'
        if body == '2':
            self.state[jid] = {**st, 'mode': 'picker_value',
                               'field': 'month'}
            return 'Введите месяц (1-12):'
        if body == '3':
            self.state[jid] = {**st, 'mode': 'picker_value',
                               'field': 'day'}
            return 'Введите день (1-31):'
        if body == '4':
            self.state[jid] = {**st, 'mode': 'picker_value',
                               'field': 'hour'}
            return 'Введите час (0-23):'
        if body == '5':
            self.state[jid] = {**st, 'mode': 'picker_value',
                               'field': 'minute'}
            return 'Введите минуту (0-59):'
        if body == '6':
            new_st = {**st, 'mode': 'relative',
                      'rel_value': 1, 'rel_unit': 1}
            self.state[jid] = new_st
            return self._fmt_relative(new_st)
        if body == '7':
            return self._picker_save(jid, st)
        return 'Введите номер пункта от 1 до 7 (0 - выход).'

    def _mode_picker_value(self, jid, st, body):
        field = st['field']
        val = body.strip()
        if not val.isdigit():
            return self._picker_value_prompt(field)
        val = int(val)
        limits = {
            'year': (2024, 2099),
            'month': (1, 12),
            'day': (1, 31),
            'hour': (0, 23),
            'minute': (0, 59),
        }
        lo, hi = limits[field]
        if not lo <= val <= hi:
            return self._picker_value_prompt(field)
        new_st = {**st, 'mode': 'picker', field: val}
        self.state[jid] = new_st
        return self._fmt_picker(st['id'], st['target'],
                                new_st['year'], new_st['month'],
                                new_st['day'], new_st['hour'],
                                new_st['minute'],
                                creating=st.get('creating', False))

    def _picker_value_prompt(self, field):
        prompts = {
            'year': 'Введите год (2024-2099):',
            'month': 'Введите месяц (1-12):',
            'day': 'Введите день (1-31):',
            'hour': 'Введите час (0-23):',
            'minute': 'Введите минуту (0-59):',
        }
        return prompts[field]

    def _fix_expires(self, jid, nid, new_remind_at):
        row = self.db.execute(
            'SELECT expires_at FROM reminders WHERE id=? AND owner=?',
            (nid, jid)).fetchone()
        if row and row[0] <= new_remind_at:
            self.db.execute(
                'UPDATE reminders SET expires_at=? WHERE id=? AND owner=?',
                (new_remind_at + 7 * 86400, nid, jid))

    def _picker_save(self, jid, st):
        ts = self._ts_from_picker(st)
        field = st['target']
        cur = self.db.execute(
            'UPDATE reminders SET %s=? WHERE id=? AND owner=?' % field,
            (ts, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Напоминание не найдено.'
        if field == 'remind_at':
            self._fix_expires(jid, st['id'], ts)
            self.db.execute(
                'UPDATE reminders SET delivered_at=NULL '
                'WHERE id=? AND owner=?',
                (st['id'], jid))
        if st.get('creating'):
            self.state[jid] = {**st, 'creating': False}
        return self._view(jid, st['id'])

    def _ts_from_picker(self, st):
        y = st['year']
        m = st['month']
        d = min(st['day'], calendar.monthrange(y, m)[1])
        tz = self._local_tz()
        dt = datetime(y, m, d, st['hour'], st['minute'], tzinfo=tz)
        return int(dt.timestamp())

    # --- relative time submenu ----------------------------------------------

    def _fmt_relative(self, st):
        unit_label = RELATIVE_UNITS[st['rel_unit'] - 1][0]
        return ('Относительное время:\n'
                '1. Значение: %d\n'
                '2. Единица: %s\n'
                '\n'
                '3. Сохранить\n'
                '0. Назад'
                % (st['rel_value'], unit_label))

    def _mode_relative(self, jid, st, body):
        if body == '1':
            self.state[jid] = {**st, 'mode': 'relative_value'}
            return 'Введите значение (1-1000):'
        if body == '2':
            self.state[jid] = {**st, 'mode': 'relative_unit'}
            lines = ['Выберите единицу:']
            for idx, (label, _) in enumerate(RELATIVE_UNITS, 1):
                lines.append('%d. %s' % (idx, label))
            return '\n'.join(lines)
        if body == '3':
            return self._relative_save(jid, st)
        return self._fmt_relative(st)

    def _mode_relative_value(self, jid, st, body):
        val = body.strip()
        if not val.isdigit() or not 1 <= int(val) <= 1000:
            return 'Введите значение от 1 до 1000:'
        new_st = {**st, 'mode': 'relative', 'rel_value': int(val)}
        self.state[jid] = new_st
        return self._fmt_relative(new_st)

    def _mode_relative_unit(self, jid, st, body):
        if not body.isdigit() or not 1 <= int(body) <= len(RELATIVE_UNITS):
            return ('Выберите номер единицы от 1 до %d.'
                    % len(RELATIVE_UNITS))
        new_st = {**st, 'mode': 'relative', 'rel_unit': int(body)}
        self.state[jid] = new_st
        return self._fmt_relative(new_st)

    def _relative_save(self, jid, st):
        val = st['rel_value']
        unit_idx = st['rel_unit'] - 1
        _, seconds_per = RELATIVE_UNITS[unit_idx]
        delta = val * seconds_per
        now = int(time.time())
        new_ts = now + delta
        field = st['target']
        cur = self.db.execute(
            'UPDATE reminders SET %s=? WHERE id=? AND owner=?' % field,
            (new_ts, st['id'], jid))
        if not cur.rowcount:
            del self.state[jid]
            return 'Напоминание не найдено.'
        if field == 'remind_at':
            self._fix_expires(jid, st['id'], new_ts)
            self.db.execute(
                'UPDATE reminders SET delivered_at=NULL '
                'WHERE id=? AND owner=?',
                (st['id'], jid))
        if st.get('creating'):
            self.state[jid] = {**st, 'creating': False}
        return self._view(jid, st['id'])

    # --- background check ---------------------------------------------------

    def check_reminders(self):
        now = int(time.time())
        rows = self.db.execute(
            'SELECT id, owner, title, body FROM reminders '
            'WHERE remind_at <= ? AND expires_at > ? '
            'AND delivered_at IS NULL',
            (now, now)).fetchall()
        if not rows:
            return []
        result = []
        ids = []
        for nid, owner, title, body in rows:
            result.append((owner, 'Напоминание: %s\n%s' % (title, body)))
            ids.append(nid)
        self.db.execute(
            'UPDATE reminders SET delivered_at=? WHERE id IN (%s)'
            % ','.join('?' * len(ids)),
            [now] + ids)
        return result

    @staticmethod
    def _local_tz():
        from plugins import TZ_OFFSET
        return timezone(timedelta(hours=TZ_OFFSET))

    @staticmethod
    def _fmt(ts):
        from plugins import TZ_OFFSET
        tz = timezone(timedelta(hours=TZ_OFFSET))
        return datetime.fromtimestamp(ts, tz=tz).strftime('%Y-%m-%d %H:%M')
