from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional, Set

from .config import LodiaSettings


class Database:
    def __init__(self, settings: LodiaSettings):
        self.settings = settings
        self.sqlite_path = settings.data_dir / "lodia.db"
        self._pool = None
        settings.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def use_postgres(self) -> bool:
        return self.settings.use_postgres

    @contextmanager
    def session(self):
        if self.use_postgres:
            with self._postgres_pool().connection() as conn:
                yield conn
            return

        conn = self._sqlite_connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def connect(self):
        if self.use_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg[binary] is required when POSTGRES_DSN is configured") from exc
            return psycopg.connect(self.settings.database_url, row_factory=dict_row)

        return self._sqlite_connect()

    def _sqlite_connect(self):
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _postgres_pool(self):
        if self._pool is not None:
            return self._pool

        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError("psycopg_pool is required when POSTGRES_DSN is configured") from exc

        self._pool = ConnectionPool(
            conninfo=self.settings.database_url,
            min_size=self.settings.db_pool_min_size,
            max_size=max(self.settings.db_pool_min_size, self.settings.db_pool_max_size),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def execute(self, conn: Any, query: str, params: Iterable[Any] = ()):
        return conn.execute(self._query(query), tuple(params))

    def fetch_one(self, conn: Any, query: str, params: Iterable[Any] = ()) -> Optional[Any]:
        cursor = self.execute(conn, query, params)
        return cursor.fetchone()

    def execute_script(self, conn: Any, script: str) -> None:
        if not self.use_postgres:
            conn.executescript(script)
            return

        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)

    def column_names(self, conn: Any, table_name: str) -> Set[str]:
        table_name = _safe_identifier(table_name)
        if self.use_postgres:
            rows = self.execute(
                conn,
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ?
                """,
                (table_name,),
            )
            return {row["column_name"] for row in rows}

        rows = self.execute(conn, f"PRAGMA table_info({table_name})")
        return {row["name"] for row in rows}

    def ping(self) -> None:
        with self.session() as conn:
            self.fetch_one(conn, "SELECT 1")

    def _query(self, query: str) -> str:
        return query.replace("?", "%s") if self.use_postgres else query


def row_to_dict(row: Any) -> dict:
    return dict(row)


def _safe_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError("invalid_identifier")
    return value
