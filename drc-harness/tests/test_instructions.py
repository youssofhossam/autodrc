from __future__ import annotations

from pathlib import Path

from app.instructions import (
    DEFAULT_INSTRUCTIONS_PATH,
    compose_system_instruction,
    load_project_instructions,
    system_instruction_provider,
)


def test_default_path_points_at_the_repository_agents_file() -> None:
    assert DEFAULT_INSTRUCTIONS_PATH.name == "AGENTS.md"
    assert DEFAULT_INSTRUCTIONS_PATH.is_file()


def test_load_reads_the_current_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("  # Rules\n\nUse the MCP.  \n", encoding="utf-8")

    assert load_project_instructions(path) == "# Rules\n\nUse the MCP."


def test_load_returns_empty_text_for_a_missing_file(tmp_path: Path) -> None:
    assert load_project_instructions(tmp_path / "missing.md") == ""
    assert load_project_instructions(None) == ""


def test_compose_keeps_the_base_instruction_first() -> None:
    composed = compose_system_instruction(
        "Base instruction.",
        "Project rule.",
        path=Path("/repo/AGENTS.md"),
    )

    assert composed.startswith("Base instruction.")
    assert "/repo/AGENTS.md" in composed
    assert composed.endswith("Project rule.")


def test_compose_without_project_instructions_is_just_the_base() -> None:
    assert compose_system_instruction("Base instruction.", "   ") == (
        "Base instruction."
    )


def test_provider_rereads_the_file_on_every_call(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("First rule.", encoding="utf-8")
    current = system_instruction_provider("Base.", path)

    first = current()

    path.write_text("Second rule.", encoding="utf-8")
    second = current()

    assert "First rule." in first
    assert "First rule." not in second
    assert "Second rule." in second


def test_provider_tolerates_a_file_that_disappears(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("Rule.", encoding="utf-8")
    current = system_instruction_provider("Base.", path)
    path.unlink()

    assert current() == "Base."
