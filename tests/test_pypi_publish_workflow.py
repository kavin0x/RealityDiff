import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLISH = WORKFLOWS / "pypi-publish.yml"

PINNED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv": "07a047e608db84b3d8c06cd9a7c7dcc9c355c18e",
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "pypa/gh-action-pypi-publish": "7245906fb61fa33a13554f9d5fb856fe61758086",
}


def test_pypi_publish_workflow_uses_pypi_environment():
    text = PUBLISH.read_text()
    assert "name: pypi" in text
    assert "https://pypi.org/p/realitydiff" in text


def test_pypi_publish_runs_tests_and_checks_before_publish():
    text = PUBLISH.read_text()
    assert "needs: test" in text
    assert "needs: build" in text
    assert "pytest" in text
    assert "does not match pyproject version" in text
    assert "Check package contents" in text


def test_all_workflow_actions_are_pinned_to_commit_shas():
    for workflow in WORKFLOWS.glob("*.yml"):
        for action, ref in re.findall(r"uses:\s*([\w./-]+)@(\S+)", workflow.read_text()):
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{workflow.name}: {action}@{ref} is not a commit SHA"
            assert PINNED_ACTIONS.get(action) == ref, f"{workflow.name}: unexpected pin for {action}"
