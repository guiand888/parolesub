"""Tests for the SQLite engine configuration in audio_to_subs/db/base.py."""

import asyncio

from sqlalchemy import event, text

from audio_to_subs.db.base import READ_ONLY_OPTION, get_async_engine


def _statements_for(dsn: str, *, read_only: bool) -> list[str]:
    """Return every SQL statement executed while opening one connection."""
    engine = get_async_engine(dsn)
    statements: list[str] = []

    def _capture(conn, cursor, statement, *args, **kwargs) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:

        async def run() -> None:
            conn = await engine.connect()
            if read_only:
                conn = await conn.execution_options(**{READ_ONLY_OPTION: True})
            await conn.execute(text("SELECT 1"))
            await conn.close()

        asyncio.run(run())
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    return statements


class TestReadOnlyExecutionOption:
    """The `begin` listener must branch on READ_ONLY_OPTION."""

    def test_default_transaction_takes_write_lock(self, tmp_path):
        """Without the option, every transaction still opens BEGIN IMMEDIATE."""
        dsn = f"sqlite+aiosqlite:///{tmp_path / 'default.db'}"
        statements = _statements_for(dsn, read_only=False)
        assert "BEGIN IMMEDIATE" in statements
        assert "BEGIN" not in statements

    def test_read_only_transaction_is_deferred(self, tmp_path):
        """With READ_ONLY_OPTION, the transaction opens a plain BEGIN."""
        dsn = f"sqlite+aiosqlite:///{tmp_path / 'readonly.db'}"
        statements = _statements_for(dsn, read_only=True)
        assert "BEGIN" in statements
        assert "BEGIN IMMEDIATE" not in statements
