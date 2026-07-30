import logging
import json
import math
import time
import random
import secrets

from typing import *
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, send_file, redirect, url_for, flash, Response
from flask_login import login_required

from web_app.tubio.data_interface import DataInterface, AudioMetadata, Playlist
from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError, get_download_progress, clear_download_progress
from web_app.config import ConfigManager
from web_app.helpers import cur_user, parse_request, require_login_blueprint
from web_app.users import User
from web_app.helpers import limiter
from web_app.redis_client import get_redis
from web_app.tubio.surprise import reserve_audio_metadata
from web_app.logging_utils import log_event


tubio_api = Blueprint(
    'tubio',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/tubio'
)


require_login_blueprint(tubio_api)


@tubio_api.context_processor
def inject_app_name():
    cfg = ConfigManager().tubio
    return dict(
        app_name='Tubio',
        tubio_player=dict(
            volume_min_percent=cfg.trackbar_volume_min_percent,
            volume_max_percent=cfg.trackbar_volume_max_percent,
            volume_step_percent=cfg.trackbar_volume_step_percent,
            default_volume_percent=cfg.trackbar_default_volume_percent,
            volume_storage_key=cfg.trackbar_volume_storage_key,
            muted_storage_key=cfg.trackbar_muted_storage_key,
        ),
        tubio_autocomplete=dict(
            debounce_ms=cfg.autocomplete_debounce_ms,
            min_query_len=cfg.autocomplete_min_query_len,
        ),
        tubio_surprise=dict(
            buffer_size=cfg.surprise_buffer_size,
            cache_poll_interval_ms=cfg.surprise_cache_poll_interval_ms,
        ),
    )


# Importing the feature modules registers their routes on the shared blueprint.
# These facade exports preserve the established helper import paths while the
# implementations live beside their owning feature.
from web_app.tubio.services.playlists import (  # noqa: E402,F401
    _add_track_occurrences,
    _playlist_track_data,
    get_cached_yt_vid_ids,
    get_playlists_data,
)
from web_app.tubio.routes import cache as _cache_routes  # noqa: E402,F401
from web_app.tubio.routes import downloads as _download_routes  # noqa: E402,F401
from web_app.tubio.routes import media as _media_routes  # noqa: E402,F401
from web_app.tubio.routes import playlists as _playlist_routes  # noqa: E402,F401
from web_app.tubio.routes import search as _search_routes  # noqa: E402,F401
from web_app.tubio.routes import surprise as _surprise_routes  # noqa: E402,F401
from web_app.tubio.services import surprise as _surprise_service  # noqa: E402,F401

_range_response = _media_routes._range_response
_active_surprise = _surprise_service._active_surprise
_grow_surprise = _surprise_service._grow_surprise
_pick_surprise_candidates = _surprise_service._pick_surprise_candidates
_resolve_surprise_seed = _surprise_service._resolve_surprise_seed
_surprise_is_expired = _surprise_service._surprise_is_expired
_surprise_payload = _surprise_service._surprise_payload
