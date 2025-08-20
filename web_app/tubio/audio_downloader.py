import requests
import re
import json
import logging
import yt_dlp

from typing import List
from pathlib import Path
from datetime import datetime, timedelta

from web_app.config import ConfigManager


class AudioDownloader:
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

    @staticmethod
    def get_vid_length(text: str) -> timedelta:
        parts = reversed(text.split(':'))
        sec_map = [ 1, 60, 3600 ]  # seconds, minutes, hours
        total_seconds = sum(int(part) * sec for part, sec in zip(parts, sec_map))

        return timedelta(seconds=total_seconds)

    @staticmethod
    def search_youtube(query: str) -> List[dict]:
        """
        Search YouTube for videos matching the query and return a list of video info dicts.
        Extracts video_id, title, description, view count, date, and length.
        """
        params = {"search_query": query}
        response = requests.get(AudioDownloader.YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        html = response.text
        # Extract ytInitialData JSON
        initial_data_match = re.search(r'var ytInitialData = (\{.*?\});', html, re.DOTALL)
        if not initial_data_match:
            return []
        try:
            data = json.loads(initial_data_match.group(1))
        except Exception:
            return []
        # Traverse the JSON to get videoRenderer items
        sections = data.get('contents', {}) \
            .get('twoColumnSearchResultsRenderer', {}) \
            .get('primaryContents', {}) \
            .get('sectionListRenderer', {}) \
            .get('contents', [])
        
        logging.info(f"Searching YouTube with query: {query}")
        results = []
        for section in sections:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                if len(results) > ConfigManager().tudio_max_results:
                    return results
                video = item.get('videoRenderer')
                if not video:
                    continue
                length_txt = video.get('lengthText', {}).get('simpleText', '')
                if not length_txt:
                    continue
                vid_length = AudioDownloader.get_vid_length(length_txt)
                if vid_length > ConfigManager().tudio_max_video_length:
                    continue

                view_count = video.get('viewCountText', {}).get('simpleText', '')
                published = video.get('publishedTimeText', {}).get('simpleText', '')
                vid_id = video.get('videoId')
                title = ''.join([r.get('text', '') for r in video.get('title', {}).get('runs', [])])
                description = ''
                if 'detailedMetadataSnippets' in video:
                    description = ' '.join([s.get('snippetText', {}).get('runs', [{}])[0].get('text', '') for s in video['detailedMetadataSnippets']])
                results.append({
                    "video_id": vid_id,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "title": title,
                    "description": description,
                    "view_count": view_count,
                    "published": published,
                    "length": length_txt
                })
        return results

    @staticmethod
    def download_youtube_audio(video_id: str, title: str, user_dir: Path) -> str:
        """
        Download audio from YouTube using yt-dlp, save to user_dir, return the saved filename or raise Exception.
        """
        logging.info(f"Downloading YouTube audio: {title} ({video_id})")
        url = f'https://www.youtube.com/watch?v={video_id}'
        user_dir.mkdir(parents=True, exist_ok=True)
        safe_title = ''.join(c for c in title if c.isalnum() or c in (' ', '_', '-')).rstrip()
        output_path = f"{user_dir}/{video_id}.%(ext)s"
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': output_path,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '32',  # lowest quality for cost
            }],
            'extractaudio': True,
            'audioformat': 'm4a',
            'audioquality': 0,  # best effort for lowest
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # Return the expected filename (with .m4a extension)
        return (user_dir / f"{safe_title or video_id}.m4a").name
