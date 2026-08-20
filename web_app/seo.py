from flask import Flask, Response, request, url_for
from werkzeug.routing import BuildError

from web_app.config import ConfigManager


def _is_indexable_endpoint(app: Flask, endpoint: str) -> bool:
    """Return whether an endpoint represents a public, canonical HTML page."""
    if endpoint in {
        "home",
        "privacy",
        "loft.index",
        "loft.view_post",
        "crosswords.index",
        "simulations_api.index",
        "simulations_api.game_of_life",
        "simulations_api.astar",
    }:
        return True

    registry = app.extensions.get("nabicat_apps")
    if registry is None:
        return False
    return any(
        item.endpoint == endpoint and item.access.value == "public"
        for item in registry.navigation()
    )


def _canonical_path() -> str:
    endpoint = request.endpoint
    if endpoint:
        try:
            return url_for(endpoint, **(request.view_args or {}))
        except BuildError:
            pass
    return request.path


def register_seo(app: Flask) -> None:
    """Register shared canonical, social-preview, and indexing metadata."""

    @app.context_processor
    def inject_seo_metadata() -> dict[str, object]:
        site_url = ConfigManager().site_url.rstrip("/")
        return {
            "seo_site_url": site_url,
            "seo_canonical_url": f"{site_url}{_canonical_path()}",
            "seo_social_image_url": (
                f"{site_url}/static/nabicat-social.png"
            ),
            "seo_social_image_alt": (
                "NabiCat web apps, public writing, and interactive experiments"
            ),
            "seo_indexable": _is_indexable_endpoint(
                app,
                request.endpoint or "",
            ),
        }

    @app.after_request
    def apply_private_page_indexing_policy(response: Response) -> Response:
        if (
            response.mimetype == "text/html"
            and not _is_indexable_endpoint(app, request.endpoint or "")
        ):
            response.headers.setdefault(
                "X-Robots-Tag",
                "noindex, nofollow",
            )
        return response
