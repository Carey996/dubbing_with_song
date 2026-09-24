from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import FileResponse
from starlette.datastructures import UploadFile

from ..services.analyzer import analyze_text
from ..services.chapter_service import split_text_into_chapters
from ..services.lrc import enrich_analysis_with_lrc, parse_lrc
from ..services.project_store import (
    complete_render_record,
    create_project,
    create_render_record,
    delete_project,
    fail_render_record,
    get_project,
    list_analyses,
    list_projects,
    list_renders,
    save_chapter_lrc_file,
    save_chapter_song_file,
    save_analysis,
    save_song_file,
    save_timeline,
)
from ..services.renderer import render_project
from ..services.text_source import read_txt_upload

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    text: str


@router.get("/projects")
def list_projects_endpoint() -> dict:
    return {"projects": list_projects()}


@router.post("/projects")
def create_project_endpoint(payload: CreateProjectRequest) -> dict:
    project = create_project(payload.text)
    return project.public_dict()


@router.delete("/projects/{project_id}")
def delete_project_endpoint(project_id: str) -> dict:
    return delete_project(project_id)


@router.post("/projects/text-file")
async def create_project_from_text_file_endpoint(request: Request) -> dict:
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Missing txt file field named 'file'.")

    text = await read_txt_upload(upload)
    project = create_project(text)
    return project.public_dict()


@router.post("/projects/{project_id}/analyze")
def analyze_project_endpoint(project_id: str) -> dict:
    project = get_project(project_id)
    analysis = analyze_text(project.text)
    return save_analysis(project_id, analysis, scope="all")


@router.post("/projects/{project_id}/chapters/{chapter_id}/analyze")
def analyze_project_chapter_endpoint(project_id: str, chapter_id: str) -> dict:
    project = get_project(project_id)
    chapters = split_text_into_chapters(project.text)
    chapter = next((item for item in chapters if item.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")

    analysis = analyze_text(chapter.text, chapters=[chapter])
    analysis["chapters"] = [item.public_dict() for item in chapters]
    if project.chapter_lrc_path(chapter.id).exists():
        lrc_lines = parse_lrc(project.chapter_lrc_path(chapter.id).read_text(encoding="utf-8"))
        analysis = enrich_analysis_with_lrc(analysis, lrc_lines)
    return save_analysis(project_id, analysis, scope="chapter", chapter_id=chapter.id, chapter_title=chapter.title)


@router.get("/projects/{project_id}/analyses")
def list_project_analyses_endpoint(project_id: str) -> dict:
    return {"projectId": project_id, "analyses": list_analyses(project_id)}


@router.get("/projects/{project_id}/chapters")
def list_project_chapters_endpoint(project_id: str) -> dict:
    project = get_project(project_id)
    chapters = split_text_into_chapters(project.text)
    return {
        "projectId": project.id,
        "chapters": [chapter_with_assets(project, chapter) for chapter in chapters],
    }


@router.post("/projects/{project_id}/song-file")
async def upload_song_endpoint(project_id: str, request: Request) -> dict:
    project = get_project(project_id)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Missing MP3 file field named 'file'.")

    filename = Path(upload.filename or "song.mp3").name
    if not filename.lower().endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Only .mp3 files are supported in the MVP.")

    song = save_song_file(project.id, filename, await upload.read())
    return song


@router.get("/projects/{project_id}/song-file")
def get_song_endpoint(project_id: str) -> FileResponse:
    project = get_project(project_id)
    if not project.song_path.exists():
        raise HTTPException(status_code=404, detail="Song file not found.")

    return FileResponse(project.song_path, media_type="audio/mpeg", filename="song.mp3")


@router.post("/projects/{project_id}/chapters/{chapter_id}/song-file")
async def upload_chapter_song_endpoint(project_id: str, chapter_id: str, request: Request) -> dict:
    project = get_project(project_id)
    require_chapter(project, chapter_id)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Missing MP3 file field named 'file'.")

    filename = Path(upload.filename or "song.mp3").name
    if not filename.lower().endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Only .mp3 files are supported for chapter songs.")

    return save_chapter_song_file(project.id, chapter_id, filename, await upload.read())


@router.get("/projects/{project_id}/chapters/{chapter_id}/song-file")
def get_chapter_song_endpoint(project_id: str, chapter_id: str) -> FileResponse:
    project = get_project(project_id)
    require_chapter(project, chapter_id)
    path = project.chapter_song_path(chapter_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chapter song file not found.")

    return FileResponse(path, media_type="audio/mpeg", filename="song.mp3")


@router.post("/projects/{project_id}/chapters/{chapter_id}/lyric-file")
async def upload_chapter_lyric_endpoint(project_id: str, chapter_id: str, request: Request) -> dict:
    project = get_project(project_id)
    require_chapter(project, chapter_id)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Missing LRC file field named 'file'.")

    filename = Path(upload.filename or "lyrics.lrc").name
    if not filename.lower().endswith(".lrc"):
        raise HTTPException(status_code=400, detail="Only .lrc lyric files are supported.")

    content = await upload.read()
    parse_lrc(content.decode("utf-8-sig"))
    return save_chapter_lrc_file(project.id, chapter_id, filename, content)


@router.get("/projects/{project_id}/chapters/{chapter_id}/lyric-file")
def get_chapter_lyric_endpoint(project_id: str, chapter_id: str) -> FileResponse:
    project = get_project(project_id)
    require_chapter(project, chapter_id)
    path = project.chapter_lrc_path(chapter_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Chapter LRC file not found.")

    return FileResponse(path, media_type="text/plain; charset=utf-8", filename="lyrics.lrc")


@router.patch("/projects/{project_id}/timeline")
async def update_timeline_endpoint(project_id: str, request: Request) -> dict:
    get_project(project_id)
    payload = await request.json()
    timeline = save_timeline(project_id, payload)
    return timeline


@router.post("/projects/{project_id}/render")
def render_project_endpoint(project_id: str) -> dict:
    render_id = create_render_record(project_id)
    project = get_project(project_id)
    project.metadata["renderId"] = render_id
    try:
        result = render_project(project)
    except HTTPException as exc:
        fail_render_record(project_id, render_id, str(exc.detail))
        raise
    except Exception as exc:
        fail_render_record(project_id, render_id, str(exc))
        raise
    return complete_render_record(project_id, render_id, result)


@router.get("/projects/{project_id}/renders")
def list_project_renders_endpoint(project_id: str) -> dict:
    return {"projectId": project_id, "renders": list_renders(project_id)}


@router.get("/projects/{project_id}")
def get_project_endpoint(project_id: str) -> dict:
    return get_project(project_id).public_dict(include_text=True)


def require_chapter(project, chapter_id: str):
    chapters = split_text_into_chapters(project.text)
    chapter = next((item for item in chapters if item.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    return chapter


def chapter_with_assets(project, chapter) -> dict:
    payload = chapter.public_dict(include_text=True)
    song_path = project.chapter_song_path(chapter.id)
    lrc_path = project.chapter_lrc_path(chapter.id)
    payload["chapterSong"] = {
        "chapterId": chapter.id,
        "filename": "song.mp3",
        "url": f"/api/projects/{project.id}/chapters/{chapter.id}/song-file",
    } if song_path.exists() else None
    payload["chapterLyric"] = {
        "chapterId": chapter.id,
        "filename": "lyrics.lrc",
        "url": f"/api/projects/{project.id}/chapters/{chapter.id}/lyric-file",
    } if lrc_path.exists() else None
    return payload
