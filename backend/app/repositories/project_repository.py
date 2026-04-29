from __future__ import annotations

import json
import uuid
from pathlib import Path

from .db import initialize_database, read_sql, transaction


def create_project_record(project_id: str, title: str, created_at: str, text_length: int, source_path: Path) -> None:
    initialize_database()
    with transaction() as conn:
        conn.execute(
            read_sql("create_project.sql"),
            {
                "id": project_id,
                "title": title,
                "created_at": created_at,
                "text_length": text_length,
                "source_path": str(source_path),
            },
        )
        register_asset(conn, project_id, "source_text", source_path, created_at)


def list_projects() -> list[dict]:
    initialize_database()
    with transaction() as conn:
        rows = conn.execute(read_sql("list_projects.sql")).fetchall()
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


def get_project_row(project_id: str) -> dict | None:
    initialize_database()
    with transaction() as conn:
        row = conn.execute(read_sql("get_project.sql"), {"project_id": project_id}).fetchone()
    return dict(row) if row else None


def get_latest_render(project_id: str) -> dict | None:
    initialize_database()
    with transaction() as conn:
        row = conn.execute(
            read_sql("get_latest_render.sql"),
            {"project_id": project_id},
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


def import_existing_project_record(
    project_id: str,
    title: str,
    created_at: str,
    updated_at: str,
    status: str,
    text_length: int,
    source_path: Path,
    analysis_path: Path | None,
    timeline_path: Path | None,
    song_path: Path | None,
) -> None:
    initialize_database()
    with transaction() as conn:
        conn.execute(
            read_sql("import_project.sql"),
            {
                "id": project_id,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
                "status": status,
                "text_length": text_length,
                "source_path": str(source_path),
            },
        )
        register_asset(conn, project_id, "source_text", source_path, created_at)
        if analysis_path and analysis_path.exists():
            register_asset(conn, project_id, "analysis_json", analysis_path, created_at)
        if timeline_path and timeline_path.exists():
            register_asset(conn, project_id, "timeline_json", timeline_path, created_at)
        if song_path and song_path.exists():
            register_asset(conn, project_id, "song_mp3", song_path, created_at)


def save_analysis_record(
    project_id: str,
    analysis_id: str,
    scope: str,
    chapter_id: str | None,
    chapter_title: str | None,
    engine: str | None,
    result_path: Path,
    timeline_path: Path,
    segment_count: int,
    lyric_count: int,
    created_at: str,
) -> None:
    initialize_database()
    with transaction() as conn:
        conn.execute(
            read_sql("save_analysis.sql"),
            {
                "id": analysis_id,
                "project_id": project_id,
                "scope": scope,
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "engine": engine,
                "result_path": str(result_path),
                "segment_count": segment_count,
                "lyric_count": lyric_count,
                "created_at": created_at,
            },
        )
        conn.execute(
            read_sql("update_project_analysis.sql"),
            {"updated_at": created_at, "analysis_id": analysis_id, "project_id": project_id},
        )
        register_asset(conn, project_id, "analysis_json", result_path, created_at, {"analysisId": analysis_id, "scope": scope})
        register_asset(conn, project_id, "timeline_json", timeline_path, created_at)


def list_analyses(project_id: str) -> list[dict]:
    initialize_database()
    with transaction() as conn:
        rows = conn.execute(
            read_sql("list_analyses.sql"),
            {"project_id": project_id},
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


def register_asset(conn, project_id: str, asset_type: str, path: Path, created_at: str, metadata: dict | None = None) -> None:
    if not path.exists():
        return
    conn.execute(
        read_sql("register_asset.sql"),
        {
            "id": uuid.uuid4().hex,
            "project_id": project_id,
            "asset_type": asset_type,
            "path": str(path),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "created_at": created_at,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        },
    )
