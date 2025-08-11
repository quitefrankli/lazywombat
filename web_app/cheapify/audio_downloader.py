import requests
from typing import List
import re
import json

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
