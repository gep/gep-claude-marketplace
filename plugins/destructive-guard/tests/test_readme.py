"""README stays in sync with the code (port of readme.test.mjs)."""

import re

import pytest

from dg_testkit import DG

README = (DG / "README.md").read_text()
# Variables the hook wrapper and the tests use internally; not user settings.
INTERNAL = {"GATE_INFRA_PYTHON"}
ENV_NAME = re.compile(r"\b(?:GATE|TYPESAFE)_[A-Z_]*[A-Z]\b")


def env_names() -> list[str]:
    files = [p for p in (DG / "lib").rglob("*") if p.is_file() and p.suffix == ".py"]
    files += [p for p in (DG / "hooks").iterdir() if p.is_file()]
    names = {m for p in files for m in ENV_NAME.findall(p.read_text())}
    return sorted(names - INTERNAL)


def test_readme_documents_every_env_variable():
    names = env_names()
    assert len(names) >= 10, "found the variables"
    missing = [n for n in names if f"`{n}`" not in README]
    assert missing == []


@pytest.mark.parametrize("path", sorted((DG / "jev").glob("*.json")), ids=lambda p: p.name)
def test_readme_lists_every_jev_prompt(path):
    assert f"`{path.stem}`" in README


def test_readme_documents_the_removed_variables_and_assumptions():
    for n in ("GATE_JEV", "GATE_CHECKS", "GATE_RULES_FILE", "GATE_STRICT", "GATE_JEV_SCOPE"):
        assert f"`{n}`" in README  # the migration table (the removed names are also in lib/)
    for n in range(1, 8):
        assert f"| A{n} |" in README
    assert "2.1.330" in README


@pytest.mark.parametrize("text", ["jev-unavailable:", "plugin-fault:", "GATE_ENABLED=0"])
def test_readme_explains_the_ask_reasons(text):
    assert text in README
