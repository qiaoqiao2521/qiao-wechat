from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Article, JobStatus, PublishChannel, PublishJob


class XhsExporter:
    """Safe 小红书 path: export a publish package instead of bypassing platform login.

    You can later add a compliant OpenAPI/MCP adapter around this output.
    """

    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()

    CARD_TEXT_LIMIT = 120
    CARD_POINTS = 3

    def export_article(self, article_id: int, *, tags: Iterable[str] = ()) -> Path:
        article = self.session.get(Article, article_id)
        if not article:
            raise KeyError(f"article not found: {article_id}")
        note = self._to_note(article.markdown, article.title, tags=tags)
        cards = self._plan_cards(article.markdown, article.title, tags=tags)
        out_dir = self.settings.output_dir / f"article_{article.id}" / "xhs"
        out_dir.mkdir(parents=True, exist_ok=True)
        note_path = out_dir / "note.txt"
        note_path.write_text(note, encoding="utf-8")
        cards_path = out_dir / "cards.json"
        cards_path.write_text(
            json.dumps(cards, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "cards.md").write_text(self._cards_to_markdown(cards), encoding="utf-8")
        article.xhs_note = note
        article.meta = {
            **(article.meta or {}),
            "xhs_cards_path": str(cards_path),
            "xhs_cards_count": len(cards["cards"]),
        }
        self.session.add(
            PublishJob(
                article_id=article.id,
                account_id=article.account_id,
                channel=PublishChannel.xhs_export,
                status=JobStatus.success,
                response_json={"path": str(note_path), "cards": str(cards_path)},
            )
        )
        self.session.flush()
        return note_path

    def _plan_cards(self, markdown: str, title: str, *, tags: Iterable[str]) -> dict[str, Any]:
        """图片卡片规划：封面卡 + 内容卡，供人工在任意设计工具里制卡。

        仅输出规划（文本切块与要点），不生成图片、不触碰任何平台接口。
        """
        sections = self._split_sections(markdown)
        cards: list[dict[str, Any]] = [
            {
                "index": 1,
                "kind": "cover",
                "title": title[:20],
                "subtitle": self._first_sentence(sections[0]["text"]) if sections else "",
                "points": [],
                "hint": "封面卡：大字标题 + 一句钩子，建议 3:4 竖版",
            }
        ]
        for section in sections:
            points = [p for p in section["points"] if p][: self.CARD_POINTS]
            if not points:
                continue
            cards.append(
                {
                    "index": len(cards) + 1,
                    "kind": "content",
                    "title": section["title"][:20] or f"要点 {len(cards)}",
                    "subtitle": "",
                    "points": [p[: self.CARD_TEXT_LIMIT] for p in points],
                    "hint": "内容卡：标题 + 要点列表，保持与封面同一配色",
                }
            )
        tag_list = [t.strip().lstrip("#") for t in tags if t.strip()]
        return {
            "title": title,
            "cards": cards,
            "tags": [f"#{t}" for t in tag_list],
            "note": "卡片为人工制卡规划；发布仍走人工路径，本工具不触碰平台接口。",
        }

    @staticmethod
    def _cards_to_markdown(cards: dict[str, Any]) -> str:
        lines = [f"# 图片卡片规划：{cards['title']}", ""]
        for card in cards["cards"]:
            kind = "封面卡" if card["kind"] == "cover" else f"内容卡 {card['index'] - 1}"
            lines.append(f"## 卡 {card['index']}（{kind}）")
            lines.append(f"- 标题：{card['title']}")
            if card.get("subtitle"):
                lines.append(f"- 副题：{card['subtitle']}")
            for point in card.get("points", []):
                lines.append(f"- 要点：{point}")
            lines.append(f"- 提示：{card['hint']}")
            lines.append("")
        if cards.get("tags"):
            lines.append("标签：" + " ".join(cards["tags"]))
            lines.append("")
        lines.append(f"> {cards['note']}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _split_sections(markdown: str) -> list[dict[str, str | list[str]]]:
        """按二级标题切块；无标题时按空行段落切块。"""
        text = XhsExporter._strip_markdown(markdown)
        lines = [line.strip() for line in text.splitlines()]
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in lines:
            if not line:
                continue
            if line.startswith("#"):
                current = {"title": line.lstrip("#").strip(), "text": "", "points": []}
                sections.append(current)
                continue
            if current is None:
                current = {"title": "", "text": "", "points": []}
                sections.append(current)
            current["text"] += line
            current["points"].append(XhsExporter._first_sentence(line))
        result: list[dict[str, Any]] = []
        for section in sections:
            body = (section["text"] or "").strip()
            if not body and not section["points"]:
                continue
            result.append(
                {
                    "title": str(section["title"]),
                    "text": body,
                    "points": [p for p in section["points"] if p],
                }
            )
        return result

    @staticmethod
    def _first_sentence(text: str) -> str:
        cleaned = XhsExporter._strip_markdown(text).strip()
        for sep in ["。", "！", "？", "；"]:
            idx = cleaned.find(sep)
            if 0 < idx <= 60:
                return cleaned[: idx + 1]
        return cleaned[:60]

    @staticmethod
    def _strip_markdown(text: str) -> str:
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"[*_`>-]", "", text)
        return text

    @staticmethod
    def _to_note(markdown: str, title: str, *, tags: Iterable[str]) -> str:
        text = markdown
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
        text = re.sub(r"[*_`>~-]", "", text)
        text = BeautifulSoup(text, "html.parser").get_text("\n")
        chunks = [line.strip() for line in text.splitlines() if line.strip()]
        body = "\n\n".join(chunks[:12])
        if len(body) > 900:
            body = body[:880].rstrip() + "……"
        tag_text = " ".join("#" + t.strip().lstrip("#") for t in tags if t.strip())
        return f"{title}\n\n{body}\n\n{tag_text}".strip() + "\n"
