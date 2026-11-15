from __future__ import annotations

from app.gui.markdown import render_markdown


def test_plain_text_is_escaped() -> None:
    html = render_markdown("<script>alert('x')</script> & <b>no</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_fenced_code_is_escaped_and_highlighted() -> None:
    html = render_markdown("```python\nfrom klink import layout\nprint('<x>')\n```")
    assert "&lt;x&gt;" in html
    assert "python" in html
    assert "color:" in html
    assert "<table" in html


def test_inline_code_is_not_formatted_as_markdown() -> None:
    html = render_markdown("Use `**not bold**` here and **bold** there.")
    assert "**not bold**" in html
    assert "<b>bold</b>" in html


def test_links_only_allow_web_and_mail_schemes() -> None:
    html = render_markdown(
        "[docs](https://example.com/docs) and [bad](javascript:alert(1))"
    )
    assert 'href="https://example.com/docs"' in html
    assert "javascript:" in html  # shown as text, not a link
    assert 'href="javascript:' not in html


def test_lists_tables_and_blockquotes() -> None:
    markdown = """## Summary

- one
- two

1. first
2. second

| Rule | Count |
| --- | --- |
| density | 2 |

> Check the report.
"""
    html = render_markdown(markdown)
    assert "<h2>Summary</h2>" in html
    assert "<ul" in html and "<li>one</li>" in html
    assert "<ol" in html and "<li>first</li>" in html
    assert "<table" in html
    assert "density" in html
    assert "▍" in html


def test_unclosed_fence_does_not_drop_the_rest() -> None:
    html = render_markdown("```\nunclosed\n")
    assert "unclosed" in html
    assert "<table" in html
