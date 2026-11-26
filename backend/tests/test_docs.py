"""Keep the documentation honest: generated references, links, names and examples."""

import json
import re
import sys
from pathlib import Path

import pytest

from atb.cli import app
from atb.config import Settings
from atb.tools import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md"), ROOT / "backend" / "README.md"])

pytestmark = pytest.mark.skipif(not (ROOT / "docs").is_dir(), reason="the documentation is not part of this install")


def anchors(path: Path) -> set[str]:
    """Heading anchors the way GitHub derives them."""
    found = set()
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.M):
        slug = re.sub(r"[^\w‌\- ]", "", title.lower()).replace(" ", "-")
        found.add(slug)
    return found


def test_generated_references_are_current():
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    try:
        import gen_docs
    finally:
        sys.path.pop(0)
    for path, text in gen_docs.generate().items():
        assert path.read_text(encoding="utf-8") == text, f"run backend/scripts/gen_docs.py ({path.name} is stale)"


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(document):
    for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target):
            continue
        file, _, anchor = target.partition("#")
        path = (document.parent / file).resolve() if file else document
        assert path.exists(), f"{document.name}: missing {target}"
        if anchor:
            assert anchor in anchors(path), f"{document.name}: no heading for {target}"


def test_paths_referenced_by_the_code_exist():
    for source, reference in (("backend/atb/policy.py", "docs/SECURITY.md"), ("frontend/src/pages/Agents.tsx", "docs/CLIENTS.md")):
        assert reference in (ROOT / source).read_text(encoding="utf-8")
        assert (ROOT / reference).is_file()


def test_environment_variables_exist():
    known = {f"ATB_{name.upper()}" for name in Settings.model_fields}
    sources = ["docker-compose.yml", "Dockerfile", "backend/atb/cli.py", "examples/openai_agent.py",
               "backend/tests/conftest.py", "backend/tests/test_docker_logs.py"]
    for source in sources:
        known |= set(re.findall(r"\bATB_[A-Z_]*[A-Z]\b", (ROOT / source).read_text(encoding="utf-8")))
    for document in [*DOCUMENTS, ROOT / ".env.example"]:
        unknown = set(re.findall(r"\bATB_[A-Z_]*[A-Z]\b", document.read_text(encoding="utf-8"))) - known
        assert not unknown, f"{document.name}: {unknown}"


def test_command_line_examples_exist():
    commands = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    pattern = r"(?:^\s*|`|bridge )atb ([a-z][a-z\-]+)"
    for document in DOCUMENTS:
        used = set(re.findall(pattern, document.read_text(encoding="utf-8"), re.M))
        assert used <= commands, f"{document.name}: unknown commands {used - commands}"


def test_tool_call_examples_match_the_schemas():
    examples = re.findall(r"^atb call (\w+) '(\{.*\})'", (ROOT / "docs" / "SCENARIOS.md").read_text(encoding="utf-8"), re.M)
    assert len(examples) >= 10
    for name, arguments in examples:
        REGISTRY[name].model.model_validate(json.loads(arguments))


def test_scenarios_name_real_tests():
    tests = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend" / "tests").glob("test_*.py"))
    named = re.findall(r"`(test_\w+)`", (ROOT / "docs" / "SCENARIOS.md").read_text(encoding="utf-8"))
    assert named
    for name in named:
        assert f"def {name}(" in tests, name
