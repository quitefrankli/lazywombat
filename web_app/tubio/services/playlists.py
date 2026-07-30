from typing import Set
import web_app.tubio as tubio_facade

from flask import url_for

from web_app.tubio.data_interface import AudioMetadata, DataInterface
from web_app.users import User

def get_cached_yt_vid_ids(user: User|None = None) -> Set[str]:
    metadata = tubio_facade.DataInterface().get_metadata()
    if user is None:
        return {audio.yt_video_id for audio in metadata.audios.values()}
    else:
        user_metadata = tubio_facade.DataInterface().get_user_metadata(user)
        return {metadata.audios[crc].yt_video_id for crc in user_metadata.get_playlist().audio_crcs}

def _playlist_track_data(
    audio: AudioMetadata,
    user_metadata,
    *,
    is_favourite: bool = False,
) -> dict:
    playback_trim = user_metadata.get_playback_trim(audio.crc)
    has_thumbnail = tubio_facade.DataInterface().has_thumbnail(audio.crc)
    thumbnail_url = (
        url_for(".serve_thumbnail", crc=audio.crc)
        if has_thumbnail
        else (
            f"https://i.ytimg.com/vi/{audio.yt_video_id}/mqdefault.jpg"
            if audio.yt_video_id
            else ""
        )
    )
    return {
        "crc": audio.crc,
        "title": audio.title,
        "thumbnail_url": thumbnail_url,
        "source_url": audio.source_url,
        "video_id": audio.yt_video_id,
        "trim_start_s": playback_trim.start_s,
        "trim_end_s": playback_trim.end_s,
        "is_cached": audio.is_cached,
        "is_favourite": is_favourite,
    }


def _add_track_occurrences(tracks: list[dict]) -> list[dict]:
    """Give repeated CRCs stable identities within a rendered playlist."""
    occurrences: dict[int, int] = {}
    for track in tracks:
        crc = track["crc"]
        track["occurrence"] = occurrences.get(crc, 0)
        occurrences[crc] = track["occurrence"] + 1
    return tracks


def get_playlists_data(user: User) -> list[tuple[str, list[dict]]]:
    user_metadata = tubio_facade.DataInterface().get_user_metadata(user)
    playlists = []
    metadata = tubio_facade.DataInterface().get_metadata()
    for playlist in user_metadata.get_playlists():
        playlist_data = []
        for crc in reversed(playlist.audio_crcs):
            if crc in metadata.audios:
                audio = metadata.audios[crc]
                playlist_data.append(_playlist_track_data(audio, user_metadata))
        playlists.append((playlist.name, _add_track_occurrences(playlist_data)))

    return playlists
