import requests
from typing import List
import re
import json
import certifi
import logging
import yt_dlp
from pathlib import Path


class AudioDownloader:
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

    @staticmethod
    def search_youtube(query: str, max_results: int = 10) -> List[dict]:
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
        results = []
        sections = data.get('contents', {}) \
            .get('twoColumnSearchResultsRenderer', {}) \
            .get('primaryContents', {}) \
            .get('sectionListRenderer', {}) \
            .get('contents', [])
        
        
        for section in sections:
            items = section.get('itemSectionRenderer', {}).get('contents', [])
            for item in items:
                video = item.get('videoRenderer')
                if not video:
                    continue
                vid = video.get('videoId')
                title = ''.join([r.get('text', '') for r in video.get('title', {}).get('runs', [])])
                description = ''
                if 'detailedMetadataSnippets' in video:
                    description = ' '.join([s.get('snippetText', {}).get('runs', [{}])[0].get('text', '') for s in video['detailedMetadataSnippets']])
                view_count = video.get('viewCountText', {}).get('simpleText', '')
                published = video.get('publishedTimeText', {}).get('simpleText', '')
                length = video.get('lengthText', {}).get('simpleText', '')
                results.append({
                    "video_id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "description": description,
                    "view_count": view_count,
                    "published": published,
                    "length": length
                })
                if len(results) >= max_results:
                    return results
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
