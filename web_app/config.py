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
    download_progress_poll_interval_s: float = 0.3
    # TTL for the Redis download-progress record. Outlives a normal download so
    # the SSE client (possibly on another gunicorn worker) can read it; expires
    # on its own if a download dies without clearing the key.
    download_progress_ttl_s: int = 3600
    youtube_403_fallback_player_client: str = "web"
    default_playlist_name: str = "Favourites"
    trackbar_volume_min_percent: int = 0
    trackbar_volume_max_percent: int = 100
    trackbar_volume_step_percent: int = 1
    trackbar_default_volume_percent: int = 80
    trackbar_volume_storage_key: str = "tubio.volume"
    trackbar_muted_storage_key: str = "tubio.muted"
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
    client_log_rate_limit: str = "30 per minute"
    client_log_max_length: int = 2000
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
class SentinelConfig:
    # Explicit capability for QA against loopback/private targets. This is
    # intentionally independent of Flask debug mode.
    allow_local_targets: bool = False
    abandoned_run_timeout_s: int = 900
    verdict_reason_max_chars: int = 300
    report_schema_version: int = 1
    cli_schema_version: int = 1
    cli_exit_pass: int = 0
    cli_exit_fail: int = 1
    cli_exit_inconclusive: int = 2
    cli_exit_interrupted: int = 130
    report_url_base: str = ""
    console_finding_title: str = "Console"
    console_finding_kind: str = "browser.console"
    default_limit_mins: int = 5
    min_limit_mins: int = 1
    max_limit_mins: int = 10
    # TTL for the Redis cancel flag. Must exceed the longest possible run
    # (max_limit_mins) so a cancel request on any gunicorn worker still reaches
    # the run loop's worker before the flag expires.
    cancel_flag_ttl_s: int = 3600
    max_steps: int = 50
    max_screenshots: int = 50
    # If the agent emits a malformed/non-JSON response, retry once before
    # aborting the run. Catches transient LLM hiccups.
    agent_parse_retry_attempts: int = 1
    # When the agent clicks the same element_id this many times consecutively
    # without the page URL changing, surface a finding so the agent gets a
    # hint to try something else.
    click_loop_threshold: int = 3
    # If the click-loop warning fires this many distinct times in a single run,
    # treat the agent as stuck and end the run with a "stuck" finish. Stops the
    # agent from burning the whole step budget on broken controls.
    click_loop_max_warnings: int = 3
    max_retained_runs: int = 25
    prompt_max_chars: int = 4000
    additional_domains_max_count: int = 10
    additional_domain_max_chars: int = 253
    browser_width_px: int = 1366
    browser_height_px: int = 900
    browser_default_timeout_ms: int = 15000
    # Desktop user-agent override: replaces Playwright's default
    # "HeadlessChrome/..." UA, which is the most common bot-detection trigger.
    # Mobile/tablet device profiles supply their own real-device UA and bypass
    # this override.
    browser_desktop_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # Chromium launch flags applied to every Sentinel run. Disables the
    # AutomationControlled blink feature so navigator.webdriver and related
    # CDP fingerprints don't immediately trip Cloudflare/Akamai bot rules.
    browser_launch_args: list = field(default_factory=lambda: [
        "--disable-blink-features=AutomationControlled",
    ])
    navigation_timeout_ms: int = 30000
    post_click_load_timeout_ms: int = 5000
    # Settle delay after a click. Gives modals/menus/transitions a moment to
    # finish before the next observation runs.
    post_click_settle_ms: int = 600
    # Settle delay after a fill. Mostly debounced JS validators / autocomplete.
    post_fill_settle_ms: int = 200
    # Settle delay after a select. Native dropdowns can re-render the page.
    post_select_settle_ms: int = 1000
    # Settle delay after a scroll. Lazy-loaded content / IntersectionObservers.
    post_scroll_settle_ms: int = 1000
    # Pause for the explicit "wait" action.
    wait_action_ms: int = 1000
    scroll_action_delta_px: int = 650
    scroll_position_tolerance_px: int = 2
    full_page_scope_prompt_pattern: str = (
        r"\b(?:all|every|each|whole|entire|full)\b.{0,80}\b"
        r"(?:apps?|cards?|links?|items?|rows?|sections?|pages?|menus?|public|private)\b"
        r"|\b(?:apps?|cards?|links?|items?|rows?|sections?|pages?|menus?|public|private)\b"
        r".{0,80}\b(?:all|every|each|whole|entire|full)\b"
    )
    observation_max_elements: int = 80
    observation_text_max_chars: int = 3000
    observation_element_text_max_chars: int = 140
    finding_detail_max_chars: int = 500
    final_report_max_chars: int = 4000
    final_report_max_images: int = 4
    final_report_timeout_s: float = 60.0
    title_max_chars: int = 80
    llm_title_max_tokens: int = 80
    llm_title_timeout_s: float = 15.0
    llm_verdict_max_tokens: int = 200
    llm_verdict_timeout_s: float = 20.0
    # The screenshot picker decides which screenshots from the run are worth
    # attaching to the final-report LLM call. Cheaper than blindly attaching
    # every frame.
    llm_picker_max_tokens: int = 300
    llm_picker_timeout_s: float = 20.0
    # How many screenshots the picker is allowed to select.
    final_report_picker_budget: int = 6
    annotation_box_width_px: int = 3
    annotation_label_font_px: int = 14
    annotation_label_pad_px: int = 4
    screenshot_load_stagger_ms: int = 200
    screenshot_load_max_retries: int = 3
    screenshot_load_retry_delay_ms: int = 1000
    screenshot_thumb_max_px: int = 360
    # PDF export page geometry (Playwright page.pdf margins). Bottom is larger
    # than top to leave room for the running footer, which renders inside the
    # bottom margin band.
    pdf_margin_top: str = "16mm"
    pdf_margin_bottom: str = "18mm"
    pdf_margin_left: str = "14mm"
    pdf_margin_right: str = "14mm"
    pdf_footer_label: str = "Generated by Sentinel"
    # Batch jobs: queue several runs at once that share a batch_id (e.g. a
    # mobile run + a desktop run). Batches are not persisted as their own
    # entity — they are re-derived by grouping runs on batch_id.
    max_batch_items: int = 8          # max runs queued in one batch submit
    max_retained_batches: int = 25    # max derived batch groups shown in sidebar
    batch_name_max_chars: int = 80    # caps the batch label stored on each run
    batch_name_fallback: str = "Sentinel batch"
    # Capability tier used by Meridian and Bedrock. Codex is pinned separately
    # below because it also needs a Sentinel-specific reasoning effort.
    llm_tier: str = "strong"  # weak | medium | strong
    # Provider-agnostic LLM behavior knobs.
    llm_step_timeout_s: float = 45.0
    llm_step_max_tokens: int = 1024
    llm_final_report_max_tokens: int = 2048
    # Codex-specific settings. Pin these so Sentinel does not inherit mutable
    # user-wide Codex CLI defaults.
    codex_model: str = "gpt-5.6-sol"
    codex_reasoning_effort: str = "medium"
    codex_permissions_profile: str = "sentinel_qa"
    # Friendly device key -> Playwright devices registry name. Empty string
    # means "no emulation; use browser_width/height_px viewport".
    device_profiles: dict = field(default_factory=lambda: {
        "desktop":     "",
        "tablet":      "iPad (gen 7)",
        "large_phone": "iPhone 13 Pro Max",
        "small_phone": "iPhone SE",
    })
    device_labels: dict = field(default_factory=lambda: {
        "desktop":     "Desktop",
        "tablet":      "Tablet",
        "large_phone": "Large Phone",
        "small_phone": "Small Phone",
    })
    default_device: str = "desktop"
    # Demographic key -> persona sentence prepended to the agent system prompt.
    demographic_personas: dict = field(default_factory=lambda: {
        "child":  "You are an 8-year-old child using a website for the first time; you click colorful things, get bored fast, and cannot read long text.",
        "adult":  "You are a typical adult web user with average tech literacy who skims interfaces and expects standard web conventions.",
        "senior": "You are a senior in your 70s with limited tech experience; small targets, jargon, and unexpected layouts confuse you, and you prefer obvious, labeled controls.",
        "techie": "You are a power user comfortable with developer tools, keyboard shortcuts, and dense UIs; you probe edge cases and unusual flows.",
    })
    demographic_labels: dict = field(default_factory=lambda: {
        "child":  "Child",
        "adult":  "Adult",
        "senior": "Senior",
        "techie": "Techie",
    })
    default_demographic: str = "adult"
    # Keywords that imply the prompt depends on auth flows. If any appear in
    # the prompt while allow_accounts=false, the run is rejected up-front so
    # the agent doesn't immediately self-abort. Matched as whole words
    # (case-insensitive) against the prompt.
    account_keywords: tuple = (
        "account", "accounts",
        "sign up", "signup", "sign-up",
        "sign in", "signin", "sign-in",
        "log in", "login", "log-in",
        "register", "registration",
    )
    region_labels: dict = field(default_factory=lambda: {
        "australia": "Australia",
        "china":     "China",
        "us":        "US",
        "uk":        "UK",
        "japan":     "Japan",
    })
    default_region: str = "australia"


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
        self.password_hash_method = "scrypt"
        self.password_hash_prefix = "nabicat$"
        self.gunicorn_workers = 4
        self.gunicorn_request_timeout_s = 300
        self.gunicorn_graceful_timeout_s = 300
        self.deployment_canary_port = 5001
        self.deployment_health_attempts = 30
        self.deployment_health_interval_s = 1
        self.deployment_lock_path = Path.home() / ".nabicat" / "update.lock"
        self.deployment_canary = getenv("NABICAT_DEPLOYMENT_CANARY") == "1"
        self.log_format = (
            "%(asctime)s %(levelname)s worker=%(process)d "
            "thread=%(thread)d %(message)s"
        )
        self.request_id_header = "X-Request-ID"
        self.request_log_warning_status = 400
        self.request_log_error_status = 500
        # TTL for the per-job exactly-once lock guarding scheduled cron jobs so
        # they run once across gunicorn workers. Must exceed the longest job
        # runtime and matches the jobs' misfire_grace_time.
        self.scheduler_lock_ttl_s = 3600
        # rmw_lock: how long a held lock auto-expires (guards a crashed
        # holder) and how long a waiter blocks before giving up. RMW spans are
        # short, so both are small.
        self.rmw_lock_timeout_s = 10
        self.rmw_lock_blocking_timeout_s = 5.0
        self.backup_max_count = 8
        # Requests matching these prefixes are silently dropped (404, no log) — automated bots/scanners probing for common vulnerabilities
        self.known_bot_prefixes = {
            '/.env',        # env file harvesting (.env, .env.local, .env.production, etc.)
            '/.git/',       # git config/object exposure
            '/wp-',         # WordPress scanners (wp-admin, wp-login, wp-includes, xmlrpc)
            '/xmlrpc',      # WordPress XML-RPC
            '/phpmyadmin',  # phpMyAdmin probes
            '/.mist/',      # Juniper/Mist IoT probes
            '/dns-query',   # DNS-over-HTTPS probes
        }
        self.known_bot_methods = {'PROPFIND', 'TRACK', 'TRACE'}
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
            "/sentinel/static/",
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
        self.access_denied_redirect_endpoint = "home"
        self.elevated_access_denied_message = "You need elevated access to use Sentinel."
        self.admin_access_denied_message = "You need admin access to use this app."
        self.sentinel_access_denied_api_prefixes = ("/sentinel/api/",)
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
        self.sentinel = SentinelConfig()
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
