from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import LodiaSettings


class Database:
    def __init__(self, settings: LodiaSettings):
        self.settings = settings
        self.sqlite_path = settings.data_dir / "lodia.db"
        settings.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def use_postgres(self) -> bool:
        return self.settings.use_postgres

    def connect(self):
        if self.use_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg[binary] is required when POSTGRES_DSN is configured") from exc
            return psycopg.connect(self.settings.database_url, row_factory=dict_row)

        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

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

    def _query(self, query: str) -> str:
        return query.replace("?", "%s") if self.use_postgres else query


def row_to_dict(row: Any) -> dict:
    return dict(row)
