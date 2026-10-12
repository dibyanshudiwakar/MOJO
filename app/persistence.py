"""Persistence driver seam.

Sections 3 and 20 of the formal architecture baseline state that a target
database is *not* assumed and must be chosen during architecture governance.
The factory therefore talks to a driver interface instead of to ``sqlite3``
directly, so the store can be replaced without touching service code.

Two guarantees this module exists to provide, both of which the previous
inline ``sqlite3.connect`` usage failed to give:

1. Every connection is closed. ``with sqlite3.connect(...) as c`` only commits
   or rolls back the transaction -- it never closes the handle. Every call
   leaked a connection, which held file locks (breaking test teardown on
   Windows) and exhausted handles under load.
2. Concurrency settings are applied consistently: WAL journalling, a busy
   timeout so concurrent writers wait instead of raising ``database is
   locked``, and enforced foreign keys so lineage cannot dangle.
"""
import os
import sqlite3

PLACEHOLDER = '?'


class DriverError(RuntimeError):
    """Raised when a driver cannot satisfy the configured contract."""


class _Session:
    """Transaction scope that commits, rolls back, and *always* closes.

    Returned by ``Driver.session()`` so that the long-standing call pattern
    ``with repo.connect() as c: c.execute(...)`` keeps working unchanged while
    the underlying handle is now released deterministically.
    """

    __slots__ = ('_conn', '_closed')

    def __init__(self, conn):
        self._conn = conn
        self._closed = False

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._closed = True
            self._conn.close()
        return False

    # A few call sites historically used the raw connection without ``with``.
    # Expose the common surface so those keep working, but they must close.
    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self):
        if not self._closed:
            self._closed = True
            self._conn.close()


class SQLiteDriver:
    """Reference driver. Suitable for development and single-node operation."""

    name = 'sqlite'
    placeholder = PLACEHOLDER
    supports_concurrent_writers = False

    def __init__(self, dsn, timeout=30.0):
        self.dsn = dsn
        self.timeout = float(timeout)
        self._configured = False

    def _configure_once(self):
        """Apply settings that persist in the database file."""
        if self._configured:
            return
        self._configured = True
        if self.dsn == ':memory:':
            return
        try:
            c = sqlite3.connect(self.dsn, timeout=self.timeout)
            try:
                # WAL is a persistent property of the file and lets readers run
                # concurrently with a writer. It is unavailable on some network
                # filesystems, so a failure here is downgraded, not fatal.
                c.execute('pragma journal_mode=wal')
                c.execute('pragma synchronous=normal')
                c.commit()
            finally:
                c.close()
        except sqlite3.Error:
            pass

    def session(self):
        self._configure_once()
        conn = sqlite3.connect(self.dsn, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        # Per-connection settings must be re-applied on every handle.
        conn.execute('pragma foreign_keys=on')
        conn.execute('pragma busy_timeout=%d' % int(self.timeout * 1000))
        return _Session(conn)


class PostgresDriver:
    """Driver for an approved transactional store.

    Kept behind the same interface so selecting Postgres is a configuration
    change rather than a code change. It fails closed with an actionable error
    when its dependency is absent, rather than silently degrading -- the
    baseline's "no assumption under uncertainty" principle applies to
    infrastructure as much as to migration facts.
    """

    name = 'postgres'
    placeholder = '%s'
    supports_concurrent_writers = True

    def __init__(self, dsn, timeout=30.0):
        self.dsn = dsn
        self.timeout = float(timeout)
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise DriverError(
                'postgres_driver_requires_psycopg: install psycopg>=3 and set '
                'FACTORY_DB_DRIVER=postgres with FACTORY_DB_DSN'
            ) from exc

    def session(self):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self.dsn, connect_timeout=int(self.timeout), row_factory=dict_row)
        return _Session(conn)


DRIVERS = {'sqlite': SQLiteDriver, 'postgres': PostgresDriver}


def translate(sql, driver):
    """Rewrite ``?`` placeholders for drivers using a different paramstyle.

    Statements are authored once in the qmark style the SQLite driver uses.
    Quoted literals are skipped so a ``?`` inside a string is left alone.
    """
    if driver.placeholder == PLACEHOLDER:
        return sql
    out = []
    quote = None
    for ch in sql:
        if quote:
            if ch == quote:
                quote = None
            out.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        out.append(driver.placeholder if ch == PLACEHOLDER else ch)
    return ''.join(out)


def build(dsn=None, driver=None, timeout=None):
    """Construct the configured driver.

    Resolution order: explicit arguments, then ``FACTORY_DB_DRIVER`` /
    ``FACTORY_DB_DSN`` / ``FACTORY_DB``, then the SQLite default.
    """
    name = (driver or os.getenv('FACTORY_DB_DRIVER', 'sqlite')).strip().lower()
    if name not in DRIVERS:
        raise DriverError('unsupported_database_driver:%s (expected %s)'
                          % (name, ','.join(sorted(DRIVERS))))
    target = dsn or os.getenv('FACTORY_DB_DSN') or os.getenv('FACTORY_DB')
    if not target:
        raise DriverError('database_dsn_required')
    seconds = float(timeout if timeout is not None else os.getenv('FACTORY_DB_TIMEOUT', '30'))
    return DRIVERS[name](target, seconds)
