from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..repositories import project_repository


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
        latest_render = project_repository.get_latest_render(self.id)
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
        if include_text:
            payload["text"] = self.text
        if self.analysis_path.exists():
            payload["analysis"] = read_json(self.analysis_path)
        if self.timeline_path.exists():
            payload["timeline"] = read_json(self.timeline_path)
        return payload


def create_project(text: str) -> Project:
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
    project_repository.create_project_record(project_id, derive_title(normalized), now, len(normalized), source_path)
    return Project(project_id, normalized, root, metadata)


def list_projects() -> list[dict]:
    return project_repository.list_projects()


def get_project(project_id: str) -> Project:
    if not project_id or not project_id.replace("-", "").isalnum():
        raise HTTPException(status_code=404, detail="Project not found.")

    root = PROJECTS_DIR / project_id
    metadata_path = root / "metadata.json"
    source_path = root / "source.txt"
    if not metadata_path.exists() or not source_path.exists():
        raise HTTPException(status_code=404, detail="Project not found.")

    metadata = read_json(metadata_path)
    row = project_repository.get_project_row(project_id)
    if row:
        metadata = {
            **metadata,
            "title": row["title"],
            "updatedAt": row["updated_at"],
            "status": row["status"],
        }

    return Project(
        id=project_id,
        text=source_path.read_text(encoding="utf-8"),
        root=root,
        metadata=metadata,
    )

def import_existing_projects() -> None:
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

        project_repository.import_existing_project_record(
            project_id=project_id,
            title=derive_title(text),
            created_at=created_at,
            updated_at=updated_at,
            status=status,
            text_length=len(text),
            source_path=source_path,
            analysis_path=root / "analysis.json",
            timeline_path=root / "timeline.json",
            song_path=root / "song.mp3",
        )


def save_analysis(
    project_id: str,
    analysis: dict,
    scope: str = "all",
    chapter_id: str | None = None,
    chapter_title: str | None = None,
) -> dict:
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
    project_repository.save_analysis_record(
        project_id=project_id,
        analysis_id=analysis_id,
        scope=scope,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        engine=analysis.get("analysisEngine"),
        result_path=result_path,
        timeline_path=project.timeline_path,
        segment_count=segment_count,
        lyric_count=lyric_count,
        created_at=now,
    )
    return {"id": analysis_id, **analysis}


def list_analyses(project_id: str) -> list[dict]:
    get_project(project_id)
    return project_repository.list_analyses(project_id)


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
