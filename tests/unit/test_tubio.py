import pytest
from nabicat_app_sdk import DataRoot

from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from yt_dlp.utils import DownloadError

from web_app.tubio.audio_downloader import AudioDownloader, VideoTooLongError, DownloadProgress, get_download_progress, clear_download_progress
from web_app.tubio.data_interface import (
    AudioMetadata,
    DataInterface,
    Playlist,
    UserMetadata,
)
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


@pytest.fixture
def tubio_data(tmp_path):
    data = DataInterface()
    data.data = DataRoot(root=tmp_path)
    data.app_dir = tmp_path / "tubio"
    data.app_audio_dir = data.app_dir / "audio"
    data.app_thumbnails_dir = data.app_dir / "thumbnails"
    data.app_metadata_file = data.app_dir / "metadata.json"
    return data


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
    def test_get_video_info_accepts_direct_url_duration_limit_override(self, mock_ydl_class):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = Mock(return_value=mock_ydl)
        mock_ydl.__exit__ = Mock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'title': 'Long Mix',
            'duration': 59 * 60,
        }
        mock_ydl_class.return_value = mock_ydl

        result = AudioDownloader.get_video_info(
            'dQw4w9WgXcQ',
            set(),
            max_duration=timedelta(hours=1),
        )

        assert result is not None
        assert result['length'] == '59:00'

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
    @patch('web_app.tubio.audio_downloader.yt_dlp.YoutubeDL')
    def test_retries_youtube_403_with_fallback_player_client(self, mock_ydl_class):
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

    @patch.object(AudioDownloader, 'get_video_info', return_value={'video_id': 'dQw4w9WgXcQ'})
    def test_direct_url_uses_direct_video_duration_limit(self, mock_get_video_info):
        result = AudioDownloader.search_youtube(
            'https://youtu.be/dQw4w9WgXcQ',
            set(),
        )

        assert result['results'] == [{'video_id': 'dQw4w9WgXcQ'}]
        mock_get_video_info.assert_called_once_with(
            'dQw4w9WgXcQ',
            set(),
            max_duration=timedelta(hours=1),
        )

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
        mock_get_info.assert_called_once_with(
            'dQw4w9WgXcQ',
            set(),
            max_duration=ConfigManager().tubio.direct_video_max_length,
        )

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
    def test_missing_progress_returns_event_stream_payload(self, client, auth_mock):
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        response = client.get('/tubio/download_progress/missing-video')

        assert response.status_code == 200
        assert response.mimetype == 'text/event-stream'
        assert response.get_data(as_text=True) == 'data: {"status": "not_found"}\n\n'

    @patch('web_app.tubio.routes.downloads.get_playlists_data', return_value=[])
    @patch('web_app.tubio.routes.downloads.get_cached_yt_vid_ids', return_value=set())
    @patch('web_app.tubio.routes.downloads.AudioDownloader.download_youtube_audio')
    def test_youtube_download_resolves_playlist_dependencies(
        self, mock_download, _mock_cached_ids, _mock_playlists, client, auth_mock
    ):
        mock_download.return_value = Mock(crc=123)
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        response = client.post(
            '/tubio/youtube_download',
            data={'video_id': 'dQw4w9WgXcQ', 'title': 'Test track'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload['success'] is True
        assert 'library_html' in payload
        assert 'playlists' not in payload

    def test_progress_tracking(self):
        clear_download_progress('test123')

        # A fresh read (as a separate gunicorn worker's SSE stream would do)
        # sees explicitly persisted state.
        writer = DownloadProgress.start('test123')
        progress = get_download_progress('test123')
        assert progress is not None
        assert progress.percent == 0
        assert progress.status == "starting"

        writer.update(percent=50, status="downloading")
        reader = get_download_progress('test123')
        assert reader.percent == 50
        assert reader.status == "downloading"

        clear_download_progress('test123')
        assert get_download_progress('test123') is None

    def test_completed_download_returns_persisted_audio(
        self, auth_mock, tubio_data, tmp_path
    ):
        video_id = 'dQw4w9WgXcQ'
        temp_template = tmp_path / 'download.%(ext)s'
        tubio_data.find_avail_temp_file_path = Mock(return_value=temp_template)

        def write_download(_video_id, options):
            Path(options['outtmpl'].replace('%(ext)s', 'm4a')).write_bytes(
                b'converted-audio'
            )

        clear_download_progress(video_id)
        with (
            patch(
                'web_app.tubio.audio_downloader.DataInterface',
                return_value=tubio_data,
            ),
            patch.object(
                AudioDownloader,
                'download_audio_file',
                side_effect=write_download,
            ),
            patch.object(AudioDownloader, 'download_thumbnail', return_value=None),
        ):
            audio = AudioDownloader.download_youtube_audio(
                video_id,
                'Completed track',
                auth_mock,
            )

        persisted = tubio_data.get_metadata()
        progress = get_download_progress(video_id)
        assert audio == persisted.audios[audio.crc]
        assert persisted.users[auth_mock.id].get_playlist().audio_crcs == [audio.crc]
        assert tubio_data.get_audio_path(audio.crc).read_bytes() == b'converted-audio'
        assert progress.status == 'complete'


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

    def test_updates_playback_boundaries_without_writing_audio(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[123] = AudioMetadata(crc=123, title='Original')
            metadata.get_user(auth_mock.id).get_playlist().audio_crcs = [123]
        tubio_data.app_audio_dir.mkdir(parents=True)
        audio_path = tubio_data.app_audio_dir / '123.m4a'
        audio_path.write_bytes(b'original-audio')
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with patch(
            'web_app.tubio.routes.media.DataInterface',
            return_value=tubio_data,
        ):
            response = client.post('/tubio/audio/123/trim', data={
                'trim_start_s': '1.5',
                'trim_end_s': '2',
            }, headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

        assert response.status_code == 200
        assert response.get_json()['trim_start_s'] == 1.5
        assert tubio_data.get_user_metadata(auth_mock).get_playback_trim(123).model_dump() == {
            'start_s': 1.5,
            'end_s': 2,
        }
        assert audio_path.read_bytes() == b'original-audio'

    @patch('web_app.tubio.routes.media.DataInterface')
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


class TestPlaylistMutations:
    def test_deleting_regular_playlist_moves_its_tracks_to_favourites(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            user_metadata = metadata.get_user(auth_mock.id)
            user_metadata.get_playlist("Road Trip").audio_crcs = [101, 202]

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with patch('web_app.tubio.routes.playlists.DataInterface', return_value=tubio_data):
            response = client.post(
                '/tubio/delete_playlist',
                data={'playlist_name': 'Road Trip'},
            )

        user_metadata = tubio_data.get_user_metadata(auth_mock)
        assert response.status_code == 302
        assert "Road Trip" not in user_metadata.playlists
        assert user_metadata.get_playlist("Favourites").audio_crcs == [101, 202]

    def test_removing_a_custom_playlist_track_deletes_unreferenced_media(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[101] = AudioMetadata(crc=101, title="Road Song")
            user_metadata = metadata.get_user(auth_mock.id)
            user_metadata.get_playlist("Favourites").audio_crcs = [101]
            user_metadata.get_playlist("Road Trip").audio_crcs = [101, 101]
            user_metadata.set_playback_trim(101, 1.5, 2)

        tubio_data.app_audio_dir.mkdir(parents=True)
        (tubio_data.app_audio_dir / "101.m4a").write_bytes(b"audio")
        tubio_data.app_thumbnails_dir.mkdir(parents=True)
        (tubio_data.app_thumbnails_dir / "101.jpg").write_bytes(b"thumbnail")

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with patch('web_app.tubio.routes.media.DataInterface', return_value=tubio_data):
            response = client.post(
                '/tubio/delete_audio/101',
                headers={
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
            )

        payload = response.get_json()
        metadata = tubio_data.get_metadata()
        user_metadata = metadata.users[auth_mock.id]
        assert response.status_code == 200
        assert payload['success'] is True
        assert 'library_html' in payload
        assert all(
            101 not in playlist.audio_crcs
            for playlist in user_metadata.get_playlists()
        )
        assert 101 not in user_metadata.playback_trims
        assert 101 not in metadata.audios
        assert not (tubio_data.app_audio_dir / "101.m4a").exists()
        assert not (tubio_data.app_thumbnails_dir / "101.jpg").exists()

    def test_removing_a_track_preserves_media_referenced_by_another_user(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[101] = AudioMetadata(crc=101, title="Shared Song")
            metadata.get_user(auth_mock.id).get_playlist(
                "Road Trip"
            ).audio_crcs = [101]
            metadata.get_user("another-listener").get_playlist().audio_crcs = [101]
        tubio_data.app_audio_dir.mkdir(parents=True)
        audio_path = tubio_data.app_audio_dir / "101.m4a"
        audio_path.write_bytes(b"shared-audio")

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with (
            patch(
                'web_app.tubio.routes.media.DataInterface',
                return_value=tubio_data,
            ),
            patch.object(
                tubio_data,
                'cleanup_unused_resources',
                wraps=tubio_data.cleanup_unused_resources,
            ) as cleanup,
        ):
            response = client.post(
                '/tubio/delete_audio/101',
                headers={'Accept': 'application/json'},
            )

        metadata = tubio_data.get_metadata()
        assert response.status_code == 200
        assert metadata.users[auth_mock.id].playlists["Road Trip"].audio_crcs == []
        assert metadata.users["another-listener"].get_playlist().audio_crcs == [101]
        assert metadata.audios[101].title == "Shared Song"
        assert audio_path.read_bytes() == b"shared-audio"
        cleanup.assert_not_called()

    def test_moving_tracks_between_regular_playlists_preserves_surprise(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[101] = AudioMetadata(crc=101, title="Road Song")
            user_metadata = metadata.get_user(auth_mock.id)
            user_metadata.get_playlist("Favourites").audio_crcs = [101]
            user_metadata.get_playlist("Road Trip")
            user_metadata.set_surprise_playlist(Playlist(
                name="Surprise Playlist",
                audio_crcs=[101],
                last_active=datetime.now(timezone.utc),
            ))

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with patch('web_app.tubio.routes.playlists.DataInterface', return_value=tubio_data):
            response = client.post(
                '/tubio/move_tracks_to_playlist',
                data={
                    'target_playlist': 'Road Trip',
                    'song_crcs': '101',
                },
            )

        user_metadata = tubio_data.get_user_metadata(auth_mock)
        assert response.status_code == 302
        assert user_metadata.playlists["Favourites"].audio_crcs == []
        assert user_metadata.playlists["Road Trip"].audio_crcs == [101]
        assert user_metadata.get_surprise_playlist().audio_crcs == [101]

    def test_moving_tracks_rejects_audio_outside_the_users_library(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[101] = AudioMetadata(crc=101, title="Someone Else's Song")
            user_metadata = metadata.get_user(auth_mock.id)
            user_metadata.get_playlist("Favourites")
            user_metadata.get_playlist("Road Trip")

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        with patch('web_app.tubio.routes.playlists.DataInterface', return_value=tubio_data):
            response = client.post(
                '/tubio/move_tracks_to_playlist',
                data={
                    'target_playlist': 'Road Trip',
                    'song_crcs': '101',
                },
            )

        user_metadata = tubio_data.get_user_metadata(auth_mock)
        assert response.status_code == 302
        assert user_metadata.playlists["Road Trip"].audio_crcs == []

    def test_removed_bulk_delete_route_is_not_registered(self, app):
        tubio_rules = {
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("tubio.")
        }

        assert "/tubio/delete_selected_songs" not in tubio_rules


class TestSearchRoutes:
    def test_search_marks_tracks_cached_from_any_regular_playlist(
        self, client, auth_mock, tubio_data
    ):
        with tubio_data.edit_metadata() as metadata:
            metadata.audios[101] = AudioMetadata(
                crc=101,
                title="Road Song",
                yt_video_id="abcdefghijk",
            )
            metadata.get_user(auth_mock.id).get_playlist(
                "Road Trip"
            ).audio_crcs = [101]

        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        search_result = {
            'results': [],
            'page': 0,
            'total_pages': 1,
            'filtered_too_long': 0,
            'max_video_length_minutes': 30,
        }
        with (
            patch('web_app.tubio.routes.playlists.DataInterface', return_value=tubio_data),
            patch(
                'web_app.tubio.routes.search.AudioDownloader.search_youtube',
                return_value=search_result,
            ) as search_youtube,
        ):
            response = client.post(
                '/tubio/search',
                data={'youtube_query': 'road song'},
                headers={'Accept': 'application/json'},
            )

        assert response.status_code == 200
        assert search_youtube.call_args.args[1] == {'abcdefghijk'}

    def test_search_returns_server_rendered_delegated_actions(
        self, client, auth_mock, tubio_data
    ):
        with client.session_transaction() as session:
            session['_user_id'] = auth_mock.id

        search_result = {
            'results': [{
                'video_id': 'abcdefghijk',
                'title': "A Road Song",
                'description': "A description",
                'view_count': '123',
                'published': '2024',
                'length': '3:20',
                'thumbnail_url': 'https://example.test/thumbnail.jpg',
                'cached': False,
            }],
            'page': 0,
            'total_pages': 2,
            'filtered_too_long': 0,
            'max_video_length_minutes': 30,
        }
        with (
            patch('web_app.tubio.routes.playlists.DataInterface', return_value=tubio_data),
            patch(
                'web_app.tubio.routes.search.AudioDownloader.search_youtube',
                return_value=search_result,
            ),
        ):
            response = client.post(
                '/tubio/search',
                data={'youtube_query': 'road song'},
                headers={'Accept': 'application/json'},
            )

        html = response.get_json()['results_html']
        assert 'A Road Song' in html
        assert 'data-tubio-action="download-video"' in html
        assert 'data-tubio-action="search-page"' in html
        assert 'onclick=' not in html
        assert 'style=' not in html


class TestAudioRangeResponse:
    def test_clamps_open_range_to_end_of_file(self, app, tmp_path):
        from web_app.tubio.routes.media import _range_response

        audio_path = tmp_path / "track.m4a"
        audio_path.write_bytes(b"abcdef")

        with app.test_request_context(
            "/tubio/audio/123", headers={"Range": "bytes=4-99"}
        ):
            response = _range_response(audio_path, "123", "track.m4a")
            response.direct_passthrough = False

        assert response.status_code == 206
        assert response.get_data() == b"ef"
        assert response.headers["Content-Range"] == "bytes 4-5/6"
        assert response.headers["Content-Length"] == "2"
        assert response.headers["Accept-Ranges"] == "bytes"

    def test_rejects_an_unsatisfiable_range_with_file_size(self, app, tmp_path):
        from werkzeug.exceptions import RequestedRangeNotSatisfiable
        from web_app.tubio.routes.media import _range_response

        audio_path = tmp_path / "track.m4a"
        audio_path.write_bytes(b"abcdef")

        with app.test_request_context(
            "/tubio/audio/123", headers={"Range": "bytes=99-"}
        ):
            with pytest.raises(RequestedRangeNotSatisfiable) as exc_info:
                _range_response(audio_path, "123", "track.m4a")

        response = exc_info.value.get_response()
        assert response.status_code == 416
        assert response.headers["Content-Range"] == "bytes */6"

    def test_full_response_advertises_byte_ranges(self, app, tmp_path):
        from web_app.tubio.routes.media import _range_response

        audio_path = tmp_path / "track.m4a"
        audio_path.write_bytes(b"abcdef")

        with app.test_request_context("/tubio/audio/123"):
            response = _range_response(audio_path, "123", "track.m4a")

        assert response.status_code == 200
        assert response.headers["Accept-Ranges"] == "bytes"
        response.close()


def test_duplicate_playlist_entries_have_unique_dom_identity(app):
    from web_app.tubio.routes.playlists import add_track_occurrences

    track = {
        "crc": 123,
        "title": "Repeated track",
        "thumbnail_url": "",
        "source_url": "",
        "video_id": "",
        "trim_start_s": 0,
        "trim_end_s": 0,
        "is_cached": True,
        "is_favourite": True,
    }
    inserted_track = {
        **track,
        "crc": 456,
        "title": "Inserted track",
    }
    tracks = add_track_occurrences([track.copy(), track.copy()])
    rerendered_tracks = add_track_occurrences(
        [inserted_track, track.copy(), track.copy()]
    )

    with app.test_request_context("/tubio/"):
        template = app.jinja_env.get_template("playlist_components.html")
        html = str(
            template.module.playlist_panel("Duplicates", tracks)
        )
        rerendered_html = str(
            template.module.playlist_panel(
                "Duplicates", rerendered_tracks
            )
        )

    soup = BeautifulSoup(html, "html.parser")
    entries = soup.select(".playlist-track[data-track-key]")
    ids = [element["id"] for element in soup.select("[id]")]
    rerendered = BeautifulSoup(rerendered_html, "html.parser")
    rerendered_keys = {
        entry["data-track-key"]
        for entry in rerendered.select('.playlist-track[data-audio-crc="123"]')
    }

    assert len(entries) == 2
    assert len({entry["data-track-key"] for entry in entries}) == 2
    assert len(ids) == len(set(ids))
    assert rerendered_keys == {
        entry["data-track-key"] for entry in entries
    }


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
