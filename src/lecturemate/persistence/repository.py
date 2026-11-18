"""Repository duy nhất cho mọi thao tác DB. An toàn đa luồng (một kết nối + khóa)."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..domain.models import SessionState, TranscriptEvent, TranslationResult, TranslationStatus
from .db import connect

log = logging.getLogger(__name__)

UNFINISHED_STATES = (
    SessionState.CHECKING_DEVICE.value,
    SessionState.RECORDING.value,
    SessionState.PAUSED.value,
    SessionState.STOPPING.value,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class SegmentRow:
    id: str
    session_id: str
    sequence_number: int
    start_ms: int
    end_ms: int
    en_text: str
    flagged: bool
    vi_text: str | None
    translation_status: str
    error_code: str | None


class SessionRepository:
    def __init__(self, db_path: Path | str, audio_root: Path | None = None):
        self.db_path = db_path
        self.audio_root = Path(audio_root).resolve() if audio_root else None
        self._conn = connect(db_path)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ infra
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # ---------------------------------------------------------------- courses
    def upsert_course(self, name: str, glossary: list[dict] | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Tên môn học không được rỗng")
        with self._tx() as c:
            row = c.execute("SELECT id FROM courses WHERE name = ?", (name,)).fetchone()
            if row:
                if glossary is not None:
                    c.execute(
                        "UPDATE courses SET glossary_json = ? WHERE id = ?",
                        (json.dumps(glossary, ensure_ascii=False), row["id"]),
                    )
                return row["id"]
            cid = new_id()
            c.execute(
                "INSERT INTO courses(id, name, glossary_json) VALUES (?, ?, ?)",
                (cid, name, json.dumps(glossary or [], ensure_ascii=False)),
            )
            return cid

    def list_courses(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id, name, glossary_json FROM courses ORDER BY name")]

    def get_glossary(self, course_id: str | None) -> list[dict]:
        if not course_id:
            return []
        rows = self._q("SELECT glossary_json FROM courses WHERE id = ?", (course_id,))
        if not rows:
            return []
        try:
            data = json.loads(rows[0]["glossary_json"])
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def set_glossary(self, course_id: str, glossary: list[dict]) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE courses SET glossary_json = ? WHERE id = ?",
                (json.dumps(glossary, ensure_ascii=False), course_id),
            )

    # --------------------------------------------------------------- sessions
    def create_session(
        self,
        title: str,
        source_type: str,
        course_id: str | None = None,
        audio_path: str | None = None,
        asr_model: str | None = None,
        status: SessionState = SessionState.CHECKING_DEVICE,
    ) -> str:
        sid = new_id()
        with self._tx() as c:
            c.execute(
                """INSERT INTO sessions(id, course_id, title, started_at, source_type, audio_path,
                   status, asr_model, last_checkpoint_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (sid, course_id, title or "Buổi học", now_iso(), source_type, audio_path,
                 status.value, asr_model, now_iso()),
            )
        return sid

    def set_audio_path(self, session_id: str, audio_path: str | None) -> None:
        with self._tx() as c:
            c.execute("UPDATE sessions SET audio_path = ? WHERE id = ?", (audio_path, session_id))

    def update_status(self, session_id: str, state: SessionState, duration_ms: int | None = None) -> None:
        with self._tx() as c:
            if duration_ms is None:
                c.execute(
                    "UPDATE sessions SET status = ?, last_checkpoint_at = ? WHERE id = ?",
                    (state.value, now_iso(), session_id),
                )
            else:
                c.execute(
                    "UPDATE sessions SET status = ?, duration_ms = ?, last_checkpoint_at = ? WHERE id = ?",
                    (state.value, int(duration_ms), now_iso(), session_id),
                )
            if state in (SessionState.COMPLETED, SessionState.RECOVERED):
                c.execute(
                    "UPDATE sessions SET ended_at = coalesce(ended_at, ?) WHERE id = ?",
                    (now_iso(), session_id),
                )

    def checkpoint(self, session_id: str, duration_ms: int) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE sessions SET duration_ms = ?, last_checkpoint_at = ? WHERE id = ?",
                (int(duration_ms), now_iso(), session_id),
            )

    def get_session(self, session_id: str) -> dict | None:
        rows = self._q(
            """SELECT s.*, c.name AS course_name FROM sessions s
               LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?""",
            (session_id,),
        )
        return dict(rows[0]) if rows else None

    def list_sessions(self, course_id: str | None = None, text: str | None = None) -> list[dict]:
        sql = """SELECT s.*, c.name AS course_name,
                   (SELECT count(*) FROM segments g WHERE g.session_id = s.id) AS segment_count,
                   (SELECT count(*) FROM segments g JOIN translations t ON t.segment_id = g.id
                      WHERE g.session_id = s.id AND t.status = 'done') AS translated_count
                 FROM sessions s LEFT JOIN courses c ON c.id = s.course_id
                 WHERE s.status != 'pending_delete'"""
        params: list = []
        if course_id:
            sql += " AND s.course_id = ?"
            params.append(course_id)
        if text:
            sql += " AND s.title LIKE ?"
            params.append(f"%{text}%")
        sql += " ORDER BY s.started_at DESC"
        return [dict(r) for r in self._q(sql, tuple(params))]

    def find_unfinished(self) -> list[dict]:
        marks = ",".join("?" * len(UNFINISHED_STATES))
        rows = self._q(f"SELECT * FROM sessions WHERE status IN ({marks})", UNFINISHED_STATES)
        return [dict(r) for r in rows]

    def rename_session(self, session_id: str, title: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))

    def _safe_audio_file(self, audio_path: str | None) -> Path | None:
        """Chỉ cho phép xóa file nằm trong thư mục audio của ứng dụng (chống path traversal)."""
        if not audio_path:
            return None
        p = Path(audio_path).resolve()
        if self.audio_root is None:
            return None
        try:
            p.relative_to(self.audio_root)
        except ValueError:
            log.warning("Bỏ qua xóa file nằm ngoài thư mục audio")
            return None
        return p

    def delete_session(self, session_id: str) -> tuple[bool, str]:
        """Xóa DB (cascade segments/translations/metrics/notes) và file WAV của đúng phiên.

        Trả (ok, message). Nếu file không xóa được: phiên được đánh dấu pending_delete,
        DB giữ dòng session để thử lại khi khởi động (``retry_pending_cleanup``).
        """
        sess = self.get_session(session_id)
        if not sess:
            return False, "Không tìm thấy buổi học"
        target = self._safe_audio_file(sess.get("audio_path"))
        if target is not None and target.exists():
            try:
                target.unlink()
            except OSError as exc:
                with self._tx() as c:
                    c.execute(
                        "UPDATE sessions SET pending_cleanup = 1, status = 'pending_delete' WHERE id = ?",
                        (session_id,),
                    )
                return False, f"Không xóa được file âm thanh ({exc.__class__.__name__}); sẽ thử lại sau."
        with self._tx() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return True, "Đã xóa buổi học, âm thanh, transcript và ghi chú."

    def retry_pending_cleanup(self) -> int:
        done = 0
        for r in self._q("SELECT id FROM sessions WHERE pending_cleanup = 1"):
            ok, _ = self.delete_session(r["id"])
            done += ok
        return done

    # --------------------------------------------------------------- segments
    def next_sequence(self, session_id: str) -> int:
        r = self._q("SELECT coalesce(max(sequence_number), -1) + 1 AS n FROM segments WHERE session_id = ?", (session_id,))
        return int(r[0]["n"])

    def commit_final(self, event: TranscriptEvent, translate: bool = True) -> bool:
        """Lưu segment final đúng một lần. Trả True nếu là bản ghi mới."""
        if not event.is_final:
            raise ValueError("Chỉ lưu segment final")
        text = event.text_en.strip()
        if not text:
            return False
        with self._tx() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO segments(id, session_id, sequence_number, start_ms, end_ms,
                   en_text, status, created_at) VALUES (?,?,?,?,?,?,'final',?)""",
                (event.segment_id, event.session_id, event.sequence_number, event.start_ms,
                 event.end_ms, text, now_iso()),
            )
            if cur.rowcount == 0:
                return False
            status = TranslationStatus.PENDING if translate else TranslationStatus.SKIPPED
            c.execute(
                "INSERT OR IGNORE INTO translations(segment_id, status, attempts, updated_at) VALUES (?,?,0,?)",
                (event.segment_id, status.value, now_iso()),
            )
        return True

    def list_segments(self, session_id: str) -> list[SegmentRow]:
        rows = self._q(
            """SELECT g.*, t.vi_text, coalesce(t.status, 'pending') AS tstatus, t.error_code
               FROM segments g LEFT JOIN translations t ON t.segment_id = g.id
               WHERE g.session_id = ? ORDER BY g.sequence_number""",
            (session_id,),
        )
        return [
            SegmentRow(r["id"], r["session_id"], r["sequence_number"], r["start_ms"], r["end_ms"],
                       r["en_text"], bool(r["flagged"]), r["vi_text"], r["tstatus"], r["error_code"])
            for r in rows
        ]

    def get_segment(self, segment_id: str) -> SegmentRow | None:
        rows = self._q(
            """SELECT g.*, t.vi_text, coalesce(t.status, 'pending') AS tstatus, t.error_code
               FROM segments g LEFT JOIN translations t ON t.segment_id = g.id WHERE g.id = ?""",
            (segment_id,),
        )
        if not rows:
            return None
        r = rows[0]
        return SegmentRow(r["id"], r["session_id"], r["sequence_number"], r["start_ms"], r["end_ms"],
                          r["en_text"], bool(r["flagged"]), r["vi_text"], r["tstatus"], r["error_code"])

    def preceding_texts(self, session_id: str, sequence_number: int, n: int) -> list[str]:
        if n <= 0:
            return []
        rows = self._q(
            """SELECT en_text FROM segments WHERE session_id = ? AND sequence_number < ?
               ORDER BY sequence_number DESC LIMIT ?""",
            (session_id, sequence_number, n),
        )
        return [r["en_text"] for r in reversed(rows)]

    def edit_segment(self, segment_id: str, en_text: str | None = None, vi_text: str | None = None) -> None:
        with self._tx() as c:
            if en_text is not None:
                c.execute("UPDATE segments SET en_text = ? WHERE id = ?", (en_text, segment_id))
            if vi_text is not None:
                c.execute(
                    "UPDATE translations SET vi_text = ?, status = 'done', model = 'manual', updated_at = ? WHERE segment_id = ?",
                    (vi_text, now_iso(), segment_id),
                )

    def set_flag(self, segment_id: str, flagged: bool) -> None:
        with self._tx() as c:
            c.execute("UPDATE segments SET flagged = ? WHERE id = ?", (int(flagged), segment_id))

    # ----------------------------------------------------------- translations
    def claim_translation(self, segment_id: str) -> bool:
        """Đánh dấu in_flight nếu đang pending/failed. Chống gửi trùng do retry."""
        with self._tx() as c:
            cur = c.execute(
                """UPDATE translations SET status = 'in_flight', attempts = attempts + 1, updated_at = ?
                   WHERE segment_id = ? AND status IN ('pending', 'failed')""",
                (now_iso(), segment_id),
            )
            return cur.rowcount == 1

    def save_translation(self, result: TranslationResult) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE translations SET vi_text = coalesce(?, vi_text), model = ?, status = ?,
                   error_code = ?, updated_at = ? WHERE segment_id = ?""",
                (result.text_vi, result.model, result.status.value, result.error_code, now_iso(),
                 result.segment_id),
            )

    def release_translation(self, segment_id: str, status: TranslationStatus, error_code: str | None) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE translations SET status = ?, error_code = ?, updated_at = ? WHERE segment_id = ?",
                (status.value, error_code, now_iso(), segment_id),
            )

    def reset_in_flight(self) -> int:
        """Khi khởi động: in_flight còn sót (crash) -> pending để dịch lại."""
        with self._tx() as c:
            return c.execute("UPDATE translations SET status = 'pending' WHERE status = 'in_flight'").rowcount

    def pending_translations(self, session_id: str | None = None, include_failed: bool = True,
                             include_skipped: bool = False) -> list[SegmentRow]:
        statuses = ["pending"] + (["failed"] if include_failed else []) + (["skipped"] if include_skipped else [])
        marks = ",".join("?" * len(statuses))
        sql = f"""SELECT g.id FROM segments g JOIN translations t ON t.segment_id = g.id
                  WHERE t.status IN ({marks})"""
        params: list = list(statuses)
        if session_id:
            sql += " AND g.session_id = ?"
            params.append(session_id)
        sql += " ORDER BY g.session_id, g.sequence_number"
        ids = [r["id"] for r in self._q(sql, tuple(params))]
        if include_skipped and ids:
            with self._tx() as c:
                c.execute(
                    f"UPDATE translations SET status='pending' WHERE status='skipped' AND segment_id IN ({','.join('?'*len(ids))})",
                    tuple(ids),
                )
        return [s for s in (self.get_segment(i) for i in ids) if s]

    def translation_counts(self, session_id: str) -> dict[str, int]:
        rows = self._q(
            """SELECT t.status, count(*) AS n FROM segments g JOIN translations t ON t.segment_id = g.id
               WHERE g.session_id = ? GROUP BY t.status""",
            (session_id,),
        )
        return {r["status"]: r["n"] for r in rows}

    # ---------------------------------------------------------------- metrics
    def record_request(self, session_id: str | None, segment_id: str | None, kind: str,
                       result: TranslationResult, cost_usd: float) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO request_metrics(id, session_id, segment_id, kind, started_ms, first_token_ms,
                   completed_ms, input_tokens, output_tokens, estimated_cost_usd, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), session_id, segment_id, kind, 0, result.first_token_ms, result.elapsed_ms,
                 result.input_tokens, result.output_tokens, cost_usd, result.status.value, now_iso()),
            )

    def record_latency(self, segment_id: str, session_id: str, **parts) -> None:
        cols = ["asr_ms", "queue_ms", "api_ms", "ui_ms", "total_ms", "asr_model", "context_chars"]
        vals = [parts.get(k) for k in cols]
        with self._tx() as c:
            c.execute(
                f"""INSERT INTO latency(segment_id, session_id, {', '.join(cols)}) VALUES (?,?,{','.join('?'*len(cols))})
                    ON CONFLICT(segment_id) DO UPDATE SET """
                + ", ".join(f"{k} = coalesce(excluded.{k}, {k})" for k in cols),
                (segment_id, session_id, *vals),
            )

    def latency_rows(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            return [dict(r) for r in self._q("SELECT * FROM latency WHERE session_id = ?", (session_id,))]
        return [dict(r) for r in self._q("SELECT * FROM latency")]

    def month_cost_usd(self) -> float:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        r = self._q(
            "SELECT coalesce(sum(estimated_cost_usd), 0) AS c FROM request_metrics WHERE substr(created_at, 1, 7) = ?",
            (month,),
        )
        return float(r[0]["c"])

    # ------------------------------------------------------------------ notes
    def save_notes(self, session_id: str, markdown: str, model: str, source_ids: list[str],
                   status: str = "draft") -> None:
        ts = now_iso()
        with self._tx() as c:
            c.execute(
                """INSERT INTO notes(session_id, markdown, model, status, source_segment_ids, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET markdown = excluded.markdown, model = excluded.model,
                   status = excluded.status, source_segment_ids = excluded.source_segment_ids,
                   updated_at = excluded.updated_at""",
                (session_id, markdown, model, status, json.dumps(source_ids), ts, ts),
            )

    def get_notes(self, session_id: str) -> dict | None:
        rows = self._q("SELECT * FROM notes WHERE session_id = ?", (session_id,))
        return dict(rows[0]) if rows else None
