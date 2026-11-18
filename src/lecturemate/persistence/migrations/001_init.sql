-- 001: schema MVP (xem 02-thiet-ke/04-kien-truc.md)
CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  glossary_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  course_id TEXT,
  title TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  source_type TEXT NOT NULL,
  audio_path TEXT,
  status TEXT NOT NULL,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  asr_model TEXT,
  last_checkpoint_at TEXT,
  pending_cleanup INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_course ON sessions(course_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  sequence_number INTEGER NOT NULL,
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  en_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'final',
  flagged INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, sequence_number),
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id, start_ms);

CREATE TABLE IF NOT EXISTS translations (
  segment_id TEXT PRIMARY KEY,
  vi_text TEXT,
  model TEXT,
  status TEXT NOT NULL,
  error_code TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  FOREIGN KEY(segment_id) REFERENCES segments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_translations_status ON translations(status);

CREATE TABLE IF NOT EXISTS request_metrics (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  segment_id TEXT,
  kind TEXT NOT NULL DEFAULT 'translate',
  started_ms INTEGER,
  first_token_ms INTEGER,
  completed_ms INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  estimated_cost_usd REAL,
  status TEXT,
  created_at TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_metrics_created ON request_metrics(created_at);

-- Độ trễ đầu-cuối theo segment (ms tương đối, tách ASR / hàng đợi / API / UI)
CREATE TABLE IF NOT EXISTS latency (
  segment_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  asr_ms INTEGER,
  queue_ms INTEGER,
  api_ms INTEGER,
  ui_ms INTEGER,
  total_ms INTEGER,
  asr_model TEXT,
  context_chars INTEGER,
  FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
