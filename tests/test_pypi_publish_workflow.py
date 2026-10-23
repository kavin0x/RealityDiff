from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pypi-publish.yml"

LATEST_ACTIONS = {
    "actions/checkout": "v7.0.1",
    "astral-sh/setup-uv": "v10.3.2",
    "actions/upload-artifact": "v7.0.2",
    "actions/download-artifact": "v8.0.2",
    "pypa/gh-action-pypi-publish": "v1.14.3",
}


def test_pypi_publish_workflow_uses_pypi_environment_and_latest_actions():
    text = WORKFLOW.read_text()
    assert "name: pypi" in text
    assert "https://pypi.org/p/realitydiff" in text
    for action, version in LATEST_ACTIONS.items():
        assert f"uses: {action}@{version}" in text
