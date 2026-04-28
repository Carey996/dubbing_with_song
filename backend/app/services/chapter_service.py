from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChapter:
    id: str
    title: str
    text: str

    def public_dict(self, include_text: bool = False) -> dict:
        payload = {
            "id": self.id,
            "title": self.title,
            "textLength": len(self.text),
        }
        if include_text:
            payload["text"] = self.text
        return payload


def split_text_into_chapters(text: str) -> list[TextChapter]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    lines = normalized.splitlines()
    chapters: list[TextChapter] = []
    current_title = "正文"
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if is_chapter_heading(stripped):
            if current_lines:
                chapters.append(make_chapter(len(chapters), current_title, "\n".join(current_lines)))
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        chapters.append(make_chapter(len(chapters), current_title, "\n".join(current_lines)))

    if not chapters:
        chapters.append(make_chapter(0, "正文", normalized))

    return chapters


def is_chapter_heading(line: str) -> bool:
    if not line or len(line) > 60:
        return False
    patterns = [
        r"^第[一二三四五六七八九十百千万\d]+[章节回集卷部篇].*$",
        r"^Chapter\s+\d+.*$",
        r"^CHAPTER\s+\d+.*$",
        r"^\d+[\.、]\s*\S+.*$",
        r"^#{1,3}\s+\S+.*$",
    ]
    return any(re.match(pattern, line) for pattern in patterns)


def make_chapter(index: int, title: str, text: str) -> TextChapter:
    return TextChapter(id=f"chap-{index + 1:03d}", title=title.strip() or "正文", text=normalize_text(text))


def normalize_text(text: str) -> str:
    return re.sub(r"\r\n?", "\n", text or "").strip()
