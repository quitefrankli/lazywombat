import binascii
import logging
import shutil

from pathlib import Path
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from pydantic import Field
from copy import deepcopy
from pydub import AudioSegment

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.users import User
from web_app.config import ConfigManager
from web_app.logging_utils import log_event


class Playlist(BaseModel):
    name: str
    audio_crcs: list[int] = Field(default_factory=list)
    # NOTE this is used to indicate that the playlist is a "Surprise" playlist, which is ephemeral and can be cleaned up after a period of inactivity. If last_active is None, it is not a Surprise playlist.
    last_active: datetime | None = None

class PlaybackTrim(BaseModel):
    start_s: float = 0
    end_s: float = 0

class UserMetadata(BaseModel):
    user_id: str
    playlists: dict[str, Playlist] = Field(default_factory=dict)
    playback_trims: dict[int, PlaybackTrim] = Field(default_factory=dict)

    def add_to_playlist(
        self,
        audio_crc: int,
        playlist_name: str | None = None,
    ) -> None:
        playlist = self.get_playlist(playlist_name)
        if audio_crc not in playlist.audio_crcs:
            playlist.audio_crcs.append(audio_crc)

    def remove_from_regular_playlists(self, audio_crc: int) -> None:
        for playlist in self.get_playlists():
            playlist.audio_crcs = [
                crc for crc in playlist.audio_crcs if crc != audio_crc
            ]

    def get_playlist(self, playlist_name: str | None = None) -> Playlist:
        playlist_name = (
            playlist_name or ConfigManager().tubio.default_playlist_name
        )
        if playlist_name not in self.playlists:
            self.playlists[playlist_name] = Playlist(name=playlist_name)
        
        return self.playlists[playlist_name]
    
    def get_playlists(self) -> list[Playlist]:
        return [
            playlist
            for playlist in self.playlists.values()
            if playlist.last_active is None
        ]

    def get_surprise_playlist(self) -> Playlist | None:
        playlist = self.playlists.get(
            ConfigManager().tubio.surprise_playlist_storage_key
        )
        if playlist is None or playlist.last_active is None:
            return None
        return playlist

    def set_surprise_playlist(self, playlist: Playlist) -> None:
        if playlist.last_active is None:
            raise ValueError("A Surprise playlist requires last_active")
        self.playlists[
            ConfigManager().tubio.surprise_playlist_storage_key
        ] = playlist

    def pop_surprise_playlist(self) -> Playlist | None:
        return self.playlists.pop(
            ConfigManager().tubio.surprise_playlist_storage_key,
            None,
        )

    def set_playback_trim(self, audio_crc: int, start_s: float, end_s: float) -> None:
        if start_s == 0 and end_s == 0:
            self.playback_trims.pop(audio_crc, None)
        else:
            self.playback_trims[audio_crc] = PlaybackTrim(start_s=start_s, end_s=end_s)

    def get_playback_trim(self, audio_crc: int) -> PlaybackTrim:
        return self.playback_trims.get(audio_crc, PlaybackTrim())

class AudioMetadata(BaseModel):
    # this is also the filename to be saved on disk
    # technically it's possible to have multiple audios with the same crc
    # but the chances of such collision are extremely low
    crc: int
    title: str
    yt_video_id: str = ''  # optional, if the audio is from YouTube
    is_cached: bool = False
    source_url: str = ''  # original source URL (e.g. YouTube URL)

class Metadata(BaseModel):
    # username -> UserMetadata
    users: dict[str, UserMetadata] = Field(default_factory=dict)
    # audio crc -> AudioMetadata
    audios: dict[int, AudioMetadata] = Field(default_factory=dict)

    def get_user(self, user_id: str) -> UserMetadata:
        """Get-or-create this user's slice. Use inside an edit_metadata() block
        so the mutation is persisted under the lock."""
        if user_id not in self.users:
            self.users[user_id] = UserMetadata(user_id=user_id)
        return self.users[user_id]

class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.app_dir = ConfigManager().save_data_path / "tubio"
        self.app_audio_dir = self.app_dir / "audio"
        self.app_thumbnails_dir = self.app_dir / "thumbnails"
        self.app_metadata_file = self.app_dir / "metadata.json"

    def get_metadata(self) -> Metadata:
        """Read-only load. For mutations use edit_metadata() so the write is locked."""
        return self.load_model(self.app_metadata_file, Metadata, sync=False) or Metadata()

    def edit_metadata(self):
        """Transactional edit of the shared tubio metadata blob.

        `with di.edit_metadata() as metadata: metadata.get_user(uid)...` — locks
        the file, loads fresh, saves on clean exit (only if changed). Because
        the blob is shared across all users, this is a global lock.
        """
        return self.edit_model(self.app_metadata_file, Metadata)

    def get_user_metadata(self, user: User) -> UserMetadata:
        """Read-only per-user slice. For mutations use edit_metadata() +
        metadata.get_user(user.id)."""
        return self.get_metadata().get_user(user.id)

    def get_audio_metadata(self, crc: int|None = None, yt_video_id: str|None = None) -> AudioMetadata:
        if not ((crc is None) ^ (yt_video_id is None)):
            raise ValueError("Either crc or yt_video_id must be provided, but not both.")
        
        metadata = self.get_metadata()
        if crc is not None:
            if crc not in metadata.audios:
                raise ValueError(f"Audio with crc {crc} does not exist.")
            return metadata.audios[crc]
        else:
            for audio in metadata.audios.values():
                if audio.yt_video_id == yt_video_id:
                    return audio
            raise ValueError(f"Audio with yt_video_id {yt_video_id} does not exist.")
        
    def save_audio(self, title: str, audio_data: bytes, ext: str) -> int:
        crc = binascii.crc32(audio_data)

        if crc in self.get_metadata().audios:
            log_event(
                "tubio", "tubio.audio_save_skipped",
                level=logging.WARNING, crc=crc, reason="already_exists",
            )
            return crc  # already exists

        audio_path = self.app_audio_dir / f"{crc}.{ext}"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audio_path, 'wb') as f:
            f.write(audio_data)

        if ext != 'm4a':
            # convert to m4a (slow transcode — kept outside the metadata lock)
            audio = AudioSegment.from_file(audio_path, format=ext)
            output_path = self.app_audio_dir / f"{crc}.m4a"
            config = ConfigManager().tubio
            audio.export(
                output_path,
                format=config.upload_transcode_format,
                bitrate=config.upload_transcode_bitrate,
            )
            audio_path.unlink()  # remove original file

        self.upsert_audio_metadata(AudioMetadata(crc=crc, title=title, is_cached=True))

        return crc

    def upsert_audio_metadata(self, audio_metadata: AudioMetadata) -> None:
        """Single-shot upsert of one audio record (locked read-modify-write)."""
        with self.edit_metadata() as metadata:
            metadata.audios[audio_metadata.crc] = audio_metadata

    def get_audio_path(self, crc: int, metadata: Metadata|None = None) -> Path:
        metadata = self.get_metadata() if metadata is None else metadata
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")

        return self.app_audio_dir / f"{crc}.m4a"

    def get_thumbnail_path(self, crc: int) -> Path:
        return self.app_thumbnails_dir / f"{crc}.jpg"

    def save_thumbnail(self, crc: int, thumbnail_data: bytes) -> None:
        self.app_thumbnails_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_path = self.get_thumbnail_path(crc)
        with open(thumbnail_path, 'wb') as f:
            f.write(thumbnail_data)

    def has_thumbnail(self, crc: int) -> bool:
        return self.get_thumbnail_path(crc).exists()

    def delete_user_data(self, user: User) -> None:
        with self.edit_metadata() as metadata:
            if user.id not in metadata.users:
                return
            metadata.users.pop(user.id)
        self.cleanup_unused_resources()

    def cleanup_unused_resources(
        self,
        now: datetime | None = None,
    ) -> None:
        """Expire temporary playlists and remove their unreferenced media."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(
            seconds=ConfigManager().tubio.surprise_playlist_inactivity_ttl_s
        )
        expired_playlists = 0
        unused_crcs: set[int] = set()
        remaining_crcs: set[int] = set()

        with self.edit_metadata() as metadata:
            for user_metadata in metadata.users.values():
                for key, playlist in list(user_metadata.playlists.items()):
                    last_active = playlist.last_active
                    if last_active is not None and last_active.tzinfo is None:
                        last_active = last_active.replace(tzinfo=timezone.utc)
                    if last_active is not None and last_active < cutoff:
                        user_metadata.playlists.pop(key)
                        expired_playlists += 1

            used_crcs = {
                crc
                for user_metadata in metadata.users.values()
                for playlist in user_metadata.playlists.values()
                for crc in playlist.audio_crcs
            }
            unused_crcs = set(metadata.audios) - used_crcs
            for crc in unused_crcs:
                metadata.audios.pop(crc)
            for user_metadata in metadata.users.values():
                for crc in unused_crcs:
                    user_metadata.playback_trims.pop(crc, None)
            remaining_crcs = set(metadata.audios)

        for crc in unused_crcs:
            self.atomic_delete(self.app_audio_dir / f"{crc}.m4a")
            log_event("tubio", "tubio.unused_audio_deleted", crc=crc)
        if self.app_thumbnails_dir.exists():
            remaining_stems = {str(crc) for crc in remaining_crcs}
            for thumbnail_path in self.app_thumbnails_dir.glob("*.jpg"):
                if thumbnail_path.stem not in remaining_stems:
                    self.atomic_delete(thumbnail_path)

        log_event(
            "tubio",
            "tubio.surprise_cleanup_completed",
            removed=expired_playlists,
        )
        log_event(
            "tubio",
            "tubio.unused_track_cleanup_completed",
            removed=len(unused_crcs),
        )

    def backup_data(self, backup_dir: Path) -> None:
        tubio_backup_dir = backup_dir / "tubio"
        tubio_backup_dir.mkdir(parents=True, exist_ok=True)
        audio_backup_dir = tubio_backup_dir / "audio"
        audio_backup_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = deepcopy(self.get_metadata())
        for user_metadata in metadata.users.values():
            user_metadata.playlists = {
                key: playlist
                for key, playlist in user_metadata.playlists.items()
                if playlist.last_active is None
            }
        durable_crcs = {
            crc
            for user_metadata in metadata.users.values()
            for playlist in user_metadata.playlists.values()
            for crc in playlist.audio_crcs
        }
        metadata.audios = {
            crc: audio
            for crc, audio in metadata.audios.items()
            if crc in durable_crcs
        }
        for audio in metadata.audios.values():
            if audio.yt_video_id:
                audio.is_cached = False
            else:
                # only copy files that cannot be easily redownloaded from yt
                shutil.copy2(self.get_audio_path(audio.crc, metadata), 
                             audio_backup_dir / f"{audio.crc}.m4a")

        self._save_model(tubio_backup_dir / "metadata.json", metadata)
