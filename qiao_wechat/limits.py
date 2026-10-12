"""Shared text and cover contracts for WeChat draft producers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_TITLE_CHARS = 32
MAX_DIGEST_CHARS = 120
MAX_CONTENT_CHARS = 20_000
MAX_CONTENT_BYTES = 1_048_576


def validate_draft_text(title: str, digest: str | None) -> None:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("WeChat draft title is required")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"WeChat draft title must be at most {MAX_TITLE_CHARS} characters")
    if digest is not None and (not isinstance(digest, str) or len(digest) > MAX_DIGEST_CHARS):
        raise ValueError(f"WeChat draft digest must be at most {MAX_DIGEST_CHARS} characters")


def normalize_cover_info(cover_info: Any) -> dict[str, Any] | None:
    """Validate both crop windows and canonicalize numbers for stable request keys.

    Missing cover_info retains compatibility with old articles. Coordinates refer
    to the uploaded mother image, not either exported crop preview.
    """
    if cover_info is None:
        return None
    if not isinstance(cover_info, dict) or set(cover_info) != {"crop_percent_list"}:
        raise ValueError("cover_info must contain only crop_percent_list")
    crops = cover_info["crop_percent_list"]
    if not isinstance(crops, list) or len(crops) != 2:
        raise ValueError("cover_info requires one 2.35_1 crop and one 1_1 crop")
    normalized: dict[str, dict[str, str]] = {}
    for crop in crops:
        if not isinstance(crop, dict) or set(crop) != {"ratio", "x1", "y1", "x2", "y2"}:
            raise ValueError("each cover crop requires ratio, x1, y1, x2 and y2")
        ratio = crop["ratio"]
        if not isinstance(ratio, str) or ratio not in {"2.35_1", "1_1"} or ratio in normalized:
            raise ValueError("cover_info crop ratios must be unique: 2.35_1 and 1_1")
        coordinates: dict[str, Decimal] = {}
        for name in ("x1", "y1", "x2", "y2"):
            value = crop[name]
            if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
                raise ValueError(f"cover_info {name} must be a finite normalized number")
            try:
                number = Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError(f"invalid cover_info coordinate: {name}") from exc
            if not number.is_finite() or not 0 <= number <= 1:
                raise ValueError(f"cover_info {name} must be between 0 and 1")
            coordinates[name] = number
        if not (coordinates["x1"] < coordinates["x2"] and coordinates["y1"] < coordinates["y2"]):
            raise ValueError("cover_info crop rectangles must have positive width and height")
        normalized[ratio] = {"ratio": ratio, **{
            name: format(number.normalize(), "f") for name, number in coordinates.items()
        }}
    return {"crop_percent_list": [normalized[ratio] for ratio in ("2.35_1", "1_1")]}
