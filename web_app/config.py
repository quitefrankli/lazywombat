from dataclasses import dataclass, field
from os import getenv
from pathlib import Path
from datetime import timedelta
from typing import Callable, Literal
from dotenv import load_dotenv

LLMSource = Literal["meridian", "codex", "bedrock"]

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


@dataclass
class LLMConfig:
    api_source: LLMSource = "codex"

    # Meridian (local Claude proxy) transport
    meridian_default_port: int = 3456
    meridian_models: dict = field(default_factory=lambda: {
        "weak":   "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-6",
        "strong": "claude-opus-4-7",
    })

    # Codex CLI transport (shared by all apps that shell out to codex).
    # Empty model strings mean "let codex CLI pick its native default".
    codex_cli_command: str = "codex"
    codex_cli_approval_policy: str = "never"
    codex_cli_sandbox: str = "read-only"
    codex_models: dict = field(default_factory=lambda: {
        "weak":   "",
        "medium": "",
        "strong": "",
    })

    # Bedrock (Anthropic-on-AWS via boto3 / anthropic[bedrock] SDK).
    # AWS auth comes from the standard AWS credential chain; AWS_REGION must be set.
    # Values may be inference-profile ARNs or anthropic.<model> IDs.
    # Bedrock model IDs / inference-profile ARNs. Inference-profile ARNs are
    # required when your IAM policy only grants InvokeModel on the profile,
    # not on the bare foundation-model ID — set BEDROCK_{TIER}_MODEL in .env
    # to override per-environment without committing the ARN.
    bedrock_models: dict = field(default_factory=lambda: {
        "weak":   getenv("BEDROCK_WEAK_MODEL")   or "anthropic.claude-haiku-4-5",
        "medium": getenv("BEDROCK_MEDIUM_MODEL") or "anthropic.claude-sonnet-4-6",
        "strong": getenv("BEDROCK_STRONG_MODEL") or "anthropic.claude-opus-4-7",
    })
    # Socket-level timeouts/retries for the boto3 bedrock-runtime client. Without
    # these, a stalled connection hangs the calling thread forever (boto3's
    # defaults are a 60s connect timeout but an unbounded read, plus retries).
    # read_timeout is supplied per-call from the role's timeout_s; these are the
    # connect ceiling and retry cap that apply to every call.
    bedrock_connect_timeout_s: float = 10.0
    bedrock_max_attempts: int = 2

    @property
    def meridian_url(self) -> str:
        return f"http://127.0.0.1:{self.meridian_default_port}/v1/messages"

    def model_for(self, tier: str) -> str:
        """Resolve a tier (``weak``/``medium``/``strong``) to a concrete model
        name for the currently-configured ``api_source``. Unknown tiers fall
        back to ``medium``.
        """
        models = {
            "meridian": self.meridian_models,
            "codex":    self.codex_models,
            "bedrock":  self.bedrock_models,
        }.get(self.api_source, self.codex_models)
        return models.get(tier, models.get("medium", ""))


@dataclass
class TubioConfig:
    _save_data_path: Callable[[], Path] = field(repr=False)
    search_prefix: str = ""
    max_results: int = 10
    max_search_pages: int = 3
    max_video_length: timedelta = timedelta(minutes=10)
    direct_video_max_length: timedelta = timedelta(hours=1)
    # Ordered fallback tiers for the YouTube results `sp` duration filter:
    # no filter -> short (<4 min) -> medium (4-20 min). Each fetch is appended
    # until the requested page is filled, so long-stream-heavy queries still
    # surface short videos buried below them.
    search_length_filter_sps: tuple = (None, "EgIYAQ==", "EgIYAw==")
    test_video_id: str = "dQw4w9WgXcQ"
    upload_allowed_extensions: tuple = ("mp3", "mp4", "m4a")
    upload_transcode_format: str = "mp4"
    upload_transcode_bitrate: str = "128k"
    download_progress_poll_interval_s: float = 0.3
    # TTL for the Redis download-progress record. Outlives a normal download so
    # the SSE client (possibly on another gunicorn worker) can read it; expires
    # on its own if a download dies without clearing the key.
    download_progress_ttl_s: int = 3600
    download_progress_redis_prefix: str = "nabicat:tubio:progress:"
    youtube_403_fallback_player_client: str = "web"
    youtube_watch_url_template: str = "https://www.youtube.com/watch?v={video_id}"
    youtube_mix_url_template: str = "https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    youtube_thumbnail_url_template: str = "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    youtube_search_url: str = "https://www.youtube.com/results"
    youtube_url_patterns: tuple[str, ...] = (
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    )
    youtube_search_request_timeout_s: float = 10.0
    youtube_thumbnail_request_timeout_s: float = 10.0
    cookie_keepalive_url: str = "https://www.youtube.com/feed/subscriptions"
    cookie_keepalive_timeout_s: float = 30.0
    cookie_keepalive_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    youtube_download_format: str = "bestaudio[ext=m4a]/bestaudio/best"
    youtube_audio_preferred_codec: str = "m4a"
    youtube_audio_preferred_quality: str = "32"
    default_playlist_name: str = "Favourites"
    trackbar_volume_min_percent: int = 0
    trackbar_volume_max_percent: int = 100
    trackbar_volume_step_percent: int = 1
    trackbar_default_volume_percent: int = 80
    trackbar_volume_storage_key: str = "tubio.volume"
    trackbar_muted_storage_key: str = "tubio.muted"
    sidebar_collapsed_storage_key: str = "tubioSidebarCollapsed"
    sidebar_selected_storage_key: str = "tubioSelectedPlaylist"
    autocomplete_max_suggestions: int = 8
    autocomplete_min_query_len: int = 2
    autocomplete_debounce_ms: int = 200
    autocomplete_suggest_url: str = "https://suggestqueries.google.com/complete/search"
    autocomplete_request_timeout_s: float = 3.0
    surprise_mix_entries_per_seed: int = 15
    # Number of Surprise metadata entries kept ready ahead of playback.
    surprise_buffer_size: int = 5
    surprise_grow_batch_size: int = 1
    surprise_playlist_name: str = "Surprise Playlist"
    surprise_playlist_storage_key: str = "__surprise_playlist__"
    surprise_playlist_inactivity_ttl_s: int = 3600
    surprise_crc_collision_attempts: int = 100
    surprise_media_rate_limit: str = "30 per minute"
    playlist_create_rate_limit: str = "10 per minute"
    playlist_move_rate_limit: str = "20 per minute"
    playlist_delete_rate_limit: str = "10 per minute"
    upload_rate_limit: str = "20 per minute"
    audio_serve_rate_limit: str = "100 per second"
    resync_rate_limit: str = "5 per minute"
    client_log_rate_limit: str = "30 per minute"
    client_log_max_length: int = 2000
    client_log_scopes: tuple[str, ...] = (
        "discover-initialize",
        "media-element",
        "media-session-metadata",
        "playback-request",
        "surprise-payload",
        "surprise-refresh",
        "track-prefetch",
    )
    surprise_cache_claim_ttl_s: int = 3600
    surprise_cache_claim_token_bytes: int = 12
    surprise_cache_poll_interval_ms: int = 750
    surprise_cache_redis_prefix: str = "nabicat:tubio:cache:"

    @property
    def cookie_path(self) -> Path:
        return self._save_data_path() / "cookies.txt"


@dataclass
class TodoistConfig:
    default_page_size: int = 8
    goal_drag_hold_ms: int = 350
    goal_drag_move_threshold_px: int = 8
    goal_drag_hover_expand_ms: int = 650


@dataclass
class GPTActionsConfig:
    authorization_code_ttl_s: int = 600
    access_token_ttl_s: int = 3600
    refresh_token_ttl_s: int = 30 * 24 * 60 * 60
    consent_ttl_s: int = 365 * 24 * 60 * 60
    read_scope: str = "todoist.goals.read"
    default_page_size: int = 50
    max_page_size: int = 100
    idempotency_ttl_s: int = 24 * 60 * 60
    idempotency_pending_ttl_s: int = 60
    idempotency_key_max_length: int = 200

    @property
    def client_id(self) -> str:
        return getenv("OAUTH_CLIENT_ID", "")

    @property
    def client_secret(self) -> str:
        return getenv("OAUTH_CLIENT_SECRET", "")

    @property
    def client_secret_hash(self) -> str:
        return getenv("OAUTH_CLIENT_SECRET_HASH", "")

    @property
    def redirect_uris(self) -> tuple[str, ...]:
        return tuple(
            uri.strip()
            for uri in getenv("OAUTH_REDIRECT_URIS", "").split(",")
            if uri.strip()
        )


@dataclass
class DevConfig:
    terminal_shell: str = "/bin/bash"
    terminal_max_sessions: int = 4
    terminal_idle_timeout_s: int = 1800
    terminal_buffer_bytes: int = 1_048_576
    terminal_read_chunk: int = 4096
    log_relative_path: Path = Path("logs/web_app.log")
    log_rotation_max_bytes: int = 5_000_000
    log_rotation_backup_count: int = 20
    log_viewer_file_count: int = 2
    log_viewer_max_lines: int = 5000
    map_geo_timeout_s: int = 8
    map_geo_cache_ttl_s: int = 3600
    map_geo_batch_size: int = 100
    map_max_ips: int = 500
    map_geo_url: str = "http://ip-api.com/batch"


@dataclass
class CrosswordsConfig:
    # Provider-agnostic capability tier; LLMConfig.model_for() resolves it
    # to a concrete model for the active api_source.
    llm_tier: str = "medium"  # weak | medium | strong
    word_count: int = 7
    min_placed_words: int = 3
    llm_generation_max_tokens: int = 1024
    llm_generation_timeout_s: float = 20.0
    llm_theme_check_max_tokens: int = 4
    llm_theme_check_timeout_s: float = 10.0
    default_theme: str = "cats"
    default_difficulty: int = 2
    difficulty_min: int = 1
    difficulty_max: int = 5
    theme_min_len: int = 2
    theme_max_len: int = 13


@dataclass
class LoftConfig:
    request_path_prefix: str = "/loft/"
    non_admin_quota_bytes: int = 50 * 1024 * 1024
    admin_quota_bytes: int = 10 * 1024 * 1024 * 1024
    gallery_max_files_per_upload: int = 20
    gallery_max_videos_per_upload: int = 1
    gallery_media_filename_max_chars: int = 100
    gallery_upload_stream_chunk_bytes: int = 1024 * 1024
    gallery_upload_max_total_bytes: int = 250 * 1024 * 1024
    gallery_request_max_bytes: int = 252 * 1024 * 1024
    gallery_quota_lock_timeout_s: int = 30
    gallery_quota_lock_blocking_timeout_s: float = 10.0
    gallery_staging_root: Path | None = None
    gallery_staging_dirname: str = "loft-gallery-upload-staging"
    gallery_staging_dir_mode: int = 0o700
    gallery_staging_max_age_s: int = 3600
    gallery_publish_journal_prefix: str = ".gallery-publish-"
    gallery_publish_journal_suffix: str = ".json"
    gallery_backup_excluded_names: tuple[str, ...] = (
        ".gallery-upload-staging",
    )
    gallery_image_max_upload_bytes: int = 25 * 1024 * 1024
    gallery_image_allowed_formats: tuple[str, ...] = (
        "AVIF",
        "BMP",
        "GIF",
        "HEIF",
        "JPEG",
        "MPO",
        "PNG",
        "TIFF",
        "WEBP",
    )
    gallery_thumb_max_px: int = 1400
    gallery_thumb_quality: int = 80
    max_image_pixels: int = 40_000_000
    gallery_image_max_batch_pixels: int = 80_000_000
    gallery_video_max_upload_bytes: int = 100 * 1024 * 1024
    gallery_video_max_duration_s: int = 60
    gallery_video_allowed_demuxers: tuple[str, ...] = (
        "avi",
        "matroska",
        "mov",
        "webm",
    )
    gallery_video_protocol_whitelist: str = "file"
    gallery_video_probe_size_bytes: int = 10 * 1024 * 1024
    gallery_video_analyze_duration_us: int = 10 * 1_000_000
    gallery_video_probe_timeout_s: int = 20
    gallery_video_max_input_width_px: int = 8192
    gallery_video_max_input_height_px: int = 8192
    gallery_video_max_input_pixels: int = 40_000_000
    gallery_video_max_input_fps: int = 240
    gallery_video_max_width_px: int = 1280
    gallery_video_max_height_px: int = 720
    gallery_video_max_output_fps: int = 60
    gallery_video_max_output_bytes: int = 100 * 1024 * 1024
    gallery_video_duration_tolerance_s: float = 0.5
    gallery_video_ffmpeg_threads: int = 2
    gallery_video_ffmpeg_max_alloc_bytes: int = 512 * 1024 * 1024
    gallery_video_max_muxing_queue_packets: int = 1024
    gallery_video_h264_preset: str = "veryfast"
    gallery_video_h264_crf: int = 28
    gallery_video_h264_profile: str = "high"
    gallery_video_h264_level: str = "3.2"
    gallery_video_output_encoder: str = "libx264"
    gallery_video_output_codec: str = "h264"
    gallery_video_output_codec_tag: str = "avc1"
    gallery_video_output_pixel_format: str = "yuv420p"
    gallery_video_output_audio_codec: str = "aac"
    gallery_video_output_audio_encoder: str = "aac"
    gallery_video_output_audio_codec_tag: str = "mp4a"
    gallery_video_output_audio_profile: str = "aac_low"
    gallery_video_output_color_space: str = "bt709"
    gallery_video_output_color_range: str = "tv"
    gallery_video_sd_input_color_space: str = "smpte170m"
    gallery_video_hd_input_color_space: str = "bt709"
    gallery_video_sd_max_height_px: int = 576
    gallery_video_output_format: str = "mp4"
    gallery_video_output_demuxer: str = "mov"
    gallery_video_audio_bitrate: str = "96k"
    gallery_video_audio_channels: int = 2
    gallery_video_audio_sample_rate_hz: int = 48_000
    gallery_video_hdr_peak_nits: int = 100
    gallery_video_hdr_desaturation: float = 0.5
    gallery_video_hdr_tonemap_algorithm: str = "mobius"
    gallery_video_hdr_transfers: tuple[str, ...] = (
        "arib-std-b67",
        "smpte2084",
    )
    gallery_video_private_metadata_fragments: tuple[str, ...] = (
        "artist",
        "comment",
        "copyright",
        "creation_time",
        "description",
        "device",
        "gps",
        "location",
        "make",
        "model",
        "title",
    )
    gallery_video_transcode_timeout_s: int = 180
    gallery_image_stagger_ms: int = 200
    gallery_image_max_retries: int = 3
    gallery_image_retry_delay_ms: int = 1000
    title_max_chars: int = 120
    description_max_chars: int = 2048
    markdown_max_chars: int = 256 * 1024
    project_slug_max_chars: int = 64


@dataclass
class FileStoreConfig:
    non_admin_quota_bytes: int = 30 * 1024 * 1024
    admin_quota_bytes: int = 10 * 1024 * 1024 * 1024
    upload_stream_chunk_bytes: int = 1024 * 1024
    folder_upload_max_entries: int = 10_000
    archive_stream_queue_chunks: int = 8
    thumbnail_load_stagger_ms: int = 200
    thumbnail_load_max_retries: int = 3
    thumbnail_retry_delay_ms: int = 1_000
    gallery_columns_min: int = 2
    gallery_columns_max: int = 10
    gallery_columns_default: int = 5
    gallery_min_tile_px: int = 40


@dataclass
class ProxyConfig:
    request_timeout_s: int = 10
    max_redirects: int = 5
    response_max_bytes: int = 5 * 1024 * 1024
    response_read_chunk_bytes: int = 64 * 1024
    redirect_status_codes: tuple[int, ...] = (301, 302, 303, 307, 308)
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    )
    blocked_metadata_hostnames: tuple[str, ...] = (
        "metadata",
        "metadata.aws.internal",
        "metadata.azure.internal",
        "metadata.google.internal",
    )
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


class ConfigManager:
    _instance = None  # Class-level variable to store the single instance

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            # If no instance exists, create a new one
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __init__ will be called every time, even for existing instances,
        # but the configuration loading logic should only run once.
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.use_offline_syncer = True
        self.debug_mode = False
        self.production_data_root = Path.home() / ".nabicat" / "data"
        self.debug_data_root = Path.home() / ".nabicat_debug" / "data"
        self.server_host = "0.0.0.0"
        self.server_default_port = 80
        self.session_cookie_name = "session"
        self.debug_session_cookie_name = "session_debug"
        self.site_url = getenv("SITE_URL") or "https://nabicat.site"
        self.redis_url = getenv("REDIS_URL") or "redis://127.0.0.1:6379/0"
        self.redis_readiness_timeout_s = 5.0
        self.redis_readiness_poll_s = 0.1
        self.password_hash_method = "scrypt"
        self.password_hash_prefix = "nabicat$"
        self.gunicorn_workers = 4
        self.gunicorn_request_timeout_s = 720
        self.gunicorn_graceful_timeout_s = 720
        self.deployment_canary_port = 5001
        self.deployment_health_attempts = 30
        self.deployment_health_interval_s = 1
        self.deployment_lock_path = Path.home() / ".nabicat" / "update.lock"
        self.scheduled_job_service_unit_name = "nabicat-scheduled-job@.service"
        self.scheduled_job_timeout_s = 3600
        self.scheduled_backup_job_id = "backup"
        self.scheduled_cookie_keepalive_job_id = "cookie-keepalive"
        self.scheduled_download_health_check_job_id = "download-health-check"
        self.scheduled_job_timers = (
            (
                "nabicat-backup.timer",
                self.scheduled_backup_job_id,
                "Sun *-*-* 00:00:00",
            ),
            (
                "nabicat-cookie-keepalive.timer",
                self.scheduled_cookie_keepalive_job_id,
                "*-*-* 04:00:00",
            ),
            (
                "nabicat-download-health-check.timer",
                self.scheduled_download_health_check_job_id,
                "*-*-* 04:10:00",
            ),
        )
        self.log_format = (
            "%(asctime)s %(levelname)s worker=%(process)d "
            "thread=%(thread)d %(message)s"
        )
        self.request_id_header = "X-Request-ID"
        self.request_log_warning_status = 400
        self.request_log_error_status = 500
        # rmw_lock lease TTL, acquisition deadline, and renewal cadence. Active
        # holders renew; crashed holders expire after the TTL.
        self.rmw_lock_timeout_s = 10
        self.rmw_lock_blocking_timeout_s = 5.0
        self.rmw_lock_renewal_interval_s = 3.0
        self.installed_app_file_mode = 0o600
        self.installed_app_state_key_prefix = "nabicat:app:{app_id}:state:"
        self.installed_app_lease_key_prefix = "nabicat:app:{app_id}:lease:"
        self.installed_app_text_temp_prefix = "nabicat-{app_id}-text-"
        self.installed_app_text_image_extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        self.installed_app_default_llm_tier = "medium"
        self.installed_app_llm_tiers = {"sentinel": "strong"}
        self.installed_app_codex_models = {"sentinel": "gpt-5.6-sol"}
        self.installed_app_codex_reasoning_effort = {"sentinel": "medium"}
        self.installed_app_codex_permissions_profile = {
            "sentinel": "sentinel_qa"
        }
        self.installed_app_config_overrides: dict[str, dict[str, object]] = {}
        self.backup_max_count = 8
        self.production_sync_excluded_paths = (
            "backups/",
            "data/logs/",
        )
        # Unmatched paths containing these segments are high-confidence
        # vulnerability probes. They receive a direct 404 without generic
        # request lifecycle logs; ordinary unknown URLs remain logged.
        self.scanner_path_segment_names = frozenset({
            ".aws",
            ".env",
            ".git",
            ".mist",
            ".ssh",
            "actuator",
            "dns-query",
            "eval-stdin.php",
            "phpunit",
            "xmlrpc",
            "xmlrpc.php",
        })
        self.scanner_path_segment_prefixes = (
            ".env.",
            "phpmyadmin",
            "wp-",
        )
        self.scanner_methods = frozenset({"PROPFIND", "TRACK", "TRACE"})
        self.request_log_suppressed_paths = {
            '/dev/terminal/input',
            '/dev/terminal/output',
        }
        self.cache_max_age = 606461 # Default cache max age (1 week) in seconds, can be overridden by environment variable
        self.cache_browser_max_size_bytes = 10 * 1024 * 1024 * 1024
        self.cache_service_worker_version = "v2"
        self.cache_service_worker_prefix = "nabicat-cache-"
        self.cache_versioned_static_path_prefixes = (
            "/static/",
            "/crosswords/static/",
            "/dev/static/",
            "/file_store/static/",
            "/loft/static/",
            "/metrics/static/",
            "/proxy/static/",
            "/simulations/static/",
            "/todoist/static/",
            "/tubio/static/",
        )
        self.cache_public_media_path_prefixes = (
            "/tubio/audio/",
            "/tubio/thumbnail/",
        )
        self.cache_service_worker_ready_timeout_ms = 5000
        self.cache_service_worker_message_timeout_ms = 5000
        self.cache_public_media_endpoints = frozenset({
            "tubio.serve_audio",
            "tubio.serve_thumbnail",
        })
        self.git_command_timeout_s = 2
        self.ytdlp_pypi_url = "https://pypi.org/pypi/yt-dlp/json"
        self.ytdlp_requirement_pattern = (
            r"^yt-dlp\[default\]>=(?P<version>[^ \t;#\r\n]+)"
            r"[ \t]*(?:#.*)?\r?$"
        )
        self.ytdlp_update_timeout_s = 10.0
        self.access_denied_redirect_endpoint = "home"
        self.elevated_access_denied_message = "You need elevated access to use this app."
        self.admin_access_denied_message = "You need admin access to use this app."
        self.dev_access_denied_api_prefixes = ("/dev/logs", "/dev/map-data", "/dev/terminal/")
        self.smtp_port = 587
        self.project_dir = Path.cwd()
        # TTL for the ephemeral RSA keypair minted during the encrypted-request
        # handshake. Surfaced to clients as `expires_in` in /api/handshake.
        self.ephemeral_key_ttl_s = 300
        self.llm = LLMConfig()
        self.tubio = TubioConfig(lambda: self.save_data_path)
        self.todoist = TodoistConfig()
        self.gpt_actions = GPTActionsConfig()
        self.dev = DevConfig()
        self.crosswords = CrosswordsConfig()
        self.loft = LoftConfig()
        self.file_store = FileStoreConfig()
        self.proxy = ProxyConfig()

    @property
    def project_name(self) -> str:
        return "nabicat" if not self.debug_mode else "nabicat_debug"

    @property
    def save_data_path(self) -> Path:
        return self.debug_data_root if self.debug_mode else self.production_data_root
    
    @property
    def temp_dir(self) -> Path:
        return self.save_data_path / "temp"

    @property
    def log_file_path(self) -> Path:
        return self.save_data_path / self.dev.log_relative_path

    @property
    def flask_secret_key(self) -> str:
        key = getenv('FLASK_SECRET_KEY')
        if key:
            return key

        if self.debug_mode:
            return "DEBUG_FLASK_SECRET_KEY"

        raise ValueError("Flask secret key is not set. Please set the 'FLASK_SECRET_KEY' environment variable.")

    @property
    def flask_session_cookie_name(self) -> str:
        return (
            self.debug_session_cookie_name
            if self.debug_mode
            else self.session_cookie_name
        )

    @property
    def smtp_host(self) -> str:
        return getenv('SMTP_HOST', '')

    @property
    def smtp_user(self) -> str:
        return getenv('SMTP_USER', '')

    @property
    def smtp_password(self) -> str:
        return getenv('SMTP_PASSWORD', '')

    @property
    def alert_email_to(self) -> str:
        return getenv('ALERT_EMAIL_TO', '')
