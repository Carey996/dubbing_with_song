CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    text_length INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    current_analysis_id TEXT,
    current_render_id TEXT
);

CREATE TABLE IF NOT EXISTS project_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_id, asset_type, path),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    chapter_id TEXT,
    chapter_title TEXT,
    status TEXT NOT NULL,
    engine TEXT,
    result_path TEXT,
    segment_count INTEGER NOT NULL DEFAULT 0,
    lyric_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS renders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT,
    output_url TEXT,
    message TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

INSERT INTO schema_meta(key, value)
VALUES('schema_version', :schema_version)
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
