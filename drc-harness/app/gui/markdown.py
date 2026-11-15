"""A small, safe Markdown renderer for QTextDocument.

Qt can load a subset of HTML, but its built-in Markdown conversion offers very
little control over code blocks and spacing. This renderer covers the Markdown
used by chat assistants (headings, lists, links, code, tables, quotes) and
escapes all input before emitting HTML.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

from app.gui.theme import COLORS, mono_family, sans_family

_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([^\s`]*)?.*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(
    r"\[([^\]\n]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)"
)
_BARE_URL = re.compile(r"https?://[^\s<]+")
_TOKEN = re.compile(r"\x00T(\d+)\x00")

_COLORS = {
    "text": COLORS["text"],
    "soft": COLORS["text_soft"],
    "muted": COLORS["text_muted"],
    "faint": COLORS["text_faint"],
    "accent": COLORS["accent"],
    "code_text": COLORS["code_text"],
    "keyword": "#9bb4ff",
    "string": "#a8d69b",
    "comment": "#707a8c",
    "number": "#e0bb7a",
    "border": COLORS["border"],
    "code_bg": COLORS["code_bg"],
}


def document_css() -> str:
    return f"""
    body {{
        color: {_COLORS["text"]};
        font-family: '{sans_family()}';
        font-size: 10.5pt;
        margin: 0px;
    }}
    p {{
        margin: 0px 0px 7px 0px;
    }}
    h1, h2, h3, h4, h5, h6 {{
        color: {_COLORS["text"]};
        font-weight: 600;
        margin: 13px 0px 5px 0px;
    }}
    h1 {{ font-size: 16pt; }}
    h2 {{ font-size: 14pt; }}
    h3 {{ font-size: 12pt; }}
    h4, h5, h6 {{ font-size: 10.5pt; }}
    ul, ol {{
        margin: 2px 0px 8px 0px;
        -qt-list-indent: 1;
    }}
    li {{
        margin-bottom: 3px;
    }}
    a {{
        color: {_COLORS["accent"]};
        text-decoration: none;
    }}
    code {{
        font-family: '{mono_family()}';
        font-size: 9pt;
        color: {_COLORS["code_text"]};
        background-color: #1b202a;
    }}
    pre {{
        font-family: '{mono_family()}';
        font-size: 9pt;
        color: {_COLORS["code_text"]};
        margin: 0px;
        white-space: pre-wrap;
    }}
    blockquote {{
        color: {_COLORS["muted"]};
        margin: 4px 0px 8px 10px;
        padding-left: 10px;
    }}
    hr {{
        color: {_COLORS["border"]};
        background-color: {_COLORS["border"]};
        height: 1px;
        border: none;
    }}
    table {{
        border-collapse: collapse;
        margin: 6px 0px 10px 0px;
    }}
    th {{
        background-color: #1b202a;
        color: {_COLORS["soft"]};
        font-weight: 600;
        font-size: 9pt;
        padding: 5px 7px;
        border: 1px solid {_COLORS["border"]};
        text-align: left;
    }}
    td {{
        color: {_COLORS["text"]};
        font-size: 9.5pt;
        padding: 5px 7px;
        border: 1px solid {_COLORS["border"]};
    }}
    """


def render_markdown(text: str) -> str:
    body = _render_blocks(text.replace("\r\n", "\n").replace("\r", "\n"))
    return (
        "<html><head><style>"
        + document_css()
        + "</style></head><body>"
        + body
        + "</body></html>"
    )


def _render_blocks(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        if not paragraph:
            return
        joined = " ".join(line.strip() for line in paragraph if line.strip())
        if joined:
            out.append(f"<p>{_render_inline(joined)}</p>")
        paragraph.clear()

    while i < len(lines):
        line = lines[i]

        fence = _FENCE.match(line)
        if fence:
            flush_paragraph()
            marker = fence.group(1)[0] * 3
            language = (fence.group(2) or "").strip().lower()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].lstrip().startswith(marker):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            out.append(_render_code_block("\n".join(code), language))
            continue

        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            level = min(6, len(heading.group(1)))
            out.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if _HR.match(line):
            flush_paragraph()
            out.append("<hr>")
            i += 1
            continue

        if _QUOTE.match(line):
            flush_paragraph()
            quoted: list[str] = []
            while i < len(lines) and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.match(lines[i]).group(1))  # type: ignore[union-attr]
                i += 1
            content = " ".join(part.strip() for part in quoted if part.strip())
            out.append(
                '<div style="margin: 4px 0px 8px 8px;">'
                f'<span style="color: {_COLORS["border"]};">▍ </span>'
                f'<span style="color: {_COLORS["muted"]};">'
                f"{_render_inline(content)}</span></div>"
            )
            continue

        if _BULLET.match(line):
            flush_paragraph()
            items: list[tuple[int, str]] = []
            while i < len(lines):
                match = _BULLET.match(lines[i])
                if match is None:
                    break
                items.append((len(match.group(1).expandtabs(4)), match.group(2)))
                i += 1
            out.append(_render_list(items, ordered=False))
            continue

        if _NUMBERED.match(line):
            flush_paragraph()
            items = []
            while i < len(lines):
                match = _NUMBERED.match(lines[i])
                if match is None:
                    break
                items.append((len(match.group(1).expandtabs(4)), match.group(2)))
                i += 1
            out.append(_render_list(items, ordered=True))
            continue

        if (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR.match(lines[i + 1])
        ):
            flush_paragraph()
            header = _split_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            out.append(_render_table(header, rows))
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph()
    return "\n".join(out)


def _render_list(items: Iterable[tuple[int, str]], *, ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    rows = list(items)
    base = min((indent for indent, _ in rows), default=0)
    parts = [f'<{tag} style="margin-left: {base * 4}px;">']
    for indent, content in rows:
        extra = max(0, indent - base) * 8
        style = f' style="margin-left: {extra}px;"' if extra else ""
        parts.append(f"<li{style}>{_render_inline(content)}</li>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    parts = ['<table width="100%" cellspacing="0" cellpadding="0">']
    parts.append("<tr>")
    for cell in header:
        parts.append(f"<th>{_render_inline(cell)}</th>")
    parts.append("</tr>")
    for row in rows:
        parts.append("<tr>")
        for cell in row[: len(header)]:
            parts.append(f"<td>{_render_inline(cell)}</td>")
        for _ in range(len(header) - len(row)):
            parts.append("<td></td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _render_code_block(code: str, language: str) -> str:
    label = language or "text"
    highlighted = _highlight(code, language)
    mono = mono_family()
    return (
        '<table width="100%" cellspacing="0" cellpadding="9" '
        f'bgcolor="{_COLORS["code_bg"]}" style="margin: 6px 0px 10px 0px;">'
        "<tr><td>"
        f'<div style="color: {_COLORS["faint"]}; font-family: \'{mono}\'; '
        f'font-size: 8pt; margin-bottom: 5px;">{html.escape(label)}</div>'
        f'<pre style="color: {_COLORS["code_text"]}; font-family: \'{mono}\'; '
        f'font-size: 9pt; margin: 0px;">{highlighted}</pre>'
        "</td></tr></table>"
    )


def _render_inline(text: str) -> str:
    tokens: list[str] = []

    def store(fragment: str) -> str:
        tokens.append(fragment)
        return f"\x00T{len(tokens) - 1}\x00"

    text = _INLINE_CODE.sub(
        lambda match: store(
            '<code style="font-family: \''
            + mono_family()
            + '\'; font-size: 9pt; color: '
            + _COLORS["code_text"]
            + '; background-color: #1b202a;">'
            + html.escape(match.group(1))
            + "</code>"
        ),
        text,
    )
    text = _MD_LINK.sub(
        lambda match: store(_link(match.group(1), match.group(2))), text
    )
    text = _BARE_URL.sub(lambda match: store(_autolink(match.group(0))), text)

    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)
    escaped = re.sub(
        r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", escaped
    )
    escaped = re.sub(
        r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", escaped
    )
    escaped = re.sub(r"~~(.+?)~~", r"<s>\1</s>", escaped)

    # Tokens contain no HTML-special characters, so a single pass is enough.
    def restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return tokens[index] if 0 <= index < len(tokens) else match.group(0)

    return _TOKEN.sub(restore, escaped)


def _link(label: str, url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return (
        f'<a href="{safe_url}" style="color: {_COLORS["accent"]}; '
        'text-decoration: none;">'
        f"{html.escape(label)}</a>"
    )


def _autolink(url: str) -> str:
    # Keep trailing sentence punctuation outside the link.
    trailing = ""
    while url and url[-1] in ".,;:!?)]}":
        trailing = url[-1] + trailing
        url = url[:-1]
    if not url:
        return html.escape(trailing)
    return _link(url, url) + html.escape(trailing)


# --------------------------------------------------------------------------
# Lightweight syntax highlighting for common languages.

_KEYWORDS: dict[str, set[str]] = {
    "python": {
        "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "False", "finally", "for",
        "from", "global", "if", "import", "in", "is", "lambda", "None", "nonlocal",
        "not", "or", "pass", "raise", "return", "True", "try", "while", "with",
        "yield", "match", "case",
    },
    "javascript": {
        "async", "await", "break", "case", "catch", "class", "const", "continue",
        "default", "delete", "do", "else", "export", "extends", "false", "finally",
        "for", "from", "function", "if", "import", "in", "instanceof", "let", "new",
        "null", "of", "return", "static", "super", "switch", "this", "throw", "true",
        "try", "typeof", "undefined", "var", "void", "while", "yield",
    },
    "rust": {
        "as", "async", "await", "break", "const", "continue", "crate", "else", "enum",
        "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match",
        "mod", "move", "mut", "pub", "ref", "return", "self", "Self", "static",
        "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while",
    },
    "go": {
        "break", "case", "chan", "const", "continue", "default", "defer", "else",
        "fallthrough", "for", "func", "go", "goto", "if", "import", "interface",
        "map", "package", "range", "return", "select", "struct", "switch", "type",
        "var",
    },
    "sql": {
        "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "DELETE",
        "CREATE", "TABLE", "DROP", "ALTER", "JOIN", "LEFT", "RIGHT", "INNER",
        "OUTER", "ON", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "AND", "OR",
        "NOT", "NULL", "AS", "DISTINCT", "UNION", "ALL", "CASE", "WHEN", "THEN",
        "ELSE", "END",
    },
}

_LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "javascript",
    "tsx": "javascript",
    "typescript": "javascript",
    "node": "javascript",
    "rs": "rust",
    "golang": "go",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "console": "shell",
    "psql": "sql",
    "jsonc": "json",
    "yml": "yaml",
}

_HIGHLIGHT_RE = re.compile(
    r'(?P<triple3>"""(?:\\.|[^\\])*?""")'
    r"|(?P<triple1>'''(?:\\.|[^\\])*?''')"
    r'|(?P<double>"(?:\\.|[^"\\\n])*")'
    r"|(?P<single>'(?:\\.|[^'\\\n])*')"
    r"|(?P<line_comment>//[^\n]*)"
    r"|(?P<hash_comment>#[^\n]*)"
    r"|(?P<number>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)"
)

_TOKEN_STYLES = {
    "triple3": ("string", False),
    "triple1": ("string", False),
    "double": ("string", False),
    "single": ("string", False),
    "line_comment": ("comment", True),
    "hash_comment": ("comment", True),
    "number": ("number", False),
}


def _highlight(code: str, language: str) -> str:
    language = _LANGUAGE_ALIASES.get(language, language)
    keywords = _KEYWORDS.get(language, set())
    keyword_re = None
    if keywords:
        joined = "|".join(re.escape(word) for word in sorted(keywords, key=len, reverse=True))
        flags = re.IGNORECASE if language == "sql" else 0
        keyword_re = re.compile(rf"\b(?:{joined})\b", flags)

    if language not in _KEYWORDS and language not in {"json", "yaml", "toml", "shell"}:
        return html.escape(code)

    pieces: list[str] = []
    last = 0
    for match in _HIGHLIGHT_RE.finditer(code):
        # YAML/TOML/JSON have no // comments. Avoid colouring URLs in JSON.
        if match.lastgroup == "line_comment" and language not in {
            "javascript", "rust", "go", "json",
        }:
            continue
        if match.lastgroup == "hash_comment" and language not in {
            "python", "shell", "yaml", "toml",
        }:
            continue
        if match.lastgroup == "hash_comment" and language == "json":
            continue

        pieces.append(_highlight_keywords(code[last : match.start()], keyword_re))
        token = match.group(0)
        role, italic = _TOKEN_STYLES[match.lastgroup or "double"]
        style = f"color: {_COLORS[role]};"
        if italic:
            style += " font-style: italic;"
        pieces.append(f'<span style="{style}">{html.escape(token)}</span>')
        last = match.end()

    pieces.append(_highlight_keywords(code[last:], keyword_re))
    return "".join(pieces)


def _highlight_keywords(text: str, keyword_re: re.Pattern[str] | None) -> str:
    if keyword_re is None:
        return html.escape(text)
    pieces: list[str] = []
    last = 0
    for match in keyword_re.finditer(text):
        pieces.append(html.escape(text[last : match.start()]))
        pieces.append(
            f'<span style="color: {_COLORS["keyword"]};">'
            f"{html.escape(match.group(0))}</span>"
        )
        last = match.end()
    pieces.append(html.escape(text[last:]))
    return "".join(pieces)
