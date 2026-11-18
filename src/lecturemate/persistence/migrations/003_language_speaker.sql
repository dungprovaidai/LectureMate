-- Original text keeps the legacy en_text name for compatibility with existing sessions.
ALTER TABLE segments ADD COLUMN language TEXT NOT NULL DEFAULT 'und';
ALTER TABLE segments ADD COLUMN speaker_id TEXT;
