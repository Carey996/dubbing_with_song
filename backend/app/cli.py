from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from fastapi import HTTPException

from .config import load_env
from .services.analyzer import analyze_text
from .services.chapter_service import split_text_into_chapters
from .services.project_store import create_project, get_project, save_analysis, save_song_file
from .services.renderer import render_project
from .services.text_source import read_txt_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dubbing-with-song",
        description="Create and optionally render a dubbing project from a local txt file.",
    )
    parser.add_argument("txt_file", type=Path, help="UTF-8 .txt source file.")
    parser.add_argument("--song", type=Path, help="Optional user-provided MP3 file.")
    parser.add_argument("--render", action="store_true", help="Render output audio after analysis.")
    parser.add_argument("--no-analyze", action="store_true", help="Only create the project; skip text analysis.")
    parser.add_argument("--chapters-only", action="store_true", help="Create the project and print chapter split only.")
    parser.add_argument("--chapter", help="Analyze only one chapter id, for example chap-001.")
    parser.add_argument("--include-analysis", action="store_true", help="Print full analysis payload.")
    return parser


def run(argv: Sequence[str] | None = None) -> dict:
    load_env()
    args = build_parser().parse_args(argv)
    text = read_txt_file(args.txt_file)
    project = create_project(text)

    payload = {
        "project": project.public_dict(),
        "input": {
            "txtFile": str(args.txt_file),
        },
    }
    chapters = split_text_into_chapters(project.text)
    payload["chapters"] = [chapter.public_dict() for chapter in chapters]

    if args.chapters_only:
        return payload

    should_analyze = not args.no_analyze or args.render
    if should_analyze:
        target_chapters = chapters
        target_text = project.text
        if args.chapter:
            target_chapter = next((chapter for chapter in chapters if chapter.id == args.chapter), None)
            if not target_chapter:
                raise HTTPException(status_code=404, detail=f"Chapter not found: {args.chapter}")
            target_chapters = [target_chapter]
            target_text = target_chapter.text

        analysis = analyze_text(target_text, chapters=target_chapters)
        save_analysis(project.id, analysis)
        payload["analysis"] = {
            "scope": args.chapter or "all",
            "segmentCount": len(analysis["segments"]),
            "lyricCount": len([segment for segment in analysis["segments"] if segment["type"] == "lyric"]),
            "songCandidates": analysis["songCandidates"],
        }
        if args.include_analysis:
            payload["analysis"]["payload"] = analysis

    if args.song:
        if not args.song.name.lower().endswith(".mp3"):
            raise HTTPException(status_code=400, detail="Only .mp3 song files are supported.")
        payload["song"] = save_song_file(project.id, args.song.name, args.song.read_bytes())

    if args.render:
        payload["render"] = render_project(get_project(project.id))

    return payload


def main(argv: Sequence[str] | None = None) -> None:
    try:
        payload = run(argv)
    except HTTPException as exc:
        raise SystemExit(f"Error: {exc.detail}") from exc

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
