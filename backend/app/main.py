from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import load_env, resolve_data_dir
from .repositories.db import initialize_database
from .routers import projects
from .services.project_store import import_existing_projects


load_env()

DATA_DIR = resolve_data_dir()
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


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a serialisable 422.

    FastAPI's default handler echoes the rejected input back to the client. When that input is
    a JSON Infinity/NaN literal the response itself cannot be serialised (Starlette refuses
    non-finite floats), so the request failed with a 500 instead of a validation error.
    """
    detail = [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


app.include_router(projects.router, prefix="/api")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
