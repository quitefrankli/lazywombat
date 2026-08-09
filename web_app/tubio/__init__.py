from flask import Blueprint as _Blueprint

from web_app.config import ConfigManager as _ConfigManager
from web_app.helpers import require_login_blueprint as _require_login_blueprint


tubio_api = _Blueprint(
    'tubio',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/tubio',
)
_require_login_blueprint(tubio_api)


@tubio_api.context_processor
def _inject_tubio_config():
    config = _ConfigManager().tubio
    return {
        'app_name': 'Tubio',
        'tubio_player': {
            'volume_min_percent': config.trackbar_volume_min_percent,
            'volume_max_percent': config.trackbar_volume_max_percent,
            'volume_step_percent': config.trackbar_volume_step_percent,
            'default_volume_percent': config.trackbar_default_volume_percent,
            'volume_storage_key': config.trackbar_volume_storage_key,
            'muted_storage_key': config.trackbar_muted_storage_key,
        },
        'tubio_autocomplete': {
            'debounce_ms': config.autocomplete_debounce_ms,
            'min_query_len': config.autocomplete_min_query_len,
        },
        'tubio_sidebar': {
            'collapsed_storage_key': config.sidebar_collapsed_storage_key,
            'selected_storage_key': config.sidebar_selected_storage_key,
        },
        'tubio_surprise': {
            'buffer_size': config.surprise_buffer_size,
            'cache_poll_interval_ms': config.surprise_cache_poll_interval_ms,
        },
    }


# Import feature modules only to register their routes on the shared blueprint.
from web_app.tubio.routes import playlists as _playlists  # noqa: E402,F401
from web_app.tubio.routes import surprise as _surprise  # noqa: E402,F401
from web_app.tubio.routes import media as _media  # noqa: E402,F401
from web_app.tubio.routes import downloads as _downloads  # noqa: E402,F401
from web_app.tubio.routes import search as _search  # noqa: E402,F401


__all__ = ['tubio_api']
