from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from ..services.analyzer import analyze_text
from ..services.project_store import (
    create_project,
    get_project,
    save_analysis,
    save_song_file,
    save_timeline,
)
from ..services.renderer import render_project
from ..services.text_source import read_txt_upload

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    text: str


@router.post("/projects")
def create_project_endpoint(payload: CreateProjectRequest) -> dict:
    project = create_project(payload.text)
    return project.public_dict()


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
    save_analysis(project_id, analysis)
    return analysis


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


@router.patch("/projects/{project_id}/timeline")
async def update_timeline_endpoint(project_id: str, request: Request) -> dict:
    get_project(project_id)
    payload = await request.json()
    timeline = save_timeline(project_id, payload)
    return timeline


@router.post("/projects/{project_id}/render")
def render_project_endpoint(project_id: str) -> dict:
    project = get_project(project_id)
    result = render_project(project)
    return result


@router.get("/projects/{project_id}")
def get_project_endpoint(project_id: str) -> dict:
    return get_project(project_id).public_dict(include_text=True)
