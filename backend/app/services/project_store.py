from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .db import initialize_database, transaction


BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
OUTPUTS_DIR = DATA_DIR / "outputs"


@dataclass
class Project:
    id: str
    text: str
    root: Path
    metadata: dict[str, Any]

    @property
    def analysis_path(self) -> Path:
        return self.root / "analysis.json"

    @property
    def timeline_path(self) -> Path:
        return self.root / "timeline.json"

    @property
    def song_path(self) -> Path:
        return self.root / "song.mp3"

    @property
    def output_dir(self) -> Path:
        output_dir = OUTPUTS_DIR / self.id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def public_dict(self, include_text: bool = False) -> dict:
        payload = {
            "id": self.id,
            "createdAt": self.metadata["createdAt"],
            "textLength": len(self.text),
            "hasAnalysis": self.analysis_path.exists(),
            "hasSong": self.song_path.exists(),
            "hasTimeline": self.timeline_path.exists(),
        }
        if include_text:
            payload["text"] = self.text
        if self.analysis_path.exists():
            payload["analysis"] = read_json(self.analysis_path)
        if self.timeline_path.exists():
            payload["timeline"] = read_json(self.timeline_path)
        return payload


def create_project(text: str) -> Project:
    initialize_database()
    normalized = (text or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Text content is required.")

    project_id = uuid.uuid4().hex[:12]
    root = PROJECTS_DIR / project_id
    root.mkdir(parents=True, exist_ok=False)
    metadata = {
        "id": project_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    source_path = root / "source.txt"
    source_path.write_text(normalized, encoding="utf-8")
    write_json(root / "metadata.json", metadata)
    now = metadata["createdAt"]
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO projects(id, title, created_at, updated_at, status, text_length, source_path)
            VALUES (?, ?, ?, ?, 'created', ?, ?)
            """,
            (project_id, derive_title(normalized), now, now, len(normalized), str(source_path)),
        )
        register_asset(conn, project_id, "source_text", source_path, now)
    return Project(project_id, normalized, root, metadata)


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


def get_project(project_id: str) -> Project:
    if not project_id or not project_id.replace("-", "").isalnum():
        raise HTTPException(status_code=404, detail="Project not found.")

    root = PROJECTS_DIR / project_id
    metadata_path = root / "metadata.json"
    source_path = root / "source.txt"
    if not metadata_path.exists() or not source_path.exists():
        raise HTTPException(status_code=404, detail="Project not found.")

    return Project(
        id=project_id,
        text=source_path.read_text(encoding="utf-8"),
        root=root,
        metadata=read_json(metadata_path),
    )


def save_analysis(project_id: str, analysis: dict) -> None:
    project = get_project(project_id)
    write_json(project.analysis_path, analysis)
    write_json(project.timeline_path, analysis["timeline"])


def save_song_file(project_id: str, filename: str, content: bytes) -> dict:
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded MP3 is empty.")

    project = get_project(project_id)
    project.song_path.write_bytes(content)
    song = {
        "filename": filename,
        "size": len(content),
        "url": f"/api/projects/{project_id}/song-file",
    }
    write_json(project.root / "song.json", song)
    return song


def save_timeline(project_id: str, payload: dict) -> dict:
    project = get_project(project_id)
    timeline = {
        "bgmVolume": clamp_float(payload.get("bgmVolume", 0.22), 0.0, 1.0),
        "narrationVolume": clamp_float(payload.get("narrationVolume", 1.0), 0.0, 1.5),
        "songStartSec": max(0.0, float(payload.get("songStartSec", 0.0) or 0.0)),
        "segments": normalize_segments(payload.get("segments") or []),
    }
    write_json(project.timeline_path, timeline)
    return timeline


def read_timeline(project: Project) -> dict:
    if project.timeline_path.exists():
        return read_json(project.timeline_path)
    if project.analysis_path.exists():
        analysis = read_json(project.analysis_path)
        return analysis["timeline"]
    raise HTTPException(status_code=400, detail="Analyze the project before rendering.")


def normalize_segments(segments: list[dict]) -> list[dict]:
    normalized = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        duration = max(0.5, float(segment.get("durationSec", 2.0) or 2.0))
        kind = segment.get("type") if segment.get("type") in {"narration", "lyric"} else "narration"
        normalized.append(
            {
                "id": str(segment.get("id") or f"seg-{index + 1:03d}"),
                "index": index,
                "type": kind,
                "text": str(segment.get("text") or ""),
                "startSec": round(cursor, 2),
                "durationSec": round(duration, 2),
                "confidence": float(segment.get("confidence", 0.5) or 0.5),
                "reason": str(segment.get("reason") or ""),
                "songClipStartSec": max(0.0, float(segment.get("songClipStartSec", 0.0) or 0.0)),
                "songClipEndSec": max(0.0, float(segment.get("songClipEndSec", duration) or duration)),
            }
        )
        cursor += duration
    return normalized


def clamp_float(value: Any, min_value: float, max_value: float) -> float:
    return min(max(float(value), min_value), max_value)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def derive_title(text: str) -> str:
    for line in text.splitlines():
        title = line.strip()
        if title:
            return title[:40]
    return "未命名项目"


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
