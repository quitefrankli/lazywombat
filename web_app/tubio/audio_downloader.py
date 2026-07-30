import requests
import re
import json
import logging
import yt_dlp
import os
import binascii

from typing import *
from pathlib import Path
from datetime import datetime, timedelta

from web_app.config import ConfigManager
from web_app.redis_client import get_redis
from web_app.tubio.data_interface import Metadata, UserMetadata, AudioMetadata, DataInterface
from web_app.users import User
from web_app.logging_utils import log_event


class VideoTooLongError(Exception):
    """Raised when a video exceeds the maximum allowed length."""
    def __init__(self, video_id: str, duration: timedelta, max_duration: timedelta):
        self.video_id = video_id
        self.duration = duration
        self.max_duration = max_duration
        super().__init__(
            f"Video {video_id} is too long ({duration} > {max_duration})"
        )


_PROGRESS_PREFIX = "nabicat:tubio:progress:"


class DownloadProgress:
    """Tracks download progress for a specific video.

    State lives in Redis (keyed by video_id) so the SSE progress stream can be
    served by a different gunicorn worker than the one running the download.
    Every attribute assignment write-throughs to Redis.
    """
    def __init__(self, video_id: str):
        # Bypass __setattr__ during init so the write-through sees a complete
        # object.
        object.__setattr__(self, "video_id", video_id)
        object.__setattr__(self, "percent", 0.0)
        object.__setattr__(self, "status", "starting")
        object.__setattr__(self, "error", None)
        self._persist()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name in ("percent", "status", "error"):
            self._persist()

    def _persist(self) -> None:
        payload = json.dumps({
            "percent": self.percent,
            "status": self.status,
            "error": self.error,
        })
        get_redis().set(
            _PROGRESS_PREFIX + self.video_id,
            payload,
            ex=ConfigManager().tubio.download_progress_ttl_s,
        )


def get_download_progress(video_id: str) -> DownloadProgress | None:
    raw = get_redis().get(_PROGRESS_PREFIX + video_id)
    if raw is None:
        return None
    data = json.loads(raw)
    # Rebuild without re-persisting: construct bare, then set fields directly.
    progress = object.__new__(DownloadProgress)
    object.__setattr__(progress, "video_id", video_id)
    object.__setattr__(progress, "percent", data.get("percent", 0.0))
    object.__setattr__(progress, "status", data.get("status", "starting"))
    object.__setattr__(progress, "error", data.get("error"))
    return progress


def clear_download_progress(video_id: str) -> None:
    get_redis().delete(_PROGRESS_PREFIX + video_id)


class AudioDownloader:
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

    # Patterns for YouTube URLs
    YOUTUBE_URL_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]

    @staticmethod
    def extract_video_id(query: str) -> str | None:
        """Extract video ID from a YouTube URL. Returns None if not a valid YouTube URL."""
        query = query.strip()
        for pattern in AudioDownloader.YOUTUBE_URL_PATTERNS:
            match = re.match(pattern, query)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def get_video_info(video_id: str, cached_yt_vid_ids: Set[str]) -> dict | None:
        """Fetch info for a single video by ID using yt-dlp."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }

        if ConfigManager().tubio.cookie_path.exists():
            ydl_opts['cookiefile'] = str(ConfigManager().tubio.cookie_path)
        if ConfigManager().debug_mode:
            ydl_opts['nocheckcertificate'] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                duration = info.get('duration', 0)
                vid_length = timedelta(seconds=duration)
                max_length = ConfigManager().tubio.max_video_length

                if vid_length > max_length:
                    raise VideoTooLongError(video_id, vid_length, max_length)

                length_txt = AudioDownloader._format_duration(duration)

                cached = video_id in cached_yt_vid_ids
                view_count = info.get('view_count', 0)
                view_count_str = f"{view_count:,} views" if view_count else ''

                # Get best thumbnail URL
                thumbnail_url = info.get('thumbnail', '')
                if not thumbnail_url:
                    thumbnails = info.get('thumbnails', [])
                    if thumbnails:
                        # Prefer medium quality thumbnail
                        thumbnail_url = thumbnails[-1].get('url', '')

                return {
                    "video_id": video_id,
                    "url": url,
                    "title": info.get('title', ''),
                    "description": info.get('description', '')[:500] if info.get('description') else '',
                    "view_count": view_count_str,
                    "published": info.get('upload_date', ''),
                    "length": length_txt,
                    "cached": cached,
                    "thumbnail_url": thumbnail_url,
                }
        except VideoTooLongError:
            raise
        except Exception as error:
            log_event(
                "tubio", "tubio.video_info_failed",
                level=logging.ERROR, video_id=video_id,
                exc_info=error, error_type=type(error).__name__,
            )
            return None

    @staticmethod
    def _format_duration(seconds) -> str:
        """Format a duration in seconds as MM:SS or HH:MM:SS."""
        seconds = int(seconds or 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def get_mix_related(video_id: str) -> List[dict]:
        """Fetch YouTube's Mix (radio) recommendations seeded by a video.

        Returns search-card-shaped dicts (same shape as search_youtube results).
        Best-effort: returns [] on failure so Surprise generation can exhaust
        cleanly instead of breaking the page.
        Each result carries an integer `duration_s` so the caller can apply the
        length cap.
        """
        cfg = ConfigManager().tubio
        log_event(
            "tubio", "tubio.youtube_mix_started",
            seed_video_id=video_id,
            max_entries=cfg.surprise_mix_entries_per_seed,
        )
        url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': True,
            'playlist_items': f"1-{cfg.surprise_mix_entries_per_seed}",
        }
        if cfg.cookie_path.exists():
            ydl_opts['cookiefile'] = str(cfg.cookie_path)
        if ConfigManager().debug_mode:
            ydl_opts['nocheckcertificate'] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = (info or {}).get('entries') or []
        except Exception as error:
            log_event(
                "tubio", "tubio.youtube_mix_failed",
                level=logging.ERROR, seed_video_id=video_id,
                exc_info=error, error_type=type(error).__name__,
            )
            return []

        results = []
        for entry in entries:
            if not entry:
                continue
            vid = entry.get('id')
            if not vid:
                continue
            duration = entry.get('duration') or 0
            thumbnails = entry.get('thumbnails') or []
            thumbnail_url = thumbnails[-1].get('url', '') if thumbnails else ''
            results.append({
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": entry.get('title', ''),
                "description": entry.get('channel') or entry.get('uploader') or '',
                "view_count": '',
                "published": '',
                "length": AudioDownloader._format_duration(duration),
                "duration_s": int(duration),
                "cached": False,
                "thumbnail_url": thumbnail_url,
            })
        log_event(
            "tubio", "tubio.youtube_mix_completed",
            seed_video_id=video_id,
            entries=len(entries), usable=len(results),
        )
        return results

    @staticmethod
    def get_vid_length(text: str) -> timedelta:
        parts = reversed(text.split(':'))
        sec_map = [ 1, 60, 3600 ]  # seconds, minutes, hours
        total_seconds = sum(int(part) * sec for part, sec in zip(parts, sec_map))

        return timedelta(seconds=total_seconds)

    @staticmethod
    def suggest_queries(query: str) -> List[str]:
        """Best-effort YouTube search-term suggestions for autocomplete.

        Uses Google's public suggest endpoint (returns JSON `[query, [suggestions...]]`).
        Never raises — returns [] on any network/parse failure so typing stays responsive.
        """
        cfg = ConfigManager().tubio
        try:
            response = requests.get(
                cfg.autocomplete_suggest_url,
                params={"client": "firefox", "ds": "yt", "q": query},
                timeout=cfg.autocomplete_request_timeout_s,
            )
            response.raise_for_status()
            suggestions = json.loads(response.text)[1]
            return [str(s) for s in suggestions][: cfg.autocomplete_max_suggestions]
        except Exception as error:
            log_event(
                "tubio", "tubio.suggestions_failed",
                level=logging.WARNING, exc_info=error,
                error_type=type(error).__name__,
            )
            return []

    @staticmethod
    def _scrape_search_page(query: str, cached_yt_vid_ids: Set[str], sp: Optional[str] = None) -> dict:
        """Scrape a single YouTube results page, applying the max-length cap.

        Returns {"results": [survivors], "filtered_too_long": [long_video_ids], "raw_count": int}
        where raw_count is the number of parseable videoRenderer items on the page (before the
        length cap). `sp` is the raw base64 duration-filter param, added only when truthy.
        """
        params = {"search_query": query}
        if sp:
            params["sp"] = sp
        response = requests.get(AudioDownloader.YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        html = response.text
        # Extract ytInitialData JSON
        initial_data_match = re.search(r'var ytInitialData = (\{.*?\});', html, re.DOTALL)
        if not initial_data_match:
            log_event(
                "tubio", "tubio.search_scrape_failed",
                level=logging.WARNING, filter=sp,
                html_length=len(html), reason="initial_data_missing",
            )
            return {"results": [], "filtered_too_long": [], "raw_count": 0}
        try:
            data = json.loads(initial_data_match.group(1))
        except Exception as error:
            log_event(
                "tubio", "tubio.search_parse_failed",
                level=logging.WARNING, filter=sp, exc_info=error,
                error_type=type(error).__name__,
            )
            return {"results": [], "filtered_too_long": [], "raw_count": 0}
        # Traverse the JSON to get videoRenderer items
        sections = data.get('contents', {}) \
            .get('twoColumnSearchResultsRenderer', {}) \
            .get('primaryContents', {}) \
            .get('sectionListRenderer', {}) \
            .get('contents', [])

        results = []
        filtered_too_long = []
        raw_count = 0
        for section in sections:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                video = item.get('videoRenderer')
                if not video:
                    continue
                length_txt = video.get('lengthText', {}).get('simpleText', '')
                if not length_txt:
                    continue
                raw_count += 1
                vid_id = video.get('videoId')
                vid_length = AudioDownloader.get_vid_length(length_txt)
                if vid_length > ConfigManager().tubio.max_video_length:
                    filtered_too_long.append(vid_id)
                    continue
                cached = vid_id in cached_yt_vid_ids

                view_count = video.get('viewCountText', {}).get('simpleText', '')
                published = video.get('publishedTimeText', {}).get('simpleText', '')
                title = ''.join([r.get('text', '') for r in video.get('title', {}).get('runs', [])])
                description = ''
                if 'detailedMetadataSnippets' in video:
                    description = ' '.join([s.get('snippetText', {}).get('runs', [{}])[0].get('text', '') for s in video['detailedMetadataSnippets']])

                # Get thumbnail URL (prefer medium quality)
                thumbnails = video.get('thumbnail', {}).get('thumbnails', [])
                thumbnail_url = thumbnails[-1].get('url', '') if thumbnails else ''

                results.append({
                    "video_id": vid_id,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "title": title,
                    "description": description,
                    "view_count": view_count,
                    "published": published,
                    "length": length_txt,
                    "cached": cached,
                    "thumbnail_url": thumbnail_url,
                })
        return {"results": results, "filtered_too_long": filtered_too_long, "raw_count": raw_count}

    @staticmethod
    def search_youtube(query: str, cached_yt_vid_ids: Set[str], page: int = 0) -> dict:
        """
        Search YouTube for videos matching the query. Returns a dict with paginated results
        and pagination metadata: {"results": [...], "page": int, "total_pages": int, ...}.
        If query is a direct YouTube URL, returns only that video with no pagination.

        Applies ordered length-filter fallback tiers (config.search_length_filter_sps): starts
        unfiltered, then falls back to short/medium duration buckets, accumulating deduped results
        until the requested page is filled or a tier returns nothing.

        Raises:
            VideoTooLongError: If a direct URL video exceeds the maximum allowed length.
        """
        # Check if query is a direct YouTube URL
        video_id = AudioDownloader.extract_video_id(query)
        if video_id:
            log_event(
                "tubio", "tubio.direct_video_lookup_started",
                video_id=video_id,
            )
            # Let VideoTooLongError propagate for direct URLs
            video_info = AudioDownloader.get_video_info(video_id, cached_yt_vid_ids)
            results = [video_info] if video_info else []
            return {"results": results, "page": 0, "total_pages": 1}

        cfg = ConfigManager().tubio
        page_size = cfg.max_results
        max_pages = cfg.max_search_pages

        # Search is stateless (each page click re-scrapes) and YouTube's duration
        # buckets are non-deterministic, so we always run every tier and build the
        # full deduped set. total_pages is then computed from what we actually have
        # — no speculative "next page" that could vanish on the follow-up request.
        combined = []
        seen = set()
        filtered_ids = set()
        tiers = cfg.search_length_filter_sps
        for tier_idx, sp in enumerate(tiers, start=1):
            log_event(
                "tubio", "tubio.search_tier_started",
                page=page, tier=tier_idx, tiers=len(tiers), filter=sp,
            )
            scraped = AudioDownloader._scrape_search_page(query, cached_yt_vid_ids, sp=sp)
            new_this_tier = 0
            for vid in scraped["results"]:
                if vid["video_id"] in seen:
                    continue
                seen.add(vid["video_id"])
                combined.append(vid)
                new_this_tier += 1
            filtered_ids.update(scraped["filtered_too_long"])
            log_event(
                "tubio", "tubio.search_tier_completed",
                tier=tier_idx, raw=scraped["raw_count"],
                survivors=len(scraped["results"]), new=new_this_tier,
                too_long=len(scraped["filtered_too_long"]),
                combined=len(combined),
            )

        tiers_tried = len(tiers)
        total_pages = min(max_pages, max(1, (len(combined) + page_size - 1) // page_size))
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        end = start + page_size
        filtered_too_long = len(filtered_ids - seen)
        log_event(
            "tubio", "tubio.search_source_completed",
            tiers_tried=tiers_tried, total_results=len(combined),
            page=page, total_pages=total_pages,
            returned=len(combined[start:end]),
            filtered_too_long=filtered_too_long,
        )
        return {
            "results": combined[start:end],
            "page": page,
            "total_pages": total_pages,
            "filtered_too_long": filtered_too_long,
            "max_video_length_minutes": int(cfg.max_video_length.total_seconds() // 60),
        }

    @staticmethod
    def download_thumbnail(video_id: str, crc: int) -> Path | None:
        """Download and cache the video thumbnail. Returns the local path or None on failure."""
        # Use small YouTube thumbnail (320x180) - sufficient for our UI
        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
        try:
            response = requests.get(thumbnail_url, timeout=10)
            response.raise_for_status()
            DataInterface().save_thumbnail(crc, response.content)
            log_event(
                "tubio", "tubio.thumbnail_cached",
                video_id=video_id, crc=crc,
            )
            return DataInterface().get_thumbnail_path(crc)
        except Exception as error:
            log_event(
                "tubio", "tubio.thumbnail_download_failed",
                level=logging.WARNING, video_id=video_id, crc=crc,
                exc_info=error, error_type=type(error).__name__,
            )
            return None

    @staticmethod
    def _build_ydl_opts(outtmpl: str, progress_hooks: list | None = None) -> dict:
        opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '32',
            }],
            'extractaudio': True,
            'audioformat': 'm4a',
            'audioquality': 0,
        }
        if progress_hooks:
            opts['progress_hooks'] = progress_hooks
        if ConfigManager().tubio.cookie_path.exists() and not ConfigManager().debug_mode:
            log_event("tubio", "tubio.cookie_file_enabled")
            opts['cookiefile'] = str(ConfigManager().tubio.cookie_path)
        if ConfigManager().debug_mode:
            opts['nocheckcertificate'] = True
        return opts

    @staticmethod
    def _with_youtube_player_client(ydl_opts: dict, player_client: str) -> dict:
        retry_opts = {
            **ydl_opts,
            'extractor_args': {
                **ydl_opts.get('extractor_args', {}),
                'youtube': {
                    **ydl_opts.get('extractor_args', {}).get('youtube', {}),
                    'player_client': [player_client],
                },
            },
        }
        return retry_opts

    @staticmethod
    def download_audio_file(video_id: str, ydl_opts: dict) -> None:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            fallback_client = ConfigManager().tubio.youtube_403_fallback_player_client
            if "HTTP Error 403" not in str(e) or not fallback_client:
                raise
            log_event(
                "tubio", "tubio.download_retry",
                level=logging.WARNING, video_id=video_id,
                reason="http_403", player_client=fallback_client,
            )
            retry_opts = AudioDownloader._with_youtube_player_client(ydl_opts, fallback_client)
            with yt_dlp.YoutubeDL(retry_opts) as ydl:
                ydl.download([url])

    @staticmethod
    def download_youtube_audio(video_id: str, title: str, user: User, crc: int|None = None) -> None:
        log_event(
            "tubio", "tubio.audio_download_started",
            user=user, video_id=video_id, requested_crc=crc,
        )
        temp_file = DataInterface().find_avail_temp_file_path(ext=".%(ext)s")
        temp_file.parent.mkdir(parents=True, exist_ok=True)

        progress = DownloadProgress(video_id)

        def progress_hook(d):
            if d['status'] == 'downloading':
                progress.status = "downloading"
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    progress.percent = (downloaded / total) * 100
            elif d['status'] == 'finished':
                progress.status = "processing"
                progress.percent = 100

        ydl_opts = AudioDownloader._build_ydl_opts(temp_file.as_posix(), [progress_hook])

        try:
            AudioDownloader.download_audio_file(video_id, ydl_opts)
            progress.status = "complete"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
            raise
        temp_file = temp_file.with_suffix('.m4a')
        if not crc:
            with open(temp_file, 'rb') as f:
                crc = binascii.crc32(f.read())

        # Download and cache thumbnail
        AudioDownloader.download_thumbnail(video_id, crc)

        with DataInterface().edit_metadata() as metadata:
            metadata.audios[crc] = AudioMetadata(
                crc=crc, title=title, yt_video_id=video_id, is_cached=True,
                source_url=f"https://www.youtube.com/watch?v={video_id}"
            )
            metadata.get_user(user.id).add_to_playlist(crc)
        output_file = DataInterface().get_audio_path(crc)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temp_file, output_file)
        log_event(
            "tubio", "tubio.audio_download_completed",
            user=user, video_id=video_id, crc=crc,
        )

    @staticmethod
    def cache_youtube_audio(audio_metadata: AudioMetadata) -> None:
        """Materialize one uncached YouTube record without changing playlists."""
        video_id = audio_metadata.yt_video_id
        if not video_id:
            raise ValueError("Cannot cache audio without a YouTube video ID")

        log_event(
            "tubio", "tubio.lazy_download_started",
            crc=audio_metadata.crc, video_id=video_id,
        )
        temp_file = DataInterface().find_avail_temp_file_path(ext=".%(ext)s")
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        progress = DownloadProgress(video_id)

        def progress_hook(d):
            if d["status"] == "downloading":
                progress.status = "downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    progress.percent = (downloaded / total) * 100
            elif d["status"] == "finished":
                progress.status = "processing"
                progress.percent = 100

        ydl_opts = AudioDownloader._build_ydl_opts(
            temp_file.as_posix(), [progress_hook]
        )
        try:
            AudioDownloader.download_audio_file(video_id, ydl_opts)
            converted = temp_file.with_suffix(".m4a")
            AudioDownloader.download_thumbnail(video_id, audio_metadata.crc)
            output_file = DataInterface().app_audio_dir / f"{audio_metadata.crc}.m4a"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            os.replace(converted, output_file)
            with DataInterface().edit_metadata() as metadata:
                current = metadata.audios.get(audio_metadata.crc)
                if current is None:
                    raise ValueError("Audio metadata was removed while caching")
                current.is_cached = True
            progress.status = "complete"
            log_event(
                "tubio", "tubio.lazy_download_completed",
                crc=audio_metadata.crc, video_id=video_id,
                output=output_file.name,
            )
        except Exception as exc:
            progress.status = "error"
            progress.error = str(exc)
            temp_file.with_suffix(".m4a").unlink(missing_ok=True)
            log_event(
                "tubio", "tubio.lazy_download_failed",
                level=logging.ERROR, crc=audio_metadata.crc,
                video_id=video_id, exc_info=exc,
                error_type=type(exc).__name__,
            )
            raise
