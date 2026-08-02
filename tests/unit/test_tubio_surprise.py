from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import web_app.helpers as helpers
from web_app.redis_client import get_redis
from web_app.tubio.audio_downloader import AudioDownloader
from web_app.tubio.data_interface import (
    AudioMetadata,
    DataInterface,
    Metadata,
    Playlist,
)
from web_app.tubio.surprise import reserve_audio_metadata
from web_app.users import User


@pytest.fixture(scope="module", autouse=True)
def setup_app():
    import web_app.__main__  # noqa: F401
    from web_app.app import app
    from web_app.helpers import limiter

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    limiter.enabled = False


@pytest.fixture
def auth_mock():
    user = User(
        username="surprise-user",
        password="testpass",
        folder="test_folder",
        is_admin=False,
    )
    original = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = (
        lambda username: user if username == user.id else None
    )
    yield user
    helpers.login_manager._user_callback = original


def _surprise(*crcs: int, last_active: datetime | None = None) -> Playlist:
    return Playlist(
        name="Surprise Playlist",
        audio_crcs=list(crcs),
        last_active=last_active or datetime.now(timezone.utc),
    )


def _mock_data_interface(metadata: Metadata, tmp_path: Path | None = None):
    data = MagicMock()
    data.get_metadata.return_value = metadata
    data.get_user_metadata.side_effect = (
        lambda user: metadata.get_user(user.id)
    )
    data.edit_metadata.return_value.__enter__.return_value = metadata
    data.has_thumbnail.return_value = False
    if tmp_path is not None:
        data.app_audio_dir = tmp_path
    return data


class TestPlaylistModel:
    def test_existing_playlist_without_last_active_is_regular(self):
        playlist = Playlist.model_validate({
            "name": "Favourites",
            "audio_crcs": [101],
        })

        assert playlist.last_active is None

    def test_user_metadata_filters_temporary_playlist(self):
        metadata = Metadata()
        user = metadata.get_user("alice")
        user.get_playlist("Favourites").audio_crcs = [101]
        user.set_surprise_playlist(_surprise(202))

        assert [playlist.name for playlist in user.get_playlists()] == [
            "Favourites"
        ]
        assert user.get_surprise_playlist().audio_crcs == [202]

    def test_reservation_creates_uncached_audio_metadata(self):
        metadata = Metadata()

        crc = reserve_audio_metadata(metadata, {
            "video_id": "dQw4w9WgXcQ",
            "title": "A track",
        })

        assert metadata.audios[crc] == AudioMetadata(
            crc=crc,
            title="A track",
            yt_video_id="dQw4w9WgXcQ",
            is_cached=False,
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_reservation_reuses_existing_youtube_metadata(self):
        existing = AudioMetadata(
            crc=77,
            title="Existing",
            yt_video_id="dQw4w9WgXcQ",
            is_cached=True,
        )
        metadata = Metadata(audios={77: existing})

        crc = reserve_audio_metadata(metadata, {
            "video_id": "dQw4w9WgXcQ",
            "title": "Different title",
        })

        assert crc == 77
        assert metadata.audios == {77: existing}


class TestSurpriseCleanup:
    def test_expired_surprise_and_its_resources_are_removed(self, tmp_path):
        now = datetime.now(timezone.utc)
        data = DataInterface()
        data.app_dir = tmp_path
        data.app_audio_dir = tmp_path / "audio"
        data.app_thumbnails_dir = tmp_path / "thumbnails"
        data.app_metadata_file = tmp_path / "metadata.json"
        data.app_audio_dir.mkdir()
        data.app_thumbnails_dir.mkdir()
        metadata = Metadata(audios={
            101: AudioMetadata(crc=101, title="Durable"),
            202: AudioMetadata(crc=202, title="Active Surprise"),
            303: AudioMetadata(crc=303, title="Expired Surprise"),
            404: AudioMetadata(crc=404, title="Unused"),
        })
        user = metadata.get_user("alice")
        user.get_playlist("Favourites").audio_crcs = [101]
        user.set_surprise_playlist(_surprise(202, last_active=now))
        expired_user = metadata.get_user("bob")
        expired_user.set_surprise_playlist(
            _surprise(303, last_active=now - timedelta(hours=2))
        )
        data._save_model(data.app_metadata_file, metadata)
        for crc in (303, 404):
            (data.app_audio_dir / f"{crc}.m4a").write_bytes(b"audio")
            (data.app_thumbnails_dir / f"{crc}.jpg").write_bytes(b"image")

        data.cleanup_surprise_playlists(now=now)
        data.cleanup_unused_tracks()
        data.cleanup_unused_thumbnails()

        cleaned = data.get_metadata()
        assert cleaned.get_user("alice").get_surprise_playlist() is not None
        assert cleaned.get_user("bob").get_surprise_playlist() is None
        assert set(cleaned.audios) == {101, 202}
        assert not (data.app_audio_dir / "303.m4a").exists()
        assert not (data.app_thumbnails_dir / "303.jpg").exists()

    def test_combined_cleanup_runs_in_dependency_order(self):
        data = MagicMock(spec=DataInterface)

        DataInterface.cleanup_unused_resources(data)

        assert data.method_calls == [
            call.cleanup_surprise_playlists(),
            call.cleanup_unused_tracks(),
            call.cleanup_unused_thumbnails(),
        ]

    def test_backup_omits_temporary_playlists_and_dependencies(self, tmp_path):
        data = DataInterface()
        data.app_dir = tmp_path / "data"
        data.app_audio_dir = data.app_dir / "audio"
        data.app_thumbnails_dir = data.app_dir / "thumbnails"
        data.app_metadata_file = data.app_dir / "metadata.json"
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Durable",
                yt_video_id="video000001",
            ),
            202: AudioMetadata(
                crc=202,
                title="Temporary",
                yt_video_id="video000002",
            ),
        })
        user = metadata.get_user("alice")
        user.get_playlist("Favourites").audio_crcs = [101]
        user.set_surprise_playlist(_surprise(202))
        data._save_model(data.app_metadata_file, metadata)

        backup_dir = tmp_path / "backup"
        data.backup_data(backup_dir)

        backed_up = data.load_model(
            backup_dir / "tubio" / "metadata.json",
            Metadata,
            sync=False,
        )
        assert set(backed_up.audios) == {101}
        assert backed_up.get_user("alice").get_surprise_playlist() is None

    def test_scheduler_cleanup_was_removed(self):
        import web_app.__main__ as web_main

        assert not hasattr(web_main, "scheduled_tubio_cleanup")


class TestLazyCache:
    @patch.object(AudioDownloader, "download_thumbnail")
    @patch.object(AudioDownloader, "download_audio_file")
    @patch("web_app.tubio.audio_downloader.DataInterface")
    def test_materializes_audio_without_playlist_membership(
        self, data_class, download_audio, download_thumbnail, tmp_path
    ):
        scratch = tmp_path / "scratch.%(ext)s"
        (tmp_path / "scratch.m4a").write_bytes(b"audio")
        audio = AudioMetadata(
            crc=123,
            title="Lazy",
            yt_video_id="dQw4w9WgXcQ",
            is_cached=False,
        )
        metadata = Metadata(audios={123: audio})
        data = data_class.return_value
        data.find_avail_temp_file_path.return_value = scratch
        data.app_audio_dir = tmp_path / "audio"
        data.edit_metadata.return_value.__enter__.return_value = metadata

        AudioDownloader.cache_youtube_audio(audio)

        assert (tmp_path / "audio" / "123.m4a").read_bytes() == b"audio"
        assert metadata.audios[123].is_cached is True
        assert metadata.users == {}


class TestSurpriseRoutes:
    def setup_method(self):
        get_redis().flushdb()

    def test_client_errors_are_written_to_tubio_logs(
        self, client, auth_mock, caplog
    ):
        with client.session_transaction() as session:
            session["_user_id"] = auth_mock.id

        response = client.post(
            "/tubio/client-log",
            data={
                "scope": "discover-initialize",
                "message": "Cannot read properties of undefined",
                "stack": "stack trace",
                "context": '{"source":"restore"}',
            },
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        event = next(
            json.loads(record.getMessage())
            for record in caplog.records
            if record.getMessage().startswith("{")
            and json.loads(record.getMessage()).get("event") == "tubio.client_error"
        )
        assert event["app"] == "tubio"
        assert event["user"] == auth_mock.id
        assert event["ip"] == "127.0.0.1"
        assert event["scope"] == "discover-initialize"
        assert event["message_length"] == len("Cannot read properties of undefined")
        assert event["stack_present"] is True
        assert event["context_present"] is True
        assert "message" not in event
        assert "stack" not in event
        assert "context" not in event

    @patch("web_app.tubio.get_playlists_data", return_value=[])
    def test_discover_has_loading_shell_and_contextual_refresh_action(
        self, playlists_data, client, auth_mock
    ):
        with client.session_transaction() as session:
            session["_user_id"] = auth_mock.id

        response = client.get("/tubio/")
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'class="surprise-loading' in html
        assert 'aria-busy="true"' in html
        assert 'id="refresh-surprise-action"' in html
        assert 'data-tubio-action="refresh-surprise"' in html
        assert "script.js?v=" in html
        assert "style.css?v=" in html

    def test_regular_and_surprise_share_one_track_component(self, app):
        from flask import render_template

        track = {
            "crc": 123,
            "title": "Shared row",
            "thumbnail_url": "https://example.test/thumb.jpg",
            "source_url": "https://example.test/source",
            "video_id": "dQw4w9WgXcQ",
            "trim_start_s": 0,
            "trim_end_s": 0,
            "is_cached": False,
            "is_favourite": False,
        }
        with app.test_request_context():
            regular = render_template(
                "playlist.html",
                playlist_name="Favourites",
                playlist_data=[track],
            )
            surprise = render_template(
                "surprise_playlist.html",
                playlist_name="Surprise Playlist",
                playlist_data=[track],
            )

        for html in (regular, surprise):
            assert "playlist-track-expand" in html
            assert "playlist-track-select-slot" in html
            assert "playlist-track-details" in html
            assert "playlist-track-actions" in html
            assert "Suggest more" in html
            assert 'data-tubio-action="suggest-more"' in html
        assert 'data-tubio-action="favourite-surprise"' not in regular
        assert 'data-tubio-action="favourite-surprise"' in surprise
        assert "Converts on play" not in surprise
        assert "Downloads on play" not in surprise

        stylesheet = Path("web_app/tubio/static/style.css").read_text()
        assert ".surprise-playlist .btn {" not in stylesheet
        assert "appearance: none;" in stylesheet
        assert "--tubio-track-control-size: 16px;" in stylesheet

    def test_visible_surprise_track_can_be_favourited(
        self, client, auth_mock
    ):
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Visible track",
                yt_video_id="video000001",
                source_url="https://www.youtube.com/watch?v=video000001",
            ),
        })
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise/tracks/101/favourite",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert user.get_playlist().audio_crcs == [101]
        assert response.get_json()["playlist"]["html"].count("Favourited") >= 1

    def test_restore_touches_active_but_rejects_expired_playlist(
        self, client, auth_mock
    ):
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        metadata = Metadata()
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101, last_active=old_time))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            active_response = client.get("/tubio/surprise")
            user.get_surprise_playlist().last_active = (
                datetime.now(timezone.utc) - timedelta(hours=2)
            )
            expired_response = client.get("/tubio/surprise")

        assert active_response.get_json()["playlist"] is not None
        assert expired_response.get_json()["playlist"] is None

    @patch("web_app.tubio.AudioDownloader.cache_youtube_audio")
    def test_cache_request_touches_surprise_playlist(
        self, cache_youtube_audio, client, auth_mock, tmp_path
    ):
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        audio = AudioMetadata(
            crc=101,
            title="Lazy track",
            yt_video_id="video000001",
            is_cached=False,
        )
        metadata = Metadata(audios={101: audio})
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101, last_active=old_time))
        data = _mock_data_interface(metadata, tmp_path)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/audio/101/cache",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        cache_youtube_audio.assert_called_once_with(audio)
        assert user.get_surprise_playlist().last_active > old_time

    @patch("web_app.tubio.AudioDownloader.download_audio_file")
    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    @patch("web_app.tubio.get_cached_yt_vid_ids")
    def test_initial_generation_uses_flat_uncached_playlist_and_cleans(
        self, owned, related, download_audio, client, auth_mock
    ):
        owned.return_value = {"seed000000a"}
        related.return_value = [
            {
                "video_id": f"video00000{i}",
                "title": f"Track {i}",
                "duration_s": 120,
            }
            for i in range(1, 6)
        ]
        metadata = Metadata()
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        payload = response.get_json()["playlist"]
        assert len(payload["audio_crcs"]) == 5
        assert "playlist" not in payload
        assert payload["last_active"] is not None
        assert all(not audio.is_cached for audio in metadata.audios.values())
        assert metadata.get_user(auth_mock.id).get_surprise_playlist() is not None
        data.cleanup_unused_resources.assert_called_once()
        download_audio.assert_not_called()

    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    def test_seeded_generation_uses_only_accessible_selected_track(
        self, related, client, auth_mock
    ):
        related.return_value = [
            {
                "video_id": "seedvideo01",
                "title": "The seed itself",
                "duration_s": 120,
            },
            {
                "video_id": "ownedvideo2",
                "title": "Already owned",
                "duration_s": 120,
            },
            {
                "video_id": "suggested01",
                "title": "Suggested track",
                "duration_s": 120,
            },
        ]
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Selected seed",
                yt_video_id="seedvideo01",
            ),
            202: AudioMetadata(
                crc=202,
                title="Another library track",
                yt_video_id="ownedvideo2",
            ),
        })
        user = metadata.get_user(auth_mock.id)
        user.get_playlist().audio_crcs = [202]
        user.set_surprise_playlist(_surprise(101))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                data={"seed_crc": "101"},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        related.assert_called_once_with("seedvideo01")
        playlist = response.get_json()["playlist"]
        suggested = metadata.audios[playlist["audio_crcs"][0]]
        assert suggested.yt_video_id == "suggested01"
        assert len(playlist["audio_crcs"]) == 1

    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    @patch("web_app.tubio.AudioDownloader.search_youtube")
    def test_uploaded_seed_is_matched_by_title(
        self, search, related, client, auth_mock
    ):
        search.return_value = {
            "results": [{
                "video_id": "matchedvideo",
                "title": "Uploaded recording",
            }],
            "page": 0,
            "total_pages": 1,
        }
        related.return_value = [{
            "video_id": "suggested01",
            "title": "Suggested track",
            "duration_s": 120,
        }]
        metadata = Metadata(audios={
            101: AudioMetadata(crc=101, title="Uploaded recording"),
        })
        metadata.get_user(auth_mock.id).get_playlist().audio_crcs = [101]
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                data={"seed_crc": "101"},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert search.call_args.args[0].endswith("Uploaded recording")
        related.assert_called_once_with("matchedvideo")

    @patch("web_app.tubio.AudioDownloader.search_youtube")
    def test_unmatched_uploaded_seed_preserves_existing_surprise(
        self, search, client, auth_mock
    ):
        search.return_value = {
            "results": [],
            "page": 0,
            "total_pages": 1,
        }
        metadata = Metadata(audios={
            101: AudioMetadata(crc=101, title="Unmatched upload"),
            202: AudioMetadata(
                crc=202,
                title="Existing suggestion",
                yt_video_id="existing01",
            ),
        })
        user = metadata.get_user(auth_mock.id)
        user.get_playlist().audio_crcs = [101]
        user.set_surprise_playlist(_surprise(202))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                data={"seed_crc": "101"},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 422
        assert user.get_surprise_playlist().audio_crcs == [202]

    @pytest.mark.parametrize(
        ("seed_crc", "expected_status"),
        [("not-an-integer", 400), ("101", 404)],
    )
    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    def test_seeded_generation_rejects_invalid_or_inaccessible_track(
        self, related, seed_crc, expected_status, client, auth_mock
    ):
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Someone else's track",
                yt_video_id="privatevideo",
            ),
        })
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                data={"seed_crc": seed_crc},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == expected_status
        related.assert_not_called()

    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    def test_seeded_generation_rejects_expired_surprise_track(
        self, related, client, auth_mock
    ):
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Expired suggestion",
                yt_video_id="expiredvideo",
            ),
        })
        metadata.get_user(auth_mock.id).set_surprise_playlist(
            _surprise(
                101,
                last_active=datetime.now(timezone.utc) - timedelta(hours=2),
            )
        )
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                data={"seed_crc": "101"},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 404
        related.assert_not_called()

    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    @patch("web_app.tubio.get_cached_yt_vid_ids", return_value={"seed000000a"})
    def test_infinite_growth_appends_to_metadata_playlist(
        self, owned, related, client, auth_mock
    ):
        related.return_value = [{
            "video_id": "newvideo001",
            "title": "Next track",
            "duration_s": 120,
        }]
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Current",
                yt_video_id="oldvideo001",
            ),
        })
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise/grow",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert len(user.get_surprise_playlist().audio_crcs) == 2
        assert response.get_json()["playlist"]["audio_crcs"][0] == 101

    @patch("web_app.tubio.AudioDownloader.get_mix_related", return_value=[])
    @patch("web_app.tubio.get_cached_yt_vid_ids", return_value={"seed000000a"})
    def test_failed_refresh_preserves_existing_playlist_and_still_cleans(
        self, owned, related, client, auth_mock
    ):
        metadata = Metadata()
        user = metadata.get_user(auth_mock.id)
        old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        existing = _surprise(101, last_active=old_time)
        user.set_surprise_playlist(existing)
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert response.get_json()["exhausted"] is True
        assert user.get_surprise_playlist().audio_crcs == [101]
        assert user.get_surprise_playlist().last_active > old_time
        data.cleanup_unused_resources.assert_called_once()

    @patch("web_app.tubio.AudioDownloader.get_mix_related")
    @patch("web_app.tubio.get_cached_yt_vid_ids", return_value={"seed000000a"})
    def test_refresh_allows_tracks_from_previous_surprise(
        self, owned, related, client, auth_mock
    ):
        related.return_value = [{
            "video_id": "oldvideo001",
            "title": "Repeat",
            "duration_s": 120,
        }]
        metadata = Metadata(audios={
            101: AudioMetadata(
                crc=101,
                title="Repeat",
                yt_video_id="oldvideo001",
            ),
        })
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise",
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert response.get_json()["playlist"]["audio_crcs"] == [101]

    def test_save_converts_same_playlist_to_durable_and_preserves_order(
        self, client, auth_mock
    ):
        metadata = Metadata(audios={
            101: AudioMetadata(crc=101, title="First"),
            202: AudioMetadata(crc=202, title="Second"),
        })
        user = metadata.get_user(auth_mock.id)
        user.set_surprise_playlist(_surprise(101, 202))
        data = _mock_data_interface(metadata)

        with patch("web_app.tubio.DataInterface", return_value=data):
            with client.session_transaction() as session:
                session["_user_id"] = auth_mock.id
            response = client.post(
                "/tubio/surprise/save",
                data={"playlist_name": "Saved Mix"},
                headers={"Accept": "application/json"},
            )

        assert response.status_code == 200
        assert user.get_surprise_playlist() is None
        saved = user.playlists["Saved Mix"]
        assert saved.last_active is None
        assert saved.audio_crcs == [202, 101]
