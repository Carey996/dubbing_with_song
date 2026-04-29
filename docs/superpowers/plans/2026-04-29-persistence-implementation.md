# SQLite Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add restart-safe local persistence for projects, analyses, timelines, uploaded songs, and render outputs using SQLite metadata plus local file assets.

**Architecture:** Keep `project_store.py` as the workflow-facing service used by both FastAPI and CLI. Add a small standard-library SQLite layer for schema, repositories, and migration-from-existing-files; keep large artifacts in `data/projects` and `data/outputs`.

**Tech Stack:** Python 3.11, FastAPI, standard-library `sqlite3`, React/Vite, pytest, existing file-based assets under `data/`.

---

## File Structure

- Create `backend/app/services/db.py`: SQLite path constants, connection helper, schema initialization, transaction helper, row conversion, and existing-folder import.
- Modify `backend/app/main.py`: initialize SQLite and import existing project folders during app startup.
- Modify `backend/app/services/project_store.py`: keep public workflow functions but back metadata, analysis, timeline, song, and render records with SQLite.
- Modify `backend/app/services/renderer.py`: render into a caller-supplied output directory so each render attempt can get its own persisted folder.
- Modify `backend/app/routers/projects.py`: add project list, analysis history, render history endpoints; make chapter analysis save results.
- Modify `backend/app/cli.py`: keep CLI on the same service functions and ensure DB is initialized for CLI usage.
- Modify `frontend/src/App.jsx`: load recent projects, select a saved project, and restore current project state.
- Modify `frontend/src/styles.css`: add compact project history styles.
- Modify `README.md`: document the local persistence model and new restore behavior.
- Modify `tests/test_api.py`: add persistence, history, and migration coverage.
- Modify `tests/test_cli.py`: ensure CLI initializes persistence and records created projects.

## Task 1: SQLite Schema And Repository Primitives

**Files:**
- Create: `backend/app/services/db.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for database initialization and project creation metadata**

Append these tests near the top of `tests/test_api.py`, after constants:

```python
from backend.app.services import db
from backend.app.services import project_store
from backend.app.services.project_store import list_projects


def test_project_creation_writes_sqlite_metadata():
    created = client.post("/api/projects", json={"text": "第一章 登台\n内容一。"})
    assert created.status_code == 200

    projects_list = list_projects()

    assert any(item["id"] == created.json()["id"] for item in projects_list)
    saved = next(item for item in projects_list if item["id"] == created.json()["id"])
    assert saved["title"] == "第一章 登台"
    assert saved["status"] == "created"
    assert saved["textLength"] == len("第一章 登台\n内容一。")
    assert saved["hasAnalysis"] is False
    assert saved["hasSong"] is False
    assert saved["hasTimeline"] is False
    assert saved["hasRender"] is False


def test_database_schema_version_is_initialized():
    with db.connect() as conn:
        value = conn.execute("select value from schema_meta where key = 'schema_version'").fetchone()[0]

    assert value == "1"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_api.py::test_project_creation_writes_sqlite_metadata tests/test_api.py::test_database_schema_version_is_initialized -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-db
```

Expected: fail because `backend.app.services.db` and/or `list_projects` do not exist.

- [ ] **Step 3: Create `backend/app/services/db.py`**

```python
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.sqlite3"
SCHEMA_VERSION = "1"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    with transaction() as conn:
        conn.executescript(
            """
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
            """
        )
        conn.execute(
            """
            INSERT INTO schema_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SCHEMA_VERSION,),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)
```

- [ ] **Step 4: Import and initialize DB in `project_store.py`**

At the top of `backend/app/services/project_store.py`, add:

```python
from .db import initialize_database, transaction
```

At the start of `create_project`, before generating the project id, add:

```python
    initialize_database()
```

Add this helper near `write_json`:

```python
def derive_title(text: str) -> str:
    for line in text.splitlines():
        title = line.strip()
        if title:
            return title[:40]
    return "未命名项目"
```

Add this function near `get_project`:

```python
def list_projects() -> list[dict]:
    initialize_database()
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
              p.*,
              EXISTS(SELECT 1 FROM analyses a WHERE a.project_id = p.id AND a.status = 'succeeded') AS has_analysis,
              EXISTS(SELECT 1 FROM project_assets pa WHERE pa.project_id = p.id AND pa.asset_type = 'song_mp3') AS has_song,
              EXISTS(SELECT 1 FROM project_assets pa WHERE pa.project_id = p.id AND pa.asset_type = 'timeline_json') AS has_timeline,
              EXISTS(SELECT 1 FROM renders r WHERE r.project_id = p.id AND r.status = 'succeeded') AS has_render
            FROM projects p
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "status": row["status"],
            "textLength": row["text_length"],
            "hasAnalysis": bool(row["has_analysis"]),
            "hasSong": bool(row["has_song"]),
            "hasTimeline": bool(row["has_timeline"]),
            "hasRender": bool(row["has_render"]),
        }
        for row in rows
    ]
```

In `create_project`, after writing `source.txt` and `metadata.json`, insert:

```python
    now = metadata["createdAt"]
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO projects(id, title, created_at, updated_at, status, text_length, source_path)
            VALUES (?, ?, ?, ?, 'created', ?, ?)
            """,
            (project_id, derive_title(normalized), now, now, len(normalized), str(root / "source.txt")),
        )
        conn.execute(
            """
            INSERT INTO project_assets(id, project_id, asset_type, path, filename, size_bytes, created_at)
            VALUES (?, ?, 'source_text', ?, 'source.txt', ?, ?)
            """,
            (uuid.uuid4().hex, project_id, str(root / "source.txt"), (root / "source.txt").stat().st_size, now),
        )
```

- [ ] **Step 5: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_api.py::test_project_creation_writes_sqlite_metadata tests/test_api.py::test_database_schema_version_is_initialized -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-db
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/services/db.py backend/app/services/project_store.py tests/test_api.py
git commit -m "feat: add sqlite persistence foundation"
```

## Task 2: Project Detail Restore And Existing Folder Import

**Files:**
- Modify: `backend/app/services/project_store.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/projects.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for project list API and import scan**

Append to `tests/test_api.py`:

```python
def test_projects_endpoint_lists_persisted_projects():
    created = client.post("/api/projects", json={"text": "历史项目内容。"})

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert any(item["id"] == created.json()["id"] for item in response.json()["projects"])


def test_existing_project_folder_is_imported():
    project_id = "legacyabc123"
    root = project_store.PROJECTS_DIR / project_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.txt").write_text("旧项目正文。", encoding="utf-8")
    project_store.write_json(root / "metadata.json", {"id": project_id, "createdAt": "2026-04-29T00:00:00+00:00"})

    project_store.import_existing_projects()
    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert response.json()["id"] == project_id
    assert response.json()["title"] == "旧项目正文。"
    assert response.json()["text"] == "旧项目正文。"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_api.py::test_projects_endpoint_lists_persisted_projects tests/test_api.py::test_existing_project_folder_is_imported -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-import
```

Expected: fail because endpoint and `import_existing_projects` do not exist.

- [ ] **Step 3: Add import helpers and richer public project detail**

In `Project.public_dict`, add `title`, `updatedAt`, `status`, `hasRender`, and `latestRender`:

```python
        latest_render = get_latest_render(self.id)
        payload = {
            "id": self.id,
            "title": self.metadata.get("title") or derive_title(self.text),
            "createdAt": self.metadata["createdAt"],
            "updatedAt": self.metadata.get("updatedAt", self.metadata["createdAt"]),
            "status": self.metadata.get("status", "created"),
            "textLength": len(self.text),
            "hasAnalysis": self.analysis_path.exists(),
            "hasSong": self.song_path.exists(),
            "hasTimeline": self.timeline_path.exists(),
            "hasRender": latest_render is not None,
            "latestRender": latest_render,
        }
```

Add helper functions to `project_store.py`:

```python
def get_project_row(project_id: str) -> dict | None:
    initialize_database()
    with transaction() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def get_latest_render(project_id: str) -> dict | None:
    initialize_database()
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT * FROM renders
            WHERE project_id = ? AND status = 'succeeded'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "outputUrl": row["output_url"],
        "outputPath": row["output_path"],
        "message": row["message"],
        "warnings": json.loads(row["warnings_json"] or "[]"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
```

In `get_project`, after reading metadata JSON, merge DB row:

```python
    metadata = read_json(metadata_path)
    row = get_project_row(project_id)
    if row:
        metadata = {
            **metadata,
            "title": row["title"],
            "updatedAt": row["updated_at"],
            "status": row["status"],
        }
```

Add `import_existing_projects`:

```python
def import_existing_projects() -> None:
    initialize_database()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for root in PROJECTS_DIR.iterdir():
        metadata_path = root / "metadata.json"
        source_path = root / "source.txt"
        if not root.is_dir() or not metadata_path.exists() or not source_path.exists():
            continue
        project_id = root.name
        metadata = read_json(metadata_path)
        text = source_path.read_text(encoding="utf-8")
        created_at = metadata.get("createdAt") or datetime.now(timezone.utc).isoformat()
        updated_at = created_at
        status = "created"
        if (root / "timeline.json").exists():
            status = "timeline_saved"
        if (root / "analysis.json").exists():
            status = "analyzed"
        if (root / "song.mp3").exists():
            status = "song_uploaded"

        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects(id, title, created_at, updated_at, status, text_length, source_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (project_id, derive_title(text), created_at, updated_at, status, len(text), str(source_path)),
            )
            register_asset(conn, project_id, "source_text", source_path, created_at)
            if (root / "analysis.json").exists():
                register_asset(conn, project_id, "analysis_json", root / "analysis.json", created_at)
            if (root / "timeline.json").exists():
                register_asset(conn, project_id, "timeline_json", root / "timeline.json", created_at)
            if (root / "song.mp3").exists():
                register_asset(conn, project_id, "song_mp3", root / "song.mp3", created_at)
```

Add `register_asset`:

```python
def register_asset(conn, project_id: str, asset_type: str, path: Path, created_at: str, metadata: dict | None = None) -> None:
    if not path.exists():
        return
    conn.execute(
        """
        INSERT INTO project_assets(id, project_id, asset_type, path, filename, size_bytes, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, asset_type, path) DO UPDATE SET
          size_bytes = excluded.size_bytes,
          metadata_json = excluded.metadata_json
        """,
        (
            uuid.uuid4().hex,
            project_id,
            asset_type,
            str(path),
            path.name,
            path.stat().st_size,
            created_at,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
```

- [ ] **Step 4: Initialize and import at startup**

In `backend/app/main.py`, import:

```python
from .services.db import initialize_database
from .services.project_store import import_existing_projects
```

After creating `OUTPUT_DIR`, add:

```python
initialize_database()
import_existing_projects()
```

- [ ] **Step 5: Add project list route**

In `backend/app/routers/projects.py`, import `list_projects`, then add before `@router.post("/projects")`:

```python
@router.get("/projects")
def list_projects_endpoint() -> dict:
    return {"projects": list_projects()}
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_api.py::test_projects_endpoint_lists_persisted_projects tests/test_api.py::test_existing_project_folder_is_imported -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-import
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/main.py backend/app/routers/projects.py backend/app/services/project_store.py tests/test_api.py
git commit -m "feat: restore persisted projects"
```

## Task 3: Persist Analysis And Timeline History

**Files:**
- Modify: `backend/app/services/project_store.py`
- Modify: `backend/app/routers/projects.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for full and chapter analysis persistence**

Append to `tests/test_api.py`:

```python
def test_full_analysis_is_persisted_with_history(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]

    analyzed = client.post(f"/api/projects/{project_id}/analyze")
    history = client.get(f"/api/projects/{project_id}/analyses")
    detail = client.get(f"/api/projects/{project_id}")

    assert analyzed.status_code == 200
    assert history.status_code == 200
    assert history.json()["analyses"][0]["scope"] == "all"
    assert history.json()["analyses"][0]["status"] == "succeeded"
    assert detail.json()["analysis"]["analysisEngine"] == "test-double"
    assert detail.json()["timeline"]["segments"]


def test_chapter_analysis_is_persisted_with_scope(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "第一章 登台\n内容一。\n\n第二章 唱歌\n内容二。"}).json()["id"]

    response = client.post(f"/api/projects/{project_id}/chapters/chap-002/analyze")
    history = client.get(f"/api/projects/{project_id}/analyses")

    assert response.status_code == 200
    assert history.json()["analyses"][0]["scope"] == "chapter"
    assert history.json()["analyses"][0]["chapterId"] == "chap-002"
    assert history.json()["analyses"][0]["chapterTitle"] == "第二章 唱歌"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_api.py::test_full_analysis_is_persisted_with_history tests/test_api.py::test_chapter_analysis_is_persisted_with_scope -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-analysis
```

Expected: fail because analysis history endpoint and scoped save behavior do not exist.

- [ ] **Step 3: Replace `save_analysis` with persistent analysis records**

Change `save_analysis` signature:

```python
def save_analysis(
    project_id: str,
    analysis: dict,
    scope: str = "all",
    chapter_id: str | None = None,
    chapter_title: str | None = None,
) -> dict:
```

Use this implementation:

```python
    project = get_project(project_id)
    now = datetime.now(timezone.utc).isoformat()
    analysis_id = uuid.uuid4().hex[:12]
    analysis_dir = project.root / "analysis"
    result_path = analysis_dir / f"{analysis_id}.json"
    write_json(result_path, analysis)
    write_json(project.analysis_path, analysis)
    write_json(project.timeline_path, analysis["timeline"])

    segment_count = len(analysis.get("segments") or [])
    lyric_count = len([segment for segment in analysis.get("segments") or [] if segment.get("type") == "lyric"])
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO analyses(
              id, project_id, scope, chapter_id, chapter_title, status, engine, result_path,
              segment_count, lyric_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                project_id,
                scope,
                chapter_id,
                chapter_title,
                analysis.get("analysisEngine"),
                str(result_path),
                segment_count,
                lyric_count,
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE projects
            SET status = 'analyzed', updated_at = ?, current_analysis_id = ?
            WHERE id = ?
            """,
            (now, analysis_id, project_id),
        )
        register_asset(conn, project_id, "analysis_json", result_path, now, {"analysisId": analysis_id, "scope": scope})
        register_asset(conn, project_id, "timeline_json", project.timeline_path, now)
    return {"id": analysis_id, **analysis}
```

- [ ] **Step 4: Add analysis history function**

Add:

```python
def list_analyses(project_id: str) -> list[dict]:
    get_project(project_id)
    initialize_database()
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM analyses
            WHERE project_id = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "scope": row["scope"],
            "chapterId": row["chapter_id"],
            "chapterTitle": row["chapter_title"],
            "status": row["status"],
            "engine": row["engine"],
            "segmentCount": row["segment_count"],
            "lyricCount": row["lyric_count"],
            "error": row["error"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
```

- [ ] **Step 5: Update routes to save chapter analyses and expose history**

In `backend/app/routers/projects.py`, import `list_analyses`.

Change full analyze endpoint:

```python
    analysis = analyze_text(project.text)
    saved = save_analysis(project_id, analysis, scope="all")
    return saved
```

Change chapter analyze endpoint:

```python
    analysis = analyze_text(chapter.text, chapters=[chapter])
    saved = save_analysis(project_id, analysis, scope="chapter", chapter_id=chapter.id, chapter_title=chapter.title)
    return saved
```

Add:

```python
@router.get("/projects/{project_id}/analyses")
def list_project_analyses_endpoint(project_id: str) -> dict:
    return {"projectId": project_id, "analyses": list_analyses(project_id)}
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_api.py::test_full_analysis_is_persisted_with_history tests/test_api.py::test_chapter_analysis_is_persisted_with_scope -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-analysis
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/routers/projects.py backend/app/services/project_store.py tests/test_api.py
git commit -m "feat: persist analysis history"
```

## Task 4: Persist Song, Timeline, And Render Records

**Files:**
- Modify: `backend/app/services/project_store.py`
- Modify: `backend/app/services/renderer.py`
- Modify: `backend/app/routers/projects.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing tests for song, timeline, and render persistence**

Append to `tests/test_api.py`:

```python
def test_song_and_timeline_updates_are_reflected_in_project_list(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    timeline = client.post(f"/api/projects/{project_id}/analyze").json()["timeline"]
    timeline["bgmVolume"] = 0.42
    client.patch(f"/api/projects/{project_id}/timeline", json=timeline)
    client.post(
        f"/api/projects/{project_id}/song-file",
        files={"file": ("demo.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00", "audio/mpeg")},
    )

    listed = client.get("/api/projects").json()["projects"]
    item = next(row for row in listed if row["id"] == project_id)
    detail = client.get(f"/api/projects/{project_id}").json()

    assert item["hasSong"] is True
    assert item["hasTimeline"] is True
    assert detail["timeline"]["bgmVolume"] == 0.42


def test_render_result_is_persisted_with_history(monkeypatch):
    monkeypatch.setattr(projects, "analyze_text", fake_analysis)
    project_id = client.post("/api/projects", json={"text": "旁白内容。"}).json()["id"]
    client.post(f"/api/projects/{project_id}/analyze")

    def fake_render(project):
        output_dir = project.output_dir
        output_path = output_dir / "narration.wav"
        output_path.write_bytes(b"RIFFfakeWAVE")
        return {
            "status": "narration-only",
            "message": "测试生成完成。",
            "outputUrl": f"/outputs/{project.id}/{output_path.name}",
            "warnings": [],
        }

    monkeypatch.setattr(projects, "render_project", fake_render)
    rendered = client.post(f"/api/projects/{project_id}/render")
    history = client.get(f"/api/projects/{project_id}/renders")
    detail = client.get(f"/api/projects/{project_id}")

    assert rendered.status_code == 200
    assert history.json()["renders"][0]["status"] == "succeeded"
    assert history.json()["renders"][0]["outputUrl"]
    assert detail.json()["latestRender"]["message"] == "测试生成完成。"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_api.py::test_song_and_timeline_updates_are_reflected_in_project_list tests/test_api.py::test_render_result_is_persisted_with_history -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-render
```

Expected: fail because timeline/song DB updates and render history do not exist.

- [ ] **Step 3: Update song and timeline save functions**

In `save_song_file`, after writing `song.json`, add:

```python
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        register_asset(conn, project_id, "song_mp3", project.song_path, now, song)
        conn.execute("UPDATE projects SET status = 'song_uploaded', updated_at = ? WHERE id = ?", (now, project_id))
```

In `save_timeline`, after `write_json(project.timeline_path, timeline)`, add:

```python
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        register_asset(conn, project_id, "timeline_json", project.timeline_path, now)
        conn.execute("UPDATE projects SET status = 'timeline_saved', updated_at = ? WHERE id = ?", (now, project_id))
```

- [ ] **Step 4: Add render persistence functions**

Add to `project_store.py`:

```python
def create_render_record(project_id: str) -> str:
    get_project(project_id)
    now = datetime.now(timezone.utc).isoformat()
    render_id = uuid.uuid4().hex[:12]
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO renders(id, project_id, status, created_at, updated_at)
            VALUES (?, ?, 'running', ?, ?)
            """,
            (render_id, project_id, now, now),
        )
    return render_id


def complete_render_record(project_id: str, render_id: str, result: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    output_url = result.get("outputUrl")
    output_path = output_url_to_path(project_id, output_url) if output_url else None
    with transaction() as conn:
        conn.execute(
            """
            UPDATE renders
            SET status = 'succeeded', output_path = ?, output_url = ?, message = ?, warnings_json = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                str(output_path) if output_path else None,
                output_url,
                result.get("message"),
                json.dumps(result.get("warnings") or [], ensure_ascii=False),
                now,
                render_id,
                project_id,
            ),
        )
        conn.execute(
            "UPDATE projects SET status = 'rendered', updated_at = ?, current_render_id = ? WHERE id = ?",
            (now, render_id, project_id),
        )
        if output_path and output_path.exists():
            register_asset(conn, project_id, "render_output", output_path, now, {"renderId": render_id})
    return {"id": render_id, **result}


def fail_render_record(project_id: str, render_id: str, error: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        conn.execute(
            "UPDATE renders SET status = 'failed', error = ?, updated_at = ? WHERE id = ? AND project_id = ?",
            (error, now, render_id, project_id),
        )
        conn.execute("UPDATE projects SET status = 'failed', updated_at = ? WHERE id = ?", (now, project_id))


def output_url_to_path(project_id: str, output_url: str) -> Path | None:
    prefix = f"/outputs/{project_id}/"
    if not output_url.startswith(prefix):
        return None
    relative = output_url.removeprefix(prefix)
    return OUTPUTS_DIR / project_id / relative


def list_renders(project_id: str) -> list[dict]:
    get_project(project_id)
    initialize_database()
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM renders
            WHERE project_id = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "status": row["status"],
            "outputUrl": row["output_url"],
            "outputPath": row["output_path"],
            "message": row["message"],
            "warnings": json.loads(row["warnings_json"] or "[]"),
            "error": row["error"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
```

- [ ] **Step 5: Update render route and add render history endpoint**

In `backend/app/routers/projects.py`, import `create_render_record`, `complete_render_record`, `fail_render_record`, and `list_renders`.

Change render endpoint:

```python
@router.post("/projects/{project_id}/render")
def render_project_endpoint(project_id: str) -> dict:
    project = get_project(project_id)
    render_id = create_render_record(project_id)
    try:
        result = render_project(project)
    except HTTPException as exc:
        fail_render_record(project_id, render_id, str(exc.detail))
        raise
    except Exception as exc:
        fail_render_record(project_id, render_id, str(exc))
        raise
    return complete_render_record(project_id, render_id, result)
```

Add:

```python
@router.get("/projects/{project_id}/renders")
def list_project_renders_endpoint(project_id: str) -> dict:
    return {"projectId": project_id, "renders": list_renders(project_id)}
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```powershell
python -m pytest tests/test_api.py::test_song_and_timeline_updates_are_reflected_in_project_list tests/test_api.py::test_render_result_is_persisted_with_history -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-render
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/routers/projects.py backend/app/services/project_store.py tests/test_api.py
git commit -m "feat: persist song timeline and render state"
```

## Task 5: Frontend Project Restore UI

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add project list state and loading helpers**

In `frontend/src/App.jsx`, add state after `project`:

```javascript
  const [projectList, setProjectList] = useState([]);
```

Add an effect after the analysis timer effect:

```javascript
  useEffect(() => {
    loadProjectList();
  }, []);
```

Add helpers before `createProject`:

```javascript
  async function loadProjectList() {
    const data = await request('/api/projects');
    setProjectList(data.projects || []);
  }

  function applyProjectDetail(data) {
    setProject(data);
    setText(data.text || '');
    setAnalysis(data.analysis || null);
    applyChapters(data.analysis?.chapters || chapters);
    setTimeline(data.timeline || emptyTimeline);
    setRenderResult(data.latestRender || null);
  }

  async function loadProject(projectId) {
    if (!projectId) return;
    setBusy('loading-project');
    setError('');
    try {
      const data = await request(`/api/projects/${projectId}`);
      applyProjectDetail(data);
      await loadChapters(data.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  }
```

- [ ] **Step 2: Refresh persisted detail after workflow operations**

In `createProject`, after `setProject(data)`, add:

```javascript
      await loadProjectList();
```

In `analyzeProject`, after `setTimeline(data.timeline);`, add:

```javascript
      await loadProject(project.id);
      await loadProjectList();
```

In `uploadSong`, replace `setProject({ ...project, hasSong: true });` with:

```javascript
      await loadProject(project.id);
      await loadProjectList();
```

In `saveTimeline`, after `setTimeline(data);`, add:

```javascript
      await loadProjectList();
```

In `renderAudio`, after `setRenderResult(data);`, add:

```javascript
      await loadProject(project.id);
      await loadProjectList();
```

- [ ] **Step 3: Render project history in the side panel**

In the side panel, after the "创建项目" button, add:

```jsx
          {projectList.length > 0 && (
            <div className="project-history">
              <PanelTitle icon={<BookOpen />} title="历史项目" />
              {projectList.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`project-history-item ${project?.id === item.id ? 'active' : ''}`}
                  onClick={() => loadProject(item.id)}
                  disabled={busy}
                >
                  <span>{item.title || item.id}</span>
                  <small>{item.status} · {item.textLength} 字</small>
                </button>
              ))}
            </div>
          )}
```

- [ ] **Step 4: Add CSS**

Append to `frontend/src/styles.css`:

```css
.project-history {
  display: grid;
  gap: 8px;
}

.project-history-item {
  align-items: flex-start;
  display: grid;
  gap: 4px;
  justify-items: start;
  min-height: 54px;
  padding: 10px 12px;
  text-align: left;
}

.project-history-item span {
  font-size: 13px;
  font-weight: 700;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
}

.project-history-item small {
  color: var(--muted);
  font-size: 12px;
}

.project-history-item.active {
  border-color: var(--accent);
  color: var(--accent);
}
```

- [ ] **Step 5: Run frontend build**

Run:

```powershell
npm --prefix frontend run build
```

Expected: build succeeds. If it fails with sandbox `spawn EPERM`, record that as environment-related and run the backend test suite.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/App.jsx frontend/src/styles.css
git commit -m "feat: restore projects in frontend"
```

## Task 6: CLI, Docs, And Full Verification

**Files:**
- Modify: `backend/app/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI persistence test**

Append to `tests/test_cli.py`:

```python
def test_cli_created_project_is_listed(monkeypatch):
    monkeypatch.setattr(cli, "analyze_text", fake_analysis)
    source = write_test_txt("cli-persisted-story.txt", "CLI 持久化正文。")

    payload = run([str(source), "--no-analyze"])
    from backend.app.services.project_store import list_projects

    assert any(item["id"] == payload["project"]["id"] for item in list_projects())
```

- [ ] **Step 2: Run test and verify pass or pinpoint missing initialization**

Run:

```powershell
python -m pytest tests/test_cli.py::test_cli_created_project_is_listed -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-cli
```

Expected: pass if `create_project` initializes DB; otherwise fail with missing schema.

- [ ] **Step 3: Add explicit CLI startup initialization if needed**

If the test failed due to missing DB initialization, modify `backend/app/cli.py` imports:

```python
from .services.db import initialize_database
```

In `run`, after `load_env()`, add:

```python
    initialize_database()
```

- [ ] **Step 4: Update README persistence section**

Add this section before "当前能力":

```markdown
## 本地持久化

项目使用 SQLite + 本地文件资产保存完整流程状态：

- `data/app.sqlite3` 保存项目列表、分析历史、渲染历史和资产索引。
- `data/projects/<project_id>/` 保存原始文本、上传 MP3、分析 JSON 和时间轴 JSON。
- `data/outputs/<project_id>/` 保存生成的音频文件。

后端启动时会初始化 SQLite，并扫描已有 `data/projects` 目录导入旧项目。刷新页面或重启服务后，可以从页面左侧历史项目列表重新打开项目，恢复文本、分析结果、时间轴、歌曲状态和最近一次生成结果。
```

- [ ] **Step 5: Run full backend tests**

Run:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-full
```

Expected: pass. If renderer/TTS tests fail because environment config is missing, rerun targeted persistence tests and document the config-related failure separately.

- [ ] **Step 6: Run frontend build**

Run:

```powershell
npm --prefix frontend run build
```

Expected: pass, except known restricted-sandbox `spawn EPERM` issue.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/cli.py README.md tests/test_cli.py
git commit -m "docs: document local persistence"
```

## Final Verification Checklist

- [ ] `python -m pytest -q -p no:cacheprovider --basetemp .\pytest-cache-files-persistence-full`
- [ ] `npm --prefix frontend run build`
- [ ] Manual API smoke:

```powershell
python -m uvicorn backend.app.main:app --port 28080
```

Then create a project, analyze it, save timeline, render with test config or real TTS config, restart backend, and verify `GET /api/projects` and `GET /api/projects/{id}` restore the state.

