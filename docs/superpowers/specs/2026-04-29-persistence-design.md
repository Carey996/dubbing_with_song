# Persistence Design

## Goal

Add durable local persistence for the full dubbing workflow without turning the app into a cloud service. The app should survive backend/frontend restarts and let users reopen prior projects with their source text, analysis results, timeline edits, uploaded song, and generated audio outputs intact.

## Non-Goals

- No remote storage, object storage, sync service, login, or multi-user permission model.
- No background worker queue in this iteration.
- No SQLAlchemy dependency unless the local sqlite3 approach becomes too limiting.
- No change to the existing JSON-only project creation contract.

## Storage Model

The durable store is split between SQLite metadata and local file assets.

- SQLite database: `data/app.sqlite3`
- Project files: `data/projects/<project_id>/`
- Render outputs: `data/outputs/<project_id>/<render_id>/`

SQLite stores workflow state, searchable metadata, asset indexes, and links between project stages. Files store larger or user-facing artifacts such as source text, uploaded MP3, analysis JSON, timeline JSON, generated TTS chunks, and final audio outputs.

This keeps the MVP easy to inspect locally while avoiding a pile of unindexed JSON folders that the frontend cannot browse or recover.

## Tables

### `schema_meta`

Tracks database schema version.

- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`

Initial version is `1`.

### `projects`

One row per project.

- `id TEXT PRIMARY KEY`
- `title TEXT NOT NULL`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `status TEXT NOT NULL`
- `text_length INTEGER NOT NULL`
- `source_path TEXT NOT NULL`
- `current_analysis_id TEXT`
- `current_render_id TEXT`

`title` can be derived from the first non-empty source-text line, capped to a short display length. `status` uses simple values such as `created`, `analyzed`, `timeline_saved`, `song_uploaded`, `rendered`, and `failed`.

### `project_assets`

Indexes all relevant files for a project.

- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `asset_type TEXT NOT NULL`
- `path TEXT NOT NULL`
- `filename TEXT`
- `size_bytes INTEGER NOT NULL`
- `created_at TEXT NOT NULL`
- `metadata_json TEXT NOT NULL DEFAULT '{}'`

Asset types include `source_text`, `song_mp3`, `analysis_json`, `timeline_json`, `render_output`, `tts_chunk`, and `intermediate_audio`.

### `analyses`

Records every analysis run, including chapter-scoped analysis.

- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `scope TEXT NOT NULL`
- `chapter_id TEXT`
- `chapter_title TEXT`
- `status TEXT NOT NULL`
- `engine TEXT`
- `result_path TEXT`
- `segment_count INTEGER NOT NULL DEFAULT 0`
- `lyric_count INTEGER NOT NULL DEFAULT 0`
- `error TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`scope` is `all` or `chapter`. A successful run writes `result_path` to `data/projects/<project_id>/analysis/<analysis_id>.json` and updates `projects.current_analysis_id`.

### `renders`

Records every render attempt.

- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `status TEXT NOT NULL`
- `output_path TEXT`
- `output_url TEXT`
- `message TEXT`
- `warnings_json TEXT NOT NULL DEFAULT '[]'`
- `error TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Render outputs live under `data/outputs/<project_id>/<render_id>/`. A successful render updates `projects.current_render_id` and registers the final file in `project_assets`.

## Service Boundaries

Create a small persistence layer under `backend/app/services/`:

- `db.py`: SQLite connection, schema initialization, transaction helper, row helpers.
- `project_store.py`: remains the public project workflow service, but delegates metadata reads/writes to SQLite.
- `asset_store.py` if the asset bookkeeping becomes too large for `project_store.py`.

The router should continue to call workflow functions rather than raw database functions. This preserves the current service boundary used by both FastAPI routes and the CLI.

## Workflow Changes

### Create Project

1. Normalize and validate text.
2. Create `data/projects/<project_id>/source.txt`.
3. Insert `projects` row.
4. Insert `project_assets` row for `source_text`.
5. Return the existing project public shape, plus any additive fields needed by the frontend.

### Upload Text File

The text-file endpoint still decodes upload content and calls the same project creation service as JSON creation.

### List Projects

Add `GET /api/projects` to return recent projects ordered by `updated_at DESC`. Each item includes id, title, created time, updated time, status, text length, `hasAnalysis`, `hasSong`, `hasTimeline`, and `hasRender`.

### Get Project

`GET /api/projects/{project_id}` continues to return source text and current project state. It should also include current analysis, current timeline, and latest render result when present, so the frontend can restore the working state after refresh.

### Analyze Project

1. Insert an `analyses` row with `status='running'`.
2. Run the existing analyzer.
3. Write analysis JSON to `data/projects/<project_id>/analysis/<analysis_id>.json`.
4. Write/update `timeline.json` from the analysis timeline.
5. Mark analysis `succeeded`, register the analysis and timeline assets, and update project state.
6. On failure, mark analysis `failed`, store the error, update project status, and return the existing HTTP error.

Chapter analysis follows the same path with `scope='chapter'`, `chapter_id`, and `chapter_title`. It should no longer be response-only.

### Save Timeline

`PATCH /api/projects/{project_id}/timeline` writes `timeline.json`, upserts its asset row, and updates project status to `timeline_saved`.

### Upload Song

`POST /api/projects/{project_id}/song-file` writes `song.mp3`, upserts its `song_mp3` asset, and updates project status to `song_uploaded` without changing the project creation contract.

### Render

1. Insert a `renders` row with `status='running'`.
2. Render into `data/outputs/<project_id>/<render_id>/`.
3. Register generated chunks/intermediate files where useful and always register the final output.
4. Mark render `succeeded`, store `output_url`, update `projects.current_render_id`, and return the existing render response shape.
5. On failure, mark render `failed`, store the error, and return the existing HTTP error.

## API Additions

Existing endpoints remain valid:

- `POST /api/projects`
- `POST /api/projects/text-file`
- `GET /api/projects/{project_id}`
- `GET /api/projects/{project_id}/chapters`
- `POST /api/projects/{project_id}/analyze`
- `POST /api/projects/{project_id}/chapters/{chapter_id}/analyze`
- `POST /api/projects/{project_id}/song-file`
- `PATCH /api/projects/{project_id}/timeline`
- `POST /api/projects/{project_id}/render`

New endpoints:

- `GET /api/projects`
- `GET /api/projects/{project_id}/analyses`
- `GET /api/projects/{project_id}/renders`

All new endpoints are local app conveniences and do not imply user accounts or remote service behavior.

## Frontend Behavior

On app startup, load `GET /api/projects` and show recent projects in the side panel. Selecting a project calls `GET /api/projects/{id}` and restores:

- Source text
- Chapter preview
- Current analysis and song candidates
- Timeline segments and mix controls
- Song upload state
- Latest render result and download link

After create, analyze, upload, save timeline, or render operations, refresh the current project detail and project list so the UI reflects persisted state rather than only React state.

## Migration and Compatibility

On backend startup, initialize `data/app.sqlite3` if missing.

For existing `data/projects/<id>` folders, add a lightweight import path:

- Scan folders with `metadata.json` and `source.txt`.
- Insert missing project rows.
- Register existing `analysis.json`, `timeline.json`, `song.mp3`, and output files when present.

This lets the current local data survive the switch without a manual migration step.

## Error Handling

SQLite writes for a single workflow stage should be transactional where practical. File writes happen before or inside the workflow transaction depending on the stage:

- If file write fails, do not update DB state.
- If DB write fails after file write, return an error and leave the file as an orphan that can be recovered by the import scan.
- Failed analysis/render attempts remain visible in history with their error message.

Existing fail-fast behavior for missing LLM/TTS config remains unchanged.

## Testing

Backend tests should cover:

- Project creation writes source file and DB rows.
- Project list returns created projects after restart-like reload.
- Existing project folders can be imported.
- Full analysis saves analysis history, current analysis, and timeline.
- Chapter analysis persists with `scope='chapter'`.
- Timeline save updates DB metadata and file asset.
- Song upload registers the MP3 asset.
- Render success records a render row and output asset using renderer test doubles.

Frontend tests remain focused on pure helper logic for now. Manual browser verification should cover project-list restore after backend/frontend restart.

