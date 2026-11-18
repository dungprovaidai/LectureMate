-- 002: tìm kiếm FTS5 theo segment + ghi chú AI (Phase 3)
CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(
  segment_id UNINDEXED,
  session_id UNINDEXED,
  en_text,
  vi_text,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS trg_segments_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segment_fts(segment_id, session_id, en_text, vi_text)
  VALUES (new.id, new.session_id, new.en_text, '');
END;

CREATE TRIGGER IF NOT EXISTS trg_segments_au AFTER UPDATE OF en_text ON segments BEGIN
  UPDATE segment_fts SET en_text = new.en_text WHERE segment_id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_segments_ad AFTER DELETE ON segments BEGIN
  DELETE FROM segment_fts WHERE segment_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_translations_ai AFTER INSERT ON translations BEGIN
  UPDATE segment_fts SET vi_text = coalesce(new.vi_text, '') WHERE segment_id = new.segment_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_translations_au AFTER UPDATE OF vi_text ON translations BEGIN
  UPDATE segment_fts SET vi_text = coalesce(new.vi_text, '') WHERE segment_id = new.segment_id;
END;

CREATE TABLE IF NOT EXISTS notes (
  session_id TEXT PRIMARY KEY,
  markdown TEXT NOT NULL,
  model TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  source_segment_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
