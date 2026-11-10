"""Safe HTML for PR descriptions: a small Markdown subset plus Jira macros.

v3 rendered descriptions with ``markdown2`` (``safe_mode="escape"``) and then
the Jira ``{color}`` / ``{^}`` macros. markdown2 is not a v4 dependency, so
this covers what PR descriptions use in practice. The input is always HTML
escaped first; only the markup produced here reaches the page, and colour
values are limited to ``#hex`` or a word.
"""

from __future__ import annotations

import re

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

_COLOR = re.compile(r"\{color:(#[0-9a-fA-F]{3,8}|[a-zA-Z]+)\}(.*?)\{color\}", re.S)
_COLOR_LEFTOVER = re.compile(r"\{color(:[^}]*)?\}")
_SUP = re.compile(r"\{\^\}(.*?)\{\^\}", re.S)
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[*_](?=\S)([^*_\n]+?)(?<=\S)[*_](?![\w*])")
# Code spans, [label](https://...) links and bare URLs are cut out before emphasis runs.
_TOKENS = re.compile(r"`([^`\n]+)`|\[([^\]\n]+)\]\((https?://[^\s)\"<]+)\)|(https?://[^\s<\"]+)")
_HEADING = re.compile(r"^(?:(#{1,6})\s+|h([1-6])\.\s+)(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _emphasis(text: str) -> str:
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", text))


def _link(href: str, label: str) -> str:
    return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _inline(text: str) -> str:
    out, pos = [], 0
    for m in _TOKENS.finditer(text):
        out.append(_emphasis(text[pos:m.start()]))
        code, label, href, bare = m.groups()
        if code is not None:
            out.append(f"<code>{code}</code>")
        elif href:
            out.append(_link(href, label))
        else:
            out.append(_link(bare, bare))
        pos = m.end()
    out.append(_emphasis(text[pos:]))
    return "".join(out)


def render_description(text: str | None) -> SafeString:
    """Escaped ``text`` rendered as paragraphs, headings, lists, emphasis, links and Jira macros."""
    if not text or not str(text).strip():
        return mark_safe("")
    body = escape(str(text).replace("\r\n", "\n"))
    body = _COLOR.sub(r'<span style="color:\1">\2</span>', body)
    body = _SUP.sub(r"<sup>\1</sup>", body)
    body = _COLOR_LEFTOVER.sub("", body)

    html: list[str] = []
    para: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            html.append("<p>" + "<br>".join(_inline(line) for line in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            close_list()
            continue
        heading = _HEADING.match(line.strip())
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1)) if heading.group(1) else int(heading.group(2))
            tag = f"h{min(6, level + 3)}"  # keep headings below the page's own card titles
            html.append(f"<{tag}>{_inline(heading.group(3))}</{tag}>")
            continue
        bullet, numbered = _BULLET.match(line), _NUMBERED.match(line)
        if bullet or numbered:
            flush_para()
            tag = "ol" if numbered else "ul"
            if list_tag != tag:
                close_list()
                html.append(f"<{tag}>")
                list_tag = tag
            html.append(f"<li>{_inline((numbered or bullet).group(1))}</li>")
            continue
        close_list()
        para.append(line)
    flush_para()
    close_list()
    return mark_safe("".join(html))  # noqa: S308 - built from escaped input only
