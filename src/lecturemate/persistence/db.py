"""Kết nối SQLite (WAL + foreign keys) và chạy migration theo thứ tự."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL + WAL: một transaction đã commit vẫn còn sau khi process bị kill
    # (chỉ có thể mất khi mất điện cả hệ điều hành).
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def _migration_files() -> list[tuple[int, str, str]]:
    pkg = resources.files("lecturemate.persistence") / "migrations"
    out = []
    for entry in pkg.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:3].isdigit():
            out.append((int(name[:3]), name, entry.read_text(encoding="utf-8")))
    return sorted(out)


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    count = 0
    for version, name, sql in _migration_files():
        if version in applied:
            continue
        conn.execute("BEGIN")
        try:
            # executescript tự COMMIT, nên tách từng câu lệnh để giữ trong transaction
            for stmt in _split_sql(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version(version, name) VALUES (?, ?)", (version, name))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        count += 1
    return count


def _split_sql(sql: str) -> list[str]:
    """Tách script thành câu lệnh, giữ nguyên khối BEGIN ... END; của trigger."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    stmts, buf = [], []
    for ln in lines:
        buf.append(ln)
        candidate = "\n".join(buf).strip()
        if candidate and sqlite3.complete_statement(candidate):
            stmts.append(candidate)
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        stmts.append(rest)
    return stmts
