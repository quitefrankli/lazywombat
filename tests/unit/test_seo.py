import json
from pathlib import Path

import pytest

import web_app.__main__  # noqa: F401 - registers app routes
from web_app.app import app
from web_app.loft.data_interface import DataInterface


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def loft_posts(tmp_path, monkeypatch):
    projects_dir = tmp_path / "loft" / "projects"
    post_dir = projects_dir / "journal" / "first-post"
    post_dir.mkdir(parents=True)
    (post_dir / "source.md").write_text("Hello")
    meta = {
        "projects": {
            "journal": {
                "posts": {
                    "first-post": {
                        "type": "markdown",
                        "title": "First Post",
                        "date": "2026-06-01",
                        "owner": "alice",
                    }
                }
            }
        }
    }
    (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

    def patched_init(self):
        from markdown_it import MarkdownIt

        self.projects_dir = projects_dir
        self._content_dir = projects_dir.parent
        self._md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

    monkeypatch.setattr(DataInterface, "__init__", patched_init)


def test_sitemap_lists_public_pages_and_loft_posts(client, loft_posts):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert response.mimetype == "application/xml"
    body = response.get_data(as_text=True)
    assert "<loc>https://nabicat.site/</loc>" in body
    assert "<loc>https://nabicat.site/loft/journal/first-post/</loc>" in body
    assert "<loc>https://nabicat.site/crosswords/</loc>" in body
    assert "<loc>https://nabicat.site/simulations/game-of-life</loc>" in body
    assert "/metrics/" not in body


def test_robots_txt_allows_crawlers_and_advertises_sitemap(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == (
        "User-agent: *\n"
        "Allow: /\n"
        "Sitemap: https://nabicat.site/sitemap.xml\n"
    )


def test_public_pages_render_seo_metadata(client):
    response = client.get("/crosswords/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<title>Crosswords - NabiCat</title>" in body
    assert '<meta name="description" content="Generate and play compact themed crossword puzzles on NabiCat.">' in body
    assert '<link rel="canonical" href="http://localhost/crosswords/">' in body
    assert '<meta property="og:title" content="Crosswords - NabiCat">' in body
