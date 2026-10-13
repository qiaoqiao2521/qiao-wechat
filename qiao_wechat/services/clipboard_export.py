"""Read-only static article export, independent of accounts and draft state."""
from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup, NavigableString

from .html_normalizer import HtmlNormalizer
from .wechat_export import export_source_links, html_budget, semantic_snapshot, validate_preserved, _safe_http_url

# This is the supported static component contract, not a claim about every
# WeChat client tag. Interactive SVG stays in the existing raster export path.
_STATIC_TAGS = frozenset({
    'section', 'article', 'div', 'p', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'b', 'em', 'i', 'u', 's', 'del', 'sup', 'sub', 'br', 'hr',
    'blockquote', 'pre', 'code', 'ul', 'ol', 'li', 'table', 'thead', 'tbody',
    'tfoot', 'tr', 'td', 'th', 'img', 'a', 'figure', 'figcaption',
})
_BLOCKS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'li', 'tr', 'figcaption'}


def _check_static_fragment(soup: BeautifulSoup) -> None:
    if soup.find(True) is None:
        raise ValueError('No saved article layout is available')
    for node in soup.find_all(True):
        if node.name not in _STATIC_TAGS:
            raise ValueError('Static clipboard export does not support: ' + node.name)
        if any(key.lower().startswith('on') for key in node.attrs):
            raise ValueError('Article layout contains an event handler')
        if any(key.lower().startswith(('tn-', 'ng-')) for key in node.attrs):
            raise ValueError('Editor template bindings must be compiled before export')
        if re.search(r'expression\s*\(|@import|behavior\s*:|url\s*\(|position\s*:\s*fixed', str(node.get('style', '')), re.I):
            raise ValueError('Article layout contains unsupported active or external CSS')
        if node.name == 'img':
            src = node.get('src') or node.get('data-src')
            if not isinstance(src, str) or not _safe_http_url(src):
                raise ValueError('Upload article images before copying the layout')
            if node.get('src') and node.get('data-src') and node['src'] != node['data-src']:
                raise ValueError('Article image has ambiguous source URLs')
        if node.name == 'a' and node.get('href') and not _safe_http_url(str(node['href'])):
            raise ValueError('Article link is not an HTTP source URL')


def _plain_text(content: str) -> str:
    soup = BeautifulSoup(content, 'html.parser')
    for node in soup.find_all('br'):
        node.replace_with(NavigableString('\n'))
    for node in soup.find_all(_BLOCKS):
        node.append(NavigableString('\n'))
    return soup.get_text().strip()


def prepare_clipboard_export(content: str) -> dict:
    """Export a body fragment with inline styles and both clipboard MIME values.

    Does not inspect accounts, call the network, upload images, or write state.
    Source HTML remains the owner's input; the result is a derived artifact.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError('No saved article layout is available')
    source = BeautifulSoup(content, 'html.parser')
    _check_static_fragment(source)
    original = semantic_snapshot(content)
    normalizer = HtmlNormalizer()
    normalized = normalizer.normalize(content)
    exported, _ = export_source_links(normalized)
    exported = normalizer.compact(exported)
    _check_static_fragment(BeautifulSoup(exported, 'html.parser'))
    validate_preserved(original, exported)
    budget = html_budget(exported)
    if budget['remaining_chars'] < 0 or budget['remaining_utf8_bytes'] < 0:
        raise ValueError('Article layout exceeds the shared WeChat export budget')
    return {'html': exported, 'plain_text': _plain_text(exported),
            'html_sha256': hashlib.sha256(exported.encode()).hexdigest(), 'budget': budget}
