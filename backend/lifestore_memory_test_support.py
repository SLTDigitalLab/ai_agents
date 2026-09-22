"""Transactional psycopg test double. No production connections are made."""
from copy import deepcopy
from threading import RLock
from unittest.mock import patch

from services import lifestore_memory as memory


class MemoryDatabase:
    def __init__(self):
        self.rows = {}
        self.lock = RLock()
        self.statements = []
        self.fail_update = False

    def connect(self, *args, **kwargs):
        return Connection(self)


class Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.before = deepcopy(self.db.rows)
        return self

    def __exit__(self, kind, value, tb):
        if kind:
            self.db.rows = self.before
        self.db.lock.release()

    def cursor(self, **kwargs):
        return Cursor(self.db)


class Cursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        self.db.statements.append(sql)
        if sql.startswith('CREATE') or sql.startswith('SELECT pg_advisory_xact_lock'):
            return
        key = (params['agent_id'], params['thread_id'])
        if sql.startswith('INSERT'):
            empty = memory._empty_conversation()
            self.db.rows.setdefault(key, {**empty['state'], 'summary': '', 'messages': []})
        elif sql.startswith('SELECT'):
            self.result = deepcopy(self.db.rows[key])
        elif sql.startswith('UPDATE'):
            if self.db.fail_update:
                raise RuntimeError('postgres://secret-password')
            self.db.rows[key] = {k: deepcopy(getattr(v, 'obj', v)) for k, v in params.items()
                                 if k not in ('agent_id', 'thread_id')}
        elif sql.startswith('DELETE'):
            self.db.rows.pop(key, None)
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.result


def install_memory_database(test):
    db = MemoryDatabase()
    flag = patch.object(memory, '_table_created', False)
    connection = patch.object(memory.psycopg, 'connect', side_effect=db.connect)
    flag.start()
    connection.start()
    test.addCleanup(flag.stop)
    test.addCleanup(connection.stop)
    return db
