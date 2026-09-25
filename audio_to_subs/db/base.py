"""Database engine and WAL pragmas configuration.

SQLite with WAL mode for concurrent read/write access.
SQLAlchemy 2.x ORM.
"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Execution-option key: pass True to Connection.execution_options() to request
# a plain deferred BEGIN instead of BEGIN IMMEDIATE for that transaction.
# Opt-in, for callers that only ever read within the transaction (e.g. the
# /api/healthz probe) - every other caller keeps taking the write lock up
# front, which is deliberate (see _on_begin below).
READ_ONLY_OPTION = "parolesub_read_only"

# WAL pragmas to apply on every new connection
WAL_PRAGMAS = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    # 15s: gives a writer more room to wait out contention from another
    # process (the worker) before erroring - the in-process case (API's own
    # periodic Bazarr poll vs. a manual refresh) is additionally serialized
    # by poller.py's _full_sync_lock, but that can't cover cross-process
    # contention, so busy_timeout is the only backstop there.
    "busy_timeout": 15000,
    "foreign_keys": "ON",
}


class Base(DeclarativeBase):
    """SQLAlchemy declarative base class."""

    pass


# Async engine cache - keyed by DSN to support multiple databases
_async_engines: dict[str, AsyncEngine] = {}


def get_async_engine(dsn: str) -> AsyncEngine:
    """Create and return async SQLAlchemy engine with WAL pragmas.

    Caches engines per DSN to support multiple databases (e.g., admin CLI
    connecting to different DB than the main app).

    Args:
        dsn: Database URL, e.g., 'sqlite+aiosqlite:////data/parolesub.db'

    Returns:
        SQLAlchemy async engine with WAL pragmas configured
    """
    global _async_engines
    if dsn not in _async_engines:
        _async_engines[dsn] = create_async_engine(dsn, echo=False)
        _install_sqlite_listeners(_async_engines[dsn].sync_engine)
    return _async_engines[dsn]


def _install_sqlite_listeners(sync_engine: Engine) -> None:
    """Install WAL/BEGIN-IMMEDIATE pragma listeners on a sync engine.

    SQLAlchemy always dispatches DBAPI-level connection events against the
    underlying sync ``Engine`` object — for the async engine that means its
    ``.sync_engine`` — so this single helper covers both the async engine
    (worker/API) and the plain sync engine (used elsewhere) with identical
    listener logic.
    """

    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        # Disable pysqlite's implicit transaction management so we can emit our
        # own BEGIN IMMEDIATE below. Without this, transactions start DEFERRED:
        # a SELECT-then-UPDATE acquires a read lock first, then fails instantly
        # with SQLITE_BUSY on the read->write upgrade (busy_timeout does not
        # apply to lock upgrades). See sqlite "database is locked" gotcha.
        dbapi_connection.isolation_level = None
        for key, value in WAL_PRAGMAS.items():
            dbapi_connection.execute(f"PRAGMA {key} = {value}")

    @event.listens_for(sync_engine, "begin")
    def _on_begin(conn: Any) -> None:
        if conn.get_execution_options().get(READ_ONLY_OPTION):
            # Deferred BEGIN: SQLite takes no lock until the first
            # statement, and a read-only transaction never upgrades to a
            # write lock, so this never contends with a concurrent
            # BEGIN IMMEDIATE writer - unlike the unconditional write lock
            # below, which every other transaction (read or write) takes.
            conn.exec_driver_sql("BEGIN")
            return
        # Acquire the write lock up front; busy_timeout then makes concurrent
        # writers wait politely instead of erroring.
        conn.exec_driver_sql("BEGIN IMMEDIATE")


# Sync engine for worker
_sync_engine: Engine | None = None


def get_sync_engine(dsn: str) -> Engine:
    """Create and return sync SQLAlchemy engine with WAL pragmas.

    Args:
        dsn: Database URL, e.g., 'sqlite:////data/parolesub.db'

    Returns:
        SQLAlchemy sync engine with WAL pragmas configured
    """
    from sqlalchemy import create_engine

    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            dsn.replace("sqlite+aiosqlite", "sqlite"), echo=False
        )
        _install_sqlite_listeners(_sync_engine)
    return _sync_engine
