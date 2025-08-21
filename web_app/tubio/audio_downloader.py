import requests
import re
import json
import logging
import yt_dlp
import tempfile
import os
import binascii

from typing import *
from pathlib import Path
from datetime import datetime, timedelta

from web_app.config import ConfigManager
from web_app.tubio.data_interface import Metadata, UserMetadata, AudioMetadata, DataInterface
from web_app.users import User


class AudioDownloader:
    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"

    @staticmethod
    def get_vid_length(text: str) -> timedelta:
        parts = reversed(text.split(':'))
        sec_map = [ 1, 60, 3600 ]  # seconds, minutes, hours
        total_seconds = sum(int(part) * sec for part, sec in zip(parts, sec_map))

        return timedelta(seconds=total_seconds)

    @staticmethod
    def search_youtube(query: str, cached_yt_vid_ids: Set[str]) -> List[dict]:
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
        except Exception as e:
            logging.error(f"Failed to parse YouTube search results: {e}")
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
                vid_id = video.get('videoId')
                cached = vid_id in cached_yt_vid_ids

                view_count = video.get('viewCountText', {}).get('simpleText', '')
                published = video.get('publishedTimeText', {}).get('simpleText', '')
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
                    "length": length_txt,
                    "cached": cached,
                })
        return results

    @staticmethod
    def download_youtube_audio(video_id: str, title: str, user: User) -> None:
        logging.info(f"Tubio downloading video_id:={video_id}")
        url = f"https://www.youtube.com/watch?v={video_id}"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            temp_out_name = tmp.name
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': f"{temp_out_name}.%(ext)s",
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

        if ConfigManager().tubio_cookie_path.exists():
            logging.info(f"Using cookie file: {ConfigManager().tubio_cookie_path}")
            ydl_opts['cookiefile'] = str(ConfigManager().tubio_cookie_path)
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        temp_out_name = f"{temp_out_name}.m4a"
        with open(temp_out_name, 'rb') as f:
            crc = binascii.crc32(f.read())
        metadata = DataInterface().get_metadata()
        metadata.audios[crc] = AudioMetadata(crc=crc, title=title, yt_video_id=video_id)
        if user.id not in metadata.users:
            metadata.users[user.id] = UserMetadata()
        metadata.users[user.id].favourites.append(crc)  
        DataInterface().save_metadata(metadata)
        output_file = DataInterface().get_audio_path(crc)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temp_out_name, output_file)