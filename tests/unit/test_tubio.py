import pytest

from unittest.mock import Mock, patch, MagicMock
from datetime import timedelta
from yt_dlp.utils import DownloadError

from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError, DownloadProgress, get_download_progress, clear_download_progress
from web_app.tubio.data_interface import UserMetadata
from web_app.config import ConfigManager
from web_app.users import User
import web_app.helpers as helpers


@pytest.fixture(scope='module', autouse=True)
def setup_app():
    """Register blueprints so the /tubio/* routes exist for the client fixture."""
    from web_app.app import app
    from web_app.helpers import limiter, register_all_blueprints
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.secret_key = 'test-secret-key'
    limiter.enabled = False
    if 'tubio' not in app.blueprints:
        register_all_blueprints(app)


@pytest.fixture
def auth_mock():
    user = User(username='tubio-user', password='testpass', folder='test_folder', is_admin=False)
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: user if username == user.id else None
    yield user
    helpers.login_manager._user_callback = original_user_loader


class TestExtractVideoId:
    """Tests for YouTube URL detection and video ID extraction."""

    def test_standard_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_without_www(self):
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_http(self):
        url = "http://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_watch_url_no_protocol(self):
        url = "youtube.com/watch?v=dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_short_url_no_protocol(self):
        url = "youtu.be/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_embed_url(self):
        url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_extra_params(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PLtest"
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_url_with_whitespace(self):
        url = "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  "
        assert AudioDownloader.extract_video_id(url) == "dQw4w9WgXcQ"

    def test_not_a_url_returns_none(self):
        query = "rick astley never gonna give you up"
        assert AudioDownloader.extract_video_id(query) is None

    def test_empty_string_returns_none(self):
        assert AudioDownloader.extract_video_id("") is None

    def test_invalid_video_id_length(self):
        # Video IDs must be exactly 11 characters
        url = "https://www.youtube.com/watch?v=short"
        assert AudioDownloader.extract_video_id(url) is None

    def test_other_website_returns_none(self):
        url = "https://vimeo.com/123456789"
        assert AudioDownloader.extract_video_id(url) is None


class TestGetVideoInfo:
    """Tests for fetching video info from YouTube."""

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_success(self, mock_config, mock_ydl_class):
        mock_config.return_value.tubio.max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,  # 3 minutes
            'view_count': 1000000,
            'upload_date': '20240101',
            'description': 'Test description',
            'thumbnail': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert result is not None
        assert result['video_id'] == 'dQw4w9WgXcQ'
        assert result['title'] == 'Test Video'
        assert result['length'] == '3:00'
        assert result['cached'] is False
        assert result['thumbnail_url'] == 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_cached(self, mock_config, mock_ydl_class):
        mock_config.return_value.tubio.max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,
            'view_count': 1000000,
            'upload_date': '20240101',
            'description': 'Test description',
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', {'dQw4w9WgXcQ'})

        assert result is not None
        assert result['cached'] is True

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_too_long_raises_exception(self, mock_config, mock_ydl_class):
        mock_config.return_value.tubio.max_video_length = timedelta(minutes=10)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Long Video',
            'duration': 3600,  # 1 hour - exceeds max
        }
        mock_ydl_class.return_value = mock_ydl

        with pytest.raises(VideoTooLongError) as exc_info:
            AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert exc_info.value.video_id == 'dQw4w9WgXcQ'
        assert exc_info.value.duration == timedelta(hours=1)
        assert exc_info.value.max_duration == timedelta(minutes=10)

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    def test_get_video_info_error(self, mock_ydl_class):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception("Video not found")
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('invalidid12', set())

        assert result is None

    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    def test_get_video_info_thumbnail_from_thumbnails_array(self, mock_config, mock_ydl_class):
        """Test that thumbnail is extracted from thumbnails array if main thumbnail is missing."""
        mock_config.return_value.tubio.max_video_length = timedelta(minutes=30)

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Test Video',
            'duration': 180,
            'thumbnails': [
                {'url': 'https://example.com/small.jpg'},
                {'url': 'https://example.com/medium.jpg'},
                {'url': 'https://example.com/large.jpg'},
            ],
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info('dQw4w9WgXcQ', set())

        assert result is not None
        # Should use the last (highest quality) thumbnail
        assert result['thumbnail_url'] == 'https://example.com/large.jpg'


class TestDownloadAudioFile:
    @patch('web_app.tubio.audio_downloader.ConfigManager')
    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    def test_retries_youtube_403_with_fallback_player_client(self, mock_ydl_class, mock_config):
        mock_config.return_value.tubio.youtube_403_fallback_player_client = "web"

        first_ydl = MagicMock()
        first_ydl.__enter__ = Mock(return_value=first_ydl)
        first_ydl.__exit__ = Mock(return_value=False)
        first_ydl.download.side_effect = DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

        retry_ydl = MagicMock()
        retry_ydl.__enter__ = Mock(return_value=retry_ydl)
        retry_ydl.__exit__ = Mock(return_value=False)

        mock_ydl_class.side_effect = [first_ydl, retry_ydl]
        ydl_opts = {"format": "bestaudio"}

        AudioDownloader.download_audio_file("dQw4w9WgXcQ", ydl_opts)

        retry_opts = mock_ydl_class.call_args_list[1].args[0]
        assert retry_opts["extractor_args"]["youtube"]["player_client"] == ["web"]
        retry_ydl.download.assert_called_once_with(["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
        assert "extractor_args" not in ydl_opts


class TestDownloadThumbnail:
    """Tests for thumbnail download and caching."""

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_success(self, mock_get, mock_di):
        from pathlib import Path
        mock_response = Mock()
        mock_response.content = b'fake_image_data'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_di_instance = Mock()
        mock_thumbnail_path = Path('/fake/path/12345.jpg')
        mock_di_instance.get_thumbnail_path.return_value = mock_thumbnail_path
        mock_di.return_value = mock_di_instance

        result = AudioDownloader.download_thumbnail('dQw4w9WgXcQ', 12345)

        assert result == mock_thumbnail_path
        mock_di_instance.save_thumbnail.assert_called_once_with(12345, b'fake_image_data')

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_failure(self, mock_get, mock_di):
        mock_get.side_effect = Exception("Network error")

        result = AudioDownloader.download_thumbnail('dQw4w9WgXcQ', 12345)

        assert result is None

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_download_thumbnail_http_error(self, mock_get, mock_di):
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        mock_get.return_value = mock_response

        result = AudioDownloader.download_thumbnail('invalidid', 12345)

        assert result is None


class TestSearchYoutubeWithDirectUrl:
    """Tests for search_youtube handling direct URLs."""

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_returns_single_result(self, mock_get_info):
        mock_get_info.return_value = {
            'video_id': 'dQw4w9WgXcQ',
            'title': 'Test Video',
            'length': '3:00',
            'cached': False,
            'thumbnail_url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
        }

        search_data = AudioDownloader.search_youtube(
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            set()
        )

        results = search_data['results']
        assert len(results) == 1
        assert results[0]['video_id'] == 'dQw4w9WgXcQ'
        assert results[0]['thumbnail_url'] == 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'
        assert search_data['page'] == 0
        assert search_data['total_pages'] == 1
        mock_get_info.assert_called_once_with('dQw4w9WgXcQ', set())

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_video_not_found(self, mock_get_info):
        mock_get_info.return_value = None

        search_data = AudioDownloader.search_youtube(
            'https://www.youtube.com/watch?v=invalidid12',
            set()
        )

        assert search_data == {'results': [], 'page': 0, 'total_pages': 1}

    @patch('web_app.tubio.audio_downloader.requests.get')
    def test_search_with_regular_query_does_normal_search(self, mock_get):
        mock_response = Mock()
        mock_response.text = 'var ytInitialData = {};'
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = AudioDownloader.search_youtube('rick astley', set())

        tiers = ConfigManager().tubio.search_length_filter_sps
        assert mock_get.call_count == len(tiers)
        assert [
            request.kwargs["params"]
            for request in mock_get.call_args_list
        ] == [
            {"search_query": "rick astley", **({"sp": tier} if tier else {})}
            for tier in tiers
        ]

    @patch.object(AudioDownloader, 'get_video_info')
    def test_search_with_direct_url_raises_video_too_long_error(self, mock_get_info):
        """Test that VideoTooLongError propagates when direct URL video is too long."""
        mock_get_info.side_effect = VideoTooLongError(
            'dQw4w9WgXcQ',
            timedelta(hours=2),
            timedelta(minutes=30)
        )

        with pytest.raises(VideoTooLongError) as exc_info:
            AudioDownloader.search_youtube(
                'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                set()
            )

        assert exc_info.value.video_id == 'dQw4w9WgXcQ'
        assert 'too long' in str(exc_info.value)


class TestDownloadProgress:
    def test_progress_tracking(self):
        clear_download_progress('test123')

        # Construction write-throughs to Redis; a fresh read (as a separate
        # gunicorn worker's SSE stream would do) sees the initial state.
        DownloadProgress('test123')
        progress = get_download_progress('test123')
        assert progress is not None
        assert progress.percent == 0
        assert progress.status == "starting"

        # Mutations on the writer object propagate to independent readers.
        writer = DownloadProgress('test123')
        writer.percent = 50
        writer.status = "downloading"
        reader = get_download_progress('test123')
        assert reader.percent == 50
        assert reader.status == "downloading"

        clear_download_progress('test123')
        assert get_download_progress('test123') is None


class TestTrimAudio:
    def test_playback_trim_is_user_specific_and_zero_resets_it(self):
        user_metadata = UserMetadata(user_id='listener')

        user_metadata.set_playback_trim(123, 1.5, 2)
        assert user_metadata.get_playback_trim(123).model_dump() == {
            'start_s': 1.5,
            'end_s': 2,
        }

        user_metadata.set_playback_trim(123, 0, 0)
        assert 123 not in user_metadata.playback_trims

    @patch('web_app.tubio.DataInterface')
    def test_updates_playback_boundaries_without_writing_audio(
        self, mock_di_class, client, auth_mock
    ):
        di = mock_di_class.return_value
        metadata = MagicMock()
        audio_metadata = Mock(title='Original')
        metadata.audios = {123: audio_metadata}
        user_metadata = metadata.get_user.return_value
        user_metadata.playlists = {
            'Favourites': Mock(audio_crcs=[123]),
        }
        di.edit_metadata.return_value.__enter__.return_value = metadata
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        response = client.post('/tubio/audio/123/trim', data={
            'trim_start_s': '1.5',
            'trim_end_s': '2',
        }, headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

        assert response.status_code == 200
        assert response.get_json()['trim_start_s'] == 1.5
        user_metadata.set_playback_trim.assert_called_once_with(123, 1.5, 2)
        di.save_audio.assert_not_called()

    @patch('web_app.tubio.DataInterface')
    def test_rejects_negative_playback_boundary(
        self, mock_di_class, client, auth_mock
    ):
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        response = client.post('/tubio/audio/123/trim', data={
            'trim_start_s': '-1',
            'trim_end_s': '0',
        }, headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

        assert response.status_code == 400
        assert 'negative' in response.get_json()['error']
        mock_di_class.return_value.edit_metadata.assert_not_called()


class TestSurprisePlaylist:
    """Tests for the Surprise-Playlist temp-track pipeline."""

    @patch('web_app.tubio.audio_downloader.DataInterface')
    @patch.object(AudioDownloader, 'download_audio_file')
    def test_download_temp_track_writes_temp_and_skips_metadata(
        self, mock_download, mock_di_class, tmp_path
    ):
        from pathlib import Path
        temp_src = tmp_path / "scratch.%(ext)s"
        (tmp_path / "scratch.m4a").write_bytes(b'audio')
        out = tmp_path / "temp_tracks" / "abc1234567.m4a"

        di = mock_di_class.return_value
        di.find_avail_temp_file_path.return_value = temp_src
        di.get_temp_track_path.return_value = out

        AudioDownloader.download_temp_track('dQw4w9WgXcQ', 'abc1234567')

        assert out.exists()
        mock_download.assert_called_once()
        # A temp track must never touch the library metadata or thumbnails.
        di.edit_metadata.assert_not_called()
        di.save_thumbnail.assert_not_called()

    def test_sweep_temp_tracks_removes_aged_keeps_fresh(self, tmp_path):
        import os, time
        from web_app.tubio.data_interface import DataInterface
        with patch('web_app.tubio.data_interface.ConfigManager') as mock_cfg:
            mock_cfg.return_value.save_data_path = tmp_path
            mock_cfg.return_value.temp_dir = tmp_path / 'temp'
            mock_cfg.return_value.tubio.surprise_temp_dirname = 'temp_tracks'
            mock_cfg.return_value.tubio.surprise_temp_ttl_s = 3600
            di = DataInterface()
            di.app_temp_tracks_dir.mkdir(parents=True, exist_ok=True)

            fresh = di.get_temp_track_path('fresh12345')
            aged = di.get_temp_track_path('aged123456')
            fresh.write_bytes(b'a')
            aged.write_bytes(b'b')
            old = time.time() - 7200
            os.utime(aged, (old, old))

            di.sweep_temp_tracks()

            assert fresh.exists()
            assert not aged.exists()

    @patch('web_app.tubio.DataInterface')
    @patch('web_app.tubio.get_cached_yt_vid_ids')
    @patch('web_app.tubio.AudioDownloader.get_mix_related')
    def test_surprise_next_excludes_owned_seen_and_too_long(
        self, mock_mix, mock_owned, mock_di_class, client, auth_mock
    ):
        mock_owned.return_value = {'seed000000a', 'owned00000b'}
        mock_mix.return_value = [
            {'video_id': 'owned00000b', 'title': 'owned', 'duration_s': 100},
            {'video_id': 'excluded00c', 'title': 'excluded', 'duration_s': 100},
            {'video_id': 'toolong000d', 'title': 'too long', 'duration_s': 99999},
            {'video_id': 'goodpick00e', 'title': 'good', 'duration_s': 120,
             'thumbnail_url': 't', 'length': '2:00'},
        ]
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        response = client.post('/tubio/surprise/next', data={
            'seed': 'seed000000a', 'exclude': 'excluded00c',
        }, headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

        assert response.status_code == 200
        assert response.get_json()['track']['video_id'] == 'goodpick00e'

    @patch('web_app.tubio.DataInterface')
    @patch('web_app.tubio.get_cached_yt_vid_ids')
    def test_surprise_next_empty_library(
        self, mock_owned, mock_di_class, client, auth_mock
    ):
        mock_owned.return_value = set()
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id
        response = client.post('/tubio/surprise/next', data={},
            headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})
        assert response.get_json()['empty_reason'] == 'no_library'

    @patch('web_app.tubio.DataInterface')
    def test_surprise_audio_rejects_bad_token(
        self, mock_di_class, client, auth_mock
    ):
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id
        # Path-traversal / wrong-shape tokens must never reach the filesystem.
        response = client.get('/tubio/surprise/audio/..%2f..%2fetc')
        assert response.status_code == 404
        mock_di_class.return_value.get_temp_track_path.assert_not_called()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
