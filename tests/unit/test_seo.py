import json
import re
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

    public_post_dir = projects_dir / "journal" / "first-post"
    public_post_dir.mkdir(parents=True)
    (public_post_dir / "source.md").write_text("Hello")

    unlisted_post_dir = projects_dir / "journal" / "hidden-post"
    unlisted_post_dir.mkdir(parents=True)
    (unlisted_post_dir / "source.md").write_text("Hidden")

    meta = {
        "projects": {
            "journal": {
                "posts": {
                    "first-post": {
                        "type": "markdown",
                        "title": "First Post",
                        "date": "2026-06-01",
                        "owner": "alice",
                    },
                    "hidden-post": {
                        "type": "markdown",
                        "title": "Hidden Post",
                        "date": "2026-06-02",
                        "owner": "alice",
                        "visibility": "unlisted",
                    },
                }
            }
        }
    }
    (projects_dir.parent / "meta.json").write_text(json.dumps(meta))

    def patched_init(self):
        from markdown_it import MarkdownIt

        self.projects_dir = projects_dir
        self._content_dir = projects_dir.parent
        self._md = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": True, "breaks": True},
        )

    monkeypatch.setattr(DataInterface, "__init__", patched_init)


def _json_ld_blocks(body: str) -> list[dict]:
    return [
        json.loads(block)
        for block in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            body,
            re.DOTALL,
        )
    ]


def test_sitemap_lists_public_pages_and_loft_posts(client, loft_posts):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert response.mimetype == "application/xml"
    body = response.get_data(as_text=True)
    assert "<loc>https://nabicat.site/</loc>" in body
    assert "<loc>https://nabicat.site/loft/journal/first-post/</loc>" in body
    assert "<loc>https://nabicat.site/crosswords/</loc>" in body
    assert "<loc>https://nabicat.site/simulations/game-of-life</loc>" in body
    assert "/loft/journal/hidden-post/" not in body
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


def test_public_pages_render_canonical_and_social_metadata(client):
    response = client.get("/crosswords/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<title>Crosswords - NabiCat</title>" in body
    assert (
        '<meta name="description" '
        'content="Generate and play compact themed crossword puzzles on NabiCat.">'
    ) in body
    assert (
        '<link rel="canonical" href="https://nabicat.site/crosswords/">'
        in body
    )
    assert '<meta property="og:title" content="Crosswords - NabiCat">' in body
    assert (
        '<meta property="og:image" '
        'content="https://nabicat.site/static/nabicat-social.png">'
    ) in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert "X-Robots-Tag" not in response.headers


def test_home_describes_public_apps_without_exposing_private_dashboard(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Public writing, themed puzzles, and interactive browser experiments." in body
    assert "Public notes, essays, and galleries." in body
    assert "Generate compact themed puzzles." in body
    assert "Explore cellular automata and pathfinding." in body
    assert ">Metrics<" not in body
    assert ">Tubio<" not in body
    assert ">Proxy<" not in body

    schemas = _json_ld_blocks(body)
    assert any(
        schema.get("@type") == "WebSite"
        and schema.get("name") == "NabiCat"
        and schema.get("url") == "https://nabicat.site/"
        for schema in schemas
    )


def test_private_html_pages_are_noindex(client):
    response = client.get("/account/login")

    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert (
        '<meta name="robots" content="noindex, nofollow">'
        in response.get_data(as_text=True)
    )


def test_public_loft_posts_render_article_metadata(client, loft_posts):
    response = client.get("/loft/journal/first-post/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<meta property="og:type" content="article">' in body
    assert (
        '<meta property="article:published_time" content="2026-06-01">'
        in body
    )
    assert '<meta name="author" content="alice">' in body
    assert (
        '<link rel="canonical" '
        'href="https://nabicat.site/loft/journal/first-post/">'
    ) in body

    schemas = _json_ld_blocks(body)
    assert any(
        schema.get("@type") == "BlogPosting"
        and schema.get("headline") == "First Post"
        and schema.get("author", {}).get("name") == "alice"
        for schema in schemas
    )


def test_unlisted_loft_posts_are_noindex(client, loft_posts):
    response = client.get("/loft/journal/hidden-post/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<meta name="robots" content="noindex">' in body
    assert not any(
        schema.get("@type") == "BlogPosting"
        for schema in _json_ld_blocks(body)
    )
