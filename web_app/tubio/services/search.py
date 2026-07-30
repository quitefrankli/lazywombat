import web_app.tubio as tubio_facade

from web_app.config import ConfigManager
from web_app.users import User


def search_youtube(query: str, user: User, page: int) -> dict:
    decorated_query = f"{ConfigManager().tubio.search_prefix}{query}"
    favourites = tubio_facade.get_cached_yt_vid_ids(user)
    return tubio_facade.AudioDownloader.search_youtube(
        decorated_query,
        favourites,
        page=page,
    )


def suggest_queries(query: str) -> list[str]:
    return tubio_facade.AudioDownloader.suggest_queries(query)
