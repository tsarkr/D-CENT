"""Shared helpers for the row-level TEI annotation format."""

import re


_RB_PATTERN = re.compile(r"<rb[^>]*>([^<]*)</rb>", re.DOTALL)
_RT_PATTERN = re.compile(r"<rt[^>]*>([^<]*)</rt>", re.DOTALL)


def extract_ruby_parts(tag_content: str) -> tuple[str, str]:
    """Return concatenated ruby base text and readings from a TEI fragment."""
    bases = " ".join(match.strip() for match in _RB_PATTERN.findall(tag_content) if match.strip())
    readings = " ".join(match.strip() for match in _RT_PATTERN.findall(tag_content) if match.strip())
    return bases, readings


def extract_tei_surface(tag) -> tuple[str, str]:
    """Return a tag's base spelling and all ruby readings without dropping rb text."""
    readings = [rt.get_text("", strip=True) for rt in tag.find_all("rt")]
    for rt in tag.find_all("rt"):
        rt.extract()
    surface = " ".join(tag.get_text(" ", strip=True).split())
    return surface, " ".join(reading for reading in readings if reading)
