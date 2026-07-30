import logging
import web_app.tubio as tubio_facade

from flask import flash, redirect, request, url_for

from web_app.config import ConfigManager
from web_app.helpers import cur_user, limiter
from web_app.logging_utils import log_event
from web_app.tubio import tubio_api
from web_app.tubio.data_interface import DataInterface, Playlist
from web_app.tubio.services.playlists import get_playlists_data

@tubio_api.route('/create_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def create_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()

        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))

        user = cur_user()
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            # Check if playlist already exists
            if playlist_name in user_metadata.playlists:
                flash(f'Playlist "{playlist_name}" already exists.', 'warning')
                return redirect(url_for('.index'))

            # Create new playlist
            user_metadata.get_playlist(playlist_name)

        log_event("tubio", "tubio.playlist_created", playlist=playlist_name)
        flash(f'Playlist "{playlist_name}" created successfully!', 'success')

    except Exception as e:
        log_event(
            "tubio",
            "tubio.playlist_create_failed",
            level=logging.ERROR,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error creating playlist.', 'error')

    return redirect(url_for('.index'))

@tubio_api.route('/move_tracks_to_playlist', methods=['POST'])
@limiter.limit("20 per minute")
def move_tracks_to_playlist():
    try:
        target_playlist = request.form.get('target_playlist', '').strip()
        song_crcs_str = request.form.get('song_crcs', '')

        if not target_playlist:
            flash('Please select a target playlist.', 'error')
            return redirect(url_for('.index'))

        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))

        # Parse CRCs
        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        if not song_crcs:
            flash('No valid songs selected.', 'warning')
            return redirect(url_for('.index'))

        user = cur_user()
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            for crc in song_crcs:
                user_metadata.remove_from_all_playlists(crc)
                user_metadata.add_to_playlist(crc, target_playlist)
        log_event(
            "tubio",
            "tubio.tracks_moved",
            playlist=target_playlist,
            tracks=len(song_crcs),
        )
    except Exception as e:
        log_event(
            "tubio",
            "tubio.tracks_move_failed",
            level=logging.ERROR,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error moving songs to playlist.', 'error')

    return redirect(url_for('.index'))


@tubio_api.route('/delete_selected_songs', methods=['POST'])
@limiter.limit("10 per minute")
def delete_selected_songs():
    try:
        song_crcs_str = request.form.get('song_crcs', '')

        if not song_crcs_str:
            flash('No songs selected.', 'warning')
            return redirect(url_for('.index'))

        song_crcs = [int(crc) for crc in song_crcs_str.split(',') if crc.strip()]
        user = cur_user()
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            for crc in song_crcs:
                user_metadata.remove_from_all_playlists(crc)

        tubio_facade.DataInterface().cleanup_unused_resources()
        log_event("tubio", "tubio.tracks_deleted", tracks=len(song_crcs))
    except Exception as e:
        log_event(
            "tubio",
            "tubio.tracks_delete_failed",
            level=logging.ERROR,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error deleting songs.', 'error')

    return redirect(url_for('.index'))

@tubio_api.route('/delete_playlist', methods=['POST'])
@limiter.limit("10 per minute")
def delete_playlist():
    try:
        playlist_name = request.form.get('playlist_name', '').strip()

        if not playlist_name:
            flash('Playlist name cannot be empty.', 'error')
            return redirect(url_for('.index'))

        # Prevent deletion of Favourites playlist
        if playlist_name == ConfigManager().tubio.default_playlist_name:
            flash('Cannot delete the Favourites playlist.', 'error')
            return redirect(url_for('.index'))

        user = cur_user()
        with tubio_facade.DataInterface().edit_metadata() as metadata:
            user_metadata = metadata.get_user(user.id)

            # Check if playlist exists
            if playlist_name not in user_metadata.playlists:
                flash(f'Playlist "{playlist_name}" does not exist.', 'warning')
                return redirect(url_for('.index'))

            # Delete the playlist
            del user_metadata.playlists[playlist_name]

        tubio_facade.DataInterface().cleanup_unused_resources()
        log_event("tubio", "tubio.playlist_deleted", playlist=playlist_name)
        flash(f'Playlist "{playlist_name}" deleted successfully!', 'success')

    except Exception as e:
        log_event(
            "tubio",
            "tubio.playlist_delete_failed",
            level=logging.ERROR,
            exc_info=e,
            error_type=type(e).__name__,
        )
        flash('Error deleting playlist.', 'error')

    return redirect(url_for('.index'))
