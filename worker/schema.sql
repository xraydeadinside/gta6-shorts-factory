CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- render | vod
  url TEXT NOT NULL,
  status TEXT NOT NULL,            -- laptop | queued | working | ready | published | rejected | failed | done
  stage TEXT,
  created_at INTEGER,
  updated_at INTEGER,
  parent_id INTEGER,
  start REAL,
  end_t REAL,
  note TEXT,
  brief TEXT,
  material TEXT,                   -- clip downloaded by the laptop helper (asset name in the private repo)
  material_meta TEXT,                      -- what the scout AI saw (JSON), for /vod moments
  wish TEXT,
  intro_file_id TEXT,
  reuse_plan INTEGER DEFAULT 0,
  title TEXT,
  description TEXT,
  chosen INTEGER DEFAULT 0,
  flow_prompt TEXT,
  flow_ok INTEGER DEFAULT 0,
  card_msg_id INTEGER,
  ack_msg_id INTEGER,
  video_msg_id INTEGER,
  result TEXT,
  error TEXT,
  run_url TEXT,
  version INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_url ON jobs(url);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, exp INTEGER);
