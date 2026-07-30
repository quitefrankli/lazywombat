from web_app.config import ConfigManager
from web_app.helpers import limiter
from web_app.tubio import tubio_api
from web_app.tubio.services.cache import cache_audio_for_user


@tubio_api.route("/audio/<int:crc>/cache", methods=["POST"])
@limiter.limit(lambda: ConfigManager().tubio.surprise_media_rate_limit)
def cache_audio(crc: int):
    return cache_audio_for_user(crc)
