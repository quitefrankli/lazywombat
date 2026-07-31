"""Integration tests for loft content layout and rendering."""

import shutil
import uuid
from pathlib import Path

import pytest
import requests


DEBUG_SAVE_DATA_PATH = Path.home() / ".nabicat_debug" / "data"
LOFT_PROJECTS_DIR = DEBUG_SAVE_DATA_PATH / "loft" / "projects"


def _write_post(project: str, post_slug: str, content: str) -> str:
    post_dir = LOFT_PROJECTS_DIR / project / post_slug
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "index.html").write_text(content, encoding="utf-8")
    return f"{project}/{post_slug}"


@pytest.mark.integration
class TestLoftIntegration:
    def test_loft_index_lists_projects(self, server_url):
        project = f"loft_proj_{uuid.uuid4().hex[:8]}"
        post_slug = f"loft_post_{uuid.uuid4().hex[:8]}"
        project_dir = LOFT_PROJECTS_DIR / project

        try:
            _write_post(project, post_slug, "<h1>Test</h1>")

            response = requests.get(f"{server_url}/loft/")
            assert response.status_code == 200
            assert project in response.text
        finally:
            shutil.rmtree(project_dir, ignore_errors=True)

    def test_loft_post_route_renders_content(self, server_url):
        project = f"loft_proj_{uuid.uuid4().hex[:8]}"
        post_slug = f"loft_post_{uuid.uuid4().hex[:8]}"
        content = "<h1>Test Post</h1><p>Body content</p>"
        project_dir = LOFT_PROJECTS_DIR / project

        try:
            _write_post(project, post_slug, content)

            response = requests.get(f"{server_url}/loft/{project}/{post_slug}/")
            assert response.status_code == 200
            assert "Test Post" in response.text
            assert "Body content" in response.text
        finally:
            shutil.rmtree(project_dir, ignore_errors=True)

    def test_loft_asset_served_from_post_directory(self, server_url):
        project = f"loft_proj_{uuid.uuid4().hex[:8]}"
        post_slug = f"loft_post_{uuid.uuid4().hex[:8]}"
        asset_name = "photo.jpg"
        project_dir = LOFT_PROJECTS_DIR / project
        post_dir = project_dir / post_slug

        try:
            post_dir.mkdir(parents=True, exist_ok=True)
            asset_bytes = b"fake-jpeg-bytes"
            (post_dir / asset_name).write_bytes(asset_bytes)
            (post_dir / "index.html").write_text(f'<img src="{asset_name}">')

            response = requests.get(f"{server_url}/loft/{project}/{post_slug}/{asset_name}")
            assert response.status_code == 200
            assert response.content == asset_bytes
        finally:
            shutil.rmtree(project_dir, ignore_errors=True)
