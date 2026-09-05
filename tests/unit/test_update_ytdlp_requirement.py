import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from web_app.config import ConfigManager


def _project(requirements):
    dependencies = [line for line in requirements.splitlines() if not line.startswith("example-url=")]
    return (
        '# Keep this comment.\n[project]\nname = "example"\ndependencies = [\n'
        + "".join(f"    {json.dumps(line)},\n" for line in dependencies)
        + ']\n[tool.example]\nurl = "https://example.test/yt-dlp[default]>=2000.1.1"\n'
    )


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def _opener_for(payload, calls):
    def open_url(request, timeout):
        calls.append((request.full_url, timeout))
        return _Response(json.dumps(payload).encode())

    return open_url


def test_updates_only_the_ytdlp_floor_to_latest_stable_release(tmp_path):
    requirements_path = tmp_path / "pyproject.toml"
    requirements_path.write_text(_project(
        "other-package==1.0\n"
        "yt-dlp[default]>=2026.3.17\n"
        "example-url=https://example.test/yt-dlp[default]>=2000.1.1\n"
    ))
    calls = []
    payload = {
        "info": {"version": "2026.3.26"},
        "releases": {
            "2026.3.24": [{"yanked": False}],
            "2026.3.25": [{"yanked": False}],
            "2026.3.26": [{"yanked": True}],
            "2026.3.27rc1": [{"yanked": False}],
            "2026.3.27.dev1": [{"yanked": False}],
        },
    }

    from scripts.update_ytdlp_requirement import update_ytdlp_requirement

    result = update_ytdlp_requirement(
        requirements_path,
        opener=_opener_for(payload, calls),
    )

    assert result.current_version == "2026.3.17"
    assert result.latest_version == "2026.3.25"
    assert result.changed is True
    assert requirements_path.read_text() == _project(
        "other-package==1.0\n"
        "yt-dlp[default]>=2026.3.25\n"
        "example-url=https://example.test/yt-dlp[default]>=2000.1.1\n"
    )
    config = ConfigManager()
    assert calls == [(config.ytdlp_pypi_url, config.ytdlp_update_timeout_s)]


def test_does_not_rewrite_requirement_when_floor_is_current_or_newer(tmp_path):
    requirements_path = tmp_path / "pyproject.toml"
    original = "yt-dlp[default]>=2026.3.25\n"
    requirements_path.write_text(_project(original))
    payload = {
        "info": {"version": "2026.3.25"},
        "releases": {"2026.3.25": [{"yanked": False}]},
    }

    from scripts.update_ytdlp_requirement import update_ytdlp_requirement

    result = update_ytdlp_requirement(
        requirements_path,
        opener=_opener_for(payload, []),
    )

    assert result.changed is False
    assert requirements_path.read_text() == _project(original)


@pytest.mark.parametrize(
    "requirements",
    [
        "other-package==1.0\n",
        "yt-dlp[default]==2026.3.17\n",
        "yt-dlp[default]>=2026.3.17\nyt-dlp[default]>=2026.3.18\n",
        "yt-dlp[default]>=not-a-version\n",
    ],
)
def test_fails_before_network_access_for_missing_or_malformed_floor(
    tmp_path,
    requirements,
):
    requirements_path = tmp_path / "pyproject.toml"
    requirements_path.write_text(_project(requirements))

    def unexpected_network_call(*args, **kwargs):
        raise AssertionError("network must not be called")

    from scripts.update_ytdlp_requirement import RequirementError
    from scripts.update_ytdlp_requirement import update_ytdlp_requirement

    with pytest.raises(RequirementError):
        update_ytdlp_requirement(
            requirements_path,
            opener=unexpected_network_call,
        )

    assert requirements_path.read_text() == _project(requirements)


def test_fails_without_rewriting_when_pypi_has_no_stable_release(tmp_path):
    requirements_path = tmp_path / "pyproject.toml"
    original = "yt-dlp[default]>=2026.3.17\n"
    requirements_path.write_text(_project(original))
    payload = {
        "info": {"version": "2026.3.18rc1"},
        "releases": {
            "2026.3.18rc1": [{"yanked": False}],
            "not-a-version": [{"yanked": False}],
        },
    }

    from scripts.update_ytdlp_requirement import PyPIResponseError
    from scripts.update_ytdlp_requirement import update_ytdlp_requirement

    with pytest.raises(PyPIResponseError):
        update_ytdlp_requirement(
            requirements_path,
            opener=_opener_for(payload, []),
        )

    assert requirements_path.read_text() == _project(original)


def test_script_can_be_invoked_directly_from_the_repository():
    repository_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [sys.executable, "scripts/update_ytdlp_requirement.py", "--help"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Update the yt-dlp requirement floor" in completed.stdout


def test_workflow_validates_updated_requirements_before_push():
    repository_root = Path(__file__).resolve().parents[2]
    workflow = (
        repository_root / ".github/workflows/update-ytdlp.yml"
    ).read_text()

    assert 'cron: "30 16 * * *"' in workflow
    assert "contents: write" in workflow
    assert "FLASK_SECRET_KEY" not in workflow
    assert "uv lock --upgrade-package yt-dlp" in workflow
    assert "git add -- pyproject.toml uv.lock" in workflow
    update_index = workflow.index("python -m scripts.update_ytdlp_requirement")
    test_index = workflow.index('pytest -q -m "not ffmpeg"')
    push_index = workflow.index("git push origin HEAD:main")
    assert update_index < test_index < push_index
