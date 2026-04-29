from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import load_env
from .repositories.db import initialize_database
from .routers import projects
from .services.project_store import import_existing_projects


load_env()

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
initialize_database()
import_existing_projects()

app = FastAPI(title="Dubbing With Song MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
