"""Unit tests for file_store module"""

import pytest
import io
import binascii
import logging
import zipfile
from datetime import datetime
from unittest.mock import Mock, patch
from werkzeug.datastructures import FileStorage

# Import app from __main__ where blueprints are registered
import web_app.__main__ as main_module
from web_app.users import User
from web_app.config import ConfigManager
from web_app.file_store.data_interface import (
    DataInterface,
    Metadata,
    UserMetadata,
    FileMetadata,
    UserFileEntry
)
from web_app.helpers import limiter
import web_app.helpers as helpers

app = main_module.app


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a regular test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def test_user2():
    """Create a second test user"""
    return User(username='testuser2', password='testpass2', folder='test_folder2', is_admin=False)


@pytest.fixture
def admin_user():
    """Create an admin test user"""
    return User(username='admin', password='admin', folder='admin_folder', is_admin=True)


@pytest.fixture
def auth_mock(test_user):
    """Setup authentication mocking for tests"""
    # Mock the user_loader to return our test user
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None

    yield test_user

    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


@pytest.fixture
def data_interface(tmp_path):
    """Create a DataInterface with temporary directory"""
    di = DataInterface()
    di.file_store_dir = tmp_path / "file_store"
    di.files_dir = di.file_store_dir / "files"
    di.metadata_file = di.file_store_dir / "metadata.json"
    return di


class TestDataInterface:
    """Tests for FileStoreDataInterface with metadata-based storage"""

    def test_nested_folder_file_can_be_moved_and_deleted(self, data_interface, test_user):
        data_interface.create_folder('reports/2026', test_user)
        data_interface.save_file(
            FileStorage(io.BytesIO(b'budget'), 'budget.csv', content_type='text/csv'),
            test_user,
            relative_path='reports/2026/budget.csv',
        )

        directory = data_interface.list_directory('reports/2026', test_user)
        assert [item['name'] for item in directory['files']] == ['budget.csv']

        data_interface.move_path('reports/2026/budget.csv', 'reports/budget.csv', test_user)
        assert data_interface.get_file_path('reports/budget.csv', test_user).exists()

        data_interface.delete_path('reports', test_user)
        assert data_interface.list_directory('', test_user) == {'folders': [], 'files': []}

    def test_list_directory_recent_combines_files_and_folders_newest_first(
        self, data_interface, test_user
    ):
        data_interface.save_file(
            FileStorage(io.BytesIO(b'old'), 'old.txt'), test_user,
        )
        data_interface.save_file(
            FileStorage(io.BytesIO(b'nested'), 'nested.txt'), test_user,
            relative_path='recent-folder/nested.txt',
        )
        data_interface.save_file(
            FileStorage(io.BytesIO(b'new'), 'new.txt'), test_user,
        )
        with data_interface.edit_metadata() as metadata:
            dates = {
                'old.txt': '2026-01-01T10:00:00',
                'recent-folder/nested.txt': '2026-03-01T10:00:00',
                'new.txt': '2026-02-01T10:00:00',
            }
            for entry in metadata.users[test_user.id].files:
                entry.uploaded_at = dates[data_interface._entry_path(entry)]

        directory = data_interface.list_directory('', test_user, recent=True)

        assert [(item['kind'], item['name']) for item in directory['items']] == [
            ('folder', 'recent-folder'),
            ('file', 'new.txt'),
            ('file', 'old.txt'),
        ]

    def test_batch_operations_move_and_delete_nested_selections(self, data_interface, test_user):
        data_interface.create_folder('reports/2026', test_user)
        data_interface.save_file(
            FileStorage(io.BytesIO(b'budget'), 'budget.csv', content_type='text/csv'),
            test_user,
            relative_path='reports/2026/budget.csv',
        )
        data_interface.save_file(
            FileStorage(io.BytesIO(b'notes'), 'notes.txt', content_type='text/plain'),
            test_user,
        )

        data_interface.move_paths(
            ['reports', 'reports/2026/budget.csv', 'notes.txt'], 'archive', test_user,
        )

        assert data_interface.list_directory('', test_user)['folders'] == [{'name': 'archive', 'path': 'archive'}]
        assert [item['name'] for item in data_interface.list_directory('archive', test_user)['files']] == ['notes.txt']
        assert data_interface.get_file_path('archive/reports/2026/budget.csv', test_user).exists()

        data_interface.save_file(
            FileStorage(io.BytesIO(b'existing'), 'notes.txt', content_type='text/plain'),
            test_user,
            relative_path='target/notes.txt',
        )
        with pytest.raises(ValueError, match='Destination already exists'):
            data_interface.move_paths(['archive/notes.txt'], 'target', test_user)
        assert data_interface.get_file_path('archive/notes.txt', test_user).exists()

        data_interface.delete_paths(['archive/reports', 'archive/reports/2026/budget.csv'], test_user)

        assert data_interface.list_directory('archive', test_user)['files'][0]['name'] == 'notes.txt'
        assert data_interface.list_directory('archive/reports', test_user) == {'folders': [], 'files': []}

    def test_folder_import_rejects_file_folder_path_collision(self, data_interface, test_user):
        data_interface.save_file(FileStorage(io.BytesIO(b'file'), 'reports'), test_user)

        with pytest.raises(ValueError, match='folder path'):
            data_interface.save_files(
                [(FileStorage(io.BytesIO(b'budget'), 'budget.csv'), 'reports/budget.csv')],
                [],
                test_user,
            )

    def test_save_file_streams_large_upload_in_chunks(self, data_interface, test_user):
        """Uploads are copied incrementally rather than buffered in memory."""
        class ChunkOnlyStream(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.read_sizes = []

            def read(self, size=-1):
                assert size > 0
                self.read_sizes.append(size)
                return super().read(size)

        file_data = b"a" * (ConfigManager().file_store.upload_stream_chunk_bytes * 2 + 1)
        stream = ChunkOnlyStream(file_data)
        file_storage = Mock(filename='large.bin', content_type='application/octet-stream', stream=stream)

        crc = data_interface.save_file(file_storage, test_user)

        assert crc == binascii.crc32(file_data)
        assert (data_interface.files_dir / str(crc)).read_bytes() == file_data
        assert stream.read_sizes == [ConfigManager().file_store.upload_stream_chunk_bytes] * 4

    def test_get_metadata_empty(self, data_interface):
        """Test getting metadata when file doesn't exist"""
        metadata = data_interface.get_metadata()

        assert isinstance(metadata, Metadata)
        assert metadata.users == {}
        assert metadata.files == {}

    def test_save_and_load_metadata(self, data_interface):
        """Test saving and loading metadata"""
        metadata = Metadata()
        metadata.users['user1'] = UserMetadata(
            user_id='user1',
            files=[UserFileEntry(crc=123, original_name='test.txt')]
        )
        metadata.files[123] = FileMetadata(
            crc=123,
            original_name='test.txt',
            size=100,
            upload_date=datetime.now().isoformat()
        )

        data_interface._save_metadata(metadata)

        loaded = data_interface.get_metadata()
        assert 'user1' in loaded.users
        assert 123 in loaded.files
        assert loaded.files[123].original_name == 'test.txt'
        assert len(loaded.users['user1'].files) == 1

    def test_save_file_new(self, data_interface, test_user):
        """Test saving a new file"""
        file_data = b'test content'
        file_storage = FileStorage(io.BytesIO(file_data), 'test.txt', content_type='text/plain')

        crc = data_interface.save_file(file_storage, test_user)

        assert crc == binascii.crc32(file_data)

        # Verify file was saved
        file_path = data_interface.files_dir / str(crc)
        assert file_path.exists()
        assert file_path.read_bytes() == file_data

        # Verify metadata
        metadata = data_interface.get_metadata()
        assert test_user.id in metadata.users
        assert len(metadata.users[test_user.id].files) == 1
        assert metadata.users[test_user.id].files[0].crc == crc
        assert metadata.users[test_user.id].files[0].original_name == 'test.txt'
        assert crc in metadata.files
        assert metadata.files[crc].original_name == 'test.txt'

    def test_save_file_duplicate_dedup(self, data_interface, test_user):
        """Test that duplicate content upload is ignored for the same user"""
        file_data = b'exact same bytes for dedup check'
        file_storage1 = FileStorage(io.BytesIO(file_data), 'first_name.txt', content_type='text/plain')
        file_storage2 = FileStorage(io.BytesIO(file_data), 'second_name.txt', content_type='text/plain')

        crc1 = data_interface.save_file(file_storage1, test_user)
        crc2 = data_interface.save_file(file_storage2, test_user)

        # CRCs should be the same
        assert crc1 == crc2

        # Duplicate upload should not create another user entry
        metadata = data_interface.get_metadata()
        assert len(metadata.users[test_user.id].files) == 1
        assert metadata.users[test_user.id].files[0].original_name == 'first_name.txt'
        assert metadata.users[test_user.id].files[0].crc == crc1

        # But only one file metadata entry (from first upload)
        assert metadata.files[crc1].original_name == 'first_name.txt'

        stored_blobs = [file for file in data_interface.files_dir.iterdir() if file.is_file()]
        assert len(stored_blobs) == 1
        assert stored_blobs[0].name == str(crc1)
        assert stored_blobs[0].read_bytes() == file_data

    def test_save_file_different_users_same_content(self, data_interface, test_user, test_user2):
        """Test that different users can share the same file content"""
        file_data = b'shared content'

        file_storage1 = FileStorage(io.BytesIO(file_data), 'user1.txt', content_type='text/plain')
        file_storage2 = FileStorage(io.BytesIO(file_data), 'user2.txt', content_type='text/plain')

        crc1 = data_interface.save_file(file_storage1, test_user)
        crc2 = data_interface.save_file(file_storage2, test_user2)

        assert crc1 == crc2

        metadata = data_interface.get_metadata()
        assert any(f.crc == crc1 for f in metadata.users[test_user.id].files)
        assert any(f.crc == crc1 for f in metadata.users[test_user2.id].files)

        # Only one physical file
        file_path = data_interface.files_dir / str(crc1)
        assert file_path.exists()

    def test_get_file_path(self, data_interface, test_user):
        """Test getting file path by original filename"""
        file_data = b'test content'
        file_storage = FileStorage(io.BytesIO(file_data), 'myfile.txt', content_type='text/plain')

        crc = data_interface.save_file(file_storage, test_user)

        path = data_interface.get_file_path('myfile.txt', test_user)

        assert path == data_interface.files_dir / str(crc)
        assert path.exists()

    def test_get_file_path_not_found(self, data_interface, test_user):
        """Test getting non-existent file"""
        with pytest.raises(FileNotFoundError):
            data_interface.get_file_path('nonexistent.txt', test_user)

    def test_delete_file_single_user(self, data_interface, test_user):
        """Test deleting a file when only one user has it"""
        file_data = b'test content'
        file_storage = FileStorage(io.BytesIO(file_data), 'test.txt', content_type='text/plain')

        crc = data_interface.save_file(file_storage, test_user)
        file_path = data_interface.files_dir / str(crc)

        assert file_path.exists()

        data_interface.delete_file('test.txt', test_user)

        # File should be deleted from disk
        assert not file_path.exists()

        # Metadata should be cleaned up
        metadata = data_interface.get_metadata()
        assert crc not in metadata.files
        assert not any(f.crc == crc for f in metadata.users[test_user.id].files)

    def test_delete_file_multiple_users(self, data_interface, test_user, test_user2):
        """Test deleting file when multiple users have it"""
        file_data = b'shared content'

        file_storage1 = FileStorage(io.BytesIO(file_data), 'file1.txt', content_type='text/plain')
        file_storage2 = FileStorage(io.BytesIO(file_data), 'file2.txt', content_type='text/plain')

        crc = data_interface.save_file(file_storage1, test_user)
        data_interface.save_file(file_storage2, test_user2)

        file_path = data_interface.files_dir / str(crc)
        assert file_path.exists()

        # Delete from first user
        data_interface.delete_file('file1.txt', test_user)

        # File should still exist (user2 still has it)
        assert file_path.exists()

        # Metadata should still have the file
        metadata = data_interface.get_metadata()
        assert crc in metadata.files
        assert not any(f.crc == crc for f in metadata.users[test_user.id].files)
        assert any(f.crc == crc for f in metadata.users[test_user2.id].files)

        # Delete from second user
        data_interface.delete_file('file2.txt', test_user2)

        # Now file should be deleted
        assert not file_path.exists()
        metadata = data_interface.get_metadata()
        assert crc not in metadata.files

    def test_list_files(self, data_interface, test_user):
        """Test listing user files"""
        # New user should have empty list
        files = data_interface.list_files(test_user)
        assert files == []

        # Add some files
        for filename in ['file1.txt', 'file2.txt']:
            file_storage = FileStorage(io.BytesIO(filename.encode()), filename, content_type='text/plain')
            data_interface.save_file(file_storage, test_user)

        files = data_interface.list_files(test_user)
        assert sorted(files) == ['file1.txt', 'file2.txt']

    def test_list_files_with_metadata(self, data_interface, test_user):
        """Test listing files with metadata"""
        file_storage = FileStorage(io.BytesIO(b'test content'), 'test.txt', content_type='text/plain')

        data_interface.save_file(file_storage, test_user)

        files = data_interface.list_files_with_metadata(test_user)

        assert len(files) == 1
        assert files[0]['name'] == 'test.txt'
        assert files[0]['size'] == 12
        assert 'size_formatted' in files[0]
        assert 'modified_formatted' in files[0]
        assert files[0]['mime_type'] == 'text/plain'

    def test_get_total_storage_size_new_user(self, data_interface, test_user):
        """Test storage size for new user"""
        size = data_interface.get_total_storage_size(test_user)
        assert size == 0

    def test_get_total_storage_size_with_files(self, data_interface, test_user):
        """Test storage size calculation"""
        file1_data = b'a' * 100
        file2_data = b'b' * 200

        for filename, data in [('file1.txt', file1_data), ('file2.txt', file2_data)]:
            file_storage = FileStorage(io.BytesIO(data), filename, content_type='text/plain')
            data_interface.save_file(file_storage, test_user)

        size = data_interface.get_total_storage_size(test_user)
        assert size == 300

    def test_get_user_metadata_new_user(self, data_interface, test_user):
        """Test getting metadata for new user"""
        user_meta = data_interface.get_user_metadata(test_user)

        assert user_meta.user_id == test_user.id
        assert user_meta.files == []


class TestFileStoreRoutes:
    """Tests for file_store routes"""

    @patch('web_app.file_store.DataInterface')
    def test_index_list_mode(self, mock_di_class, client, auth_mock):
        """Test index page in list mode"""
        mock_di = mock_di_class.return_value
        file_item = {
            'name': 'file1.txt', 'path': 'file1.txt', 'size': 100,
            'size_formatted': '100.0 B', 'mime_type': 'text/plain', 'kind': 'file',
        }
        mock_di.list_directory.return_value = {
            'folders': [], 'files': [file_item], 'items': [file_item],
        }
        mock_di.get_total_storage_size.return_value = 100

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/?mode=list')

        assert response.status_code == 200
        assert b'file1.txt' in response.data
        assert b'file-store-actions' not in response.data
        assert b'class="btn btn-outline-secondary btn-sm move-button"' not in response.data
        assert b'Delete file' not in response.data
        assert b'id="galleryColumns"' not in response.data

    @patch('web_app.file_store.DataInterface')
    def test_index_grid_mode_shows_folders_and_images_only(self, mock_di_class, client, auth_mock):
        """Grid mode is a visual gallery, not a second file list."""
        mock_di = mock_di_class.return_value
        mock_di.list_directory.return_value = {'folders': [
            {'name': 'Photos', 'path': 'photos'},
        ], 'files': [
            {'name': 'photo.jpg', 'path': 'photo.jpg', 'size': 100,
             'size_formatted': '100.0 B', 'mime_type': 'image/jpeg'},
            {'name': 'document.txt', 'path': 'document.txt', 'size': 200,
             'size_formatted': '200.0 B', 'mime_type': 'text/plain'},
        ]}
        mock_di.get_total_storage_size.return_value = 300

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/?mode=grid')

        assert response.status_code == 200
        assert b'file-directory file-grid' in response.data
        assert b'file-grid-folder' in response.data
        assert b'file-grid-folder-name' in response.data
        assert b'Photos' in response.data
        assert b'data-thumbnail-src=' in response.data
        assert b'id="galleryColumns"' in response.data
        assert b'min="2"' in response.data
        assert b'max="10"' in response.data
        assert b'value="5"' in response.data
        assert b'id="modalImage"' in response.data
        assert b'modal-fullscreen-md-down' in response.data
        assert b'imageModalLabel' not in response.data
        assert b'data-gallery-swipe-min-distance-px' not in response.data
        assert b'id="previousImage"' not in response.data
        assert b'id="nextImage"' not in response.data
        assert b'photo.jpg' in response.data
        assert b'document.txt' not in response.data
        assert b'file-grid-actions' not in response.data

    @patch('web_app.file_store.DataInterface')
    def test_grid_mode_uses_nested_thumbnail_path(self, mock_di_class, client, auth_mock):
        mock_di = mock_di_class.return_value
        mock_di.list_directory.return_value = {'folders': [], 'files': [
            {'name': 'photo.jpg', 'path': 'photos/photo.jpg', 'size': 100,
             'size_formatted': '100.0 B', 'mime_type': 'image/jpeg'},
        ]}
        mock_di.get_total_storage_size.return_value = 100

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/?mode=grid')

        assert response.status_code == 200
        assert b'/file_store/thumbnail/photos/photo.jpg' in response.data

    @patch('web_app.file_store.DataInterface')
    def test_upload_file_success(self, mock_di_class, client, auth_mock, caplog):
        """Test successful file upload"""
        mock_di = mock_di_class.return_value
        mock_di.get_total_storage_size.return_value = 0
        mock_di.save_file = Mock(return_value=123)

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        caplog.set_level(logging.INFO)
        data = {'file': (io.BytesIO(b'test content'), 'test.txt')}
        response = client.post('/file_store/upload', data=data, content_type='multipart/form-data')

        assert response.status_code == 302  # Redirect
        assert '"event": "file_store.upload"' in caplog.text
        assert '"files": 1' in caplog.text

    @patch('web_app.file_store.DataInterface')
    def test_upload_file_rejection_is_logged(self, mock_di_class, client, auth_mock, caplog):
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        caplog.set_level(logging.WARNING)
        response = client.post('/file_store/upload', data={}, content_type='multipart/form-data')

        assert response.status_code == 302
        assert '"event": "file_store.upload_rejected"' in caplog.text

    @patch('web_app.file_store.DataInterface')
    def test_download_file(self, mock_di_class, client, auth_mock, tmp_path, caplog):
        """Test downloading a file"""
        mock_di = mock_di_class.return_value

        # Create a real temporary file
        test_file = tmp_path / '123'  # CRC-based filename
        test_file.write_text('download test content')
        mock_di.get_file_path.return_value = test_file

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        caplog.set_level(logging.INFO)
        response = client.get('/file_store/download/test.txt')

        assert response.status_code == 200
        assert '"event": "file_store.download"' in caplog.text
        assert '"path": "test.txt"' in caplog.text

    @patch('web_app.file_store.DataInterface')
    def test_download_file_uses_display_filename(self, mock_di_class, client, auth_mock, tmp_path):
        stored_file = tmp_path / '123'
        stored_file.write_text('download test content')
        mock_di_class.return_value.get_file_path.return_value = stored_file

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/file_store/download/report.csv')

        assert response.status_code == 200
        assert 'filename=report.csv' in response.headers['Content-Disposition']

    @patch('web_app.file_store.DataInterface')
    def test_bulk_routes_submit_all_selected_paths(self, mock_di_class, client, auth_mock):
        mock_di = mock_di_class.return_value
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        move_response = client.post('/file_store/move-selected', data={
            'paths': ['reports', 'notes.txt'], 'destination': 'archive', 'parent': 'inbox',
        })
        delete_response = client.post('/file_store/delete-selected', data={
            'paths': ['reports', 'notes.txt'], 'parent': 'inbox',
        })

        assert move_response.status_code == 302
        assert delete_response.status_code == 302
        mock_di.move_paths.assert_called_once_with(['reports', 'notes.txt'], 'archive', auth_mock)
        mock_di.delete_paths.assert_called_once_with(['reports', 'notes.txt'], auth_mock)

    @patch('web_app.file_store.DataInterface')
    def test_download_folder_returns_nested_zip(self, mock_di_class, client, auth_mock, tmp_path, caplog):
        stored_file = tmp_path / '123'
        stored_file.write_bytes(b'budget')
        mock_di_class.return_value.get_folder_files.return_value = [
            ('reports/2026/budget.csv', stored_file),
        ]
        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        caplog.set_level(logging.INFO)
        response = client.get('/file_store/download-folder/reports')

        assert response.status_code == 200
        assert '"event": "file_store.download_folder"' in caplog.text
        assert '"path": "reports"' in caplog.text
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            assert archive.read('reports/2026/budget.csv') == b'budget'

    @patch('web_app.file_store.DataInterface')
    def test_delete_all_files(self, mock_di_class, client, auth_mock, caplog):
        """Test deleting all files clears the user's entries in one transaction."""
        from contextlib import contextmanager

        metadata = Metadata(
            users={auth_mock.id: UserMetadata(
                user_id=auth_mock.id,
                files=[
                    UserFileEntry(crc=1, original_name='file1.txt', path='file1.txt'),
                    UserFileEntry(crc=2, original_name='file2.txt', path='file2.txt'),
                ],
                folders=['docs'],
            )},
            files={
                1: FileMetadata(crc=1, original_name='file1.txt', size=1, upload_date='x'),
                2: FileMetadata(crc=2, original_name='file2.txt', size=1, upload_date='x'),
            },
        )

        mock_di = mock_di_class.return_value
        mock_di.list_files.return_value = ['file1.txt', 'file2.txt']

        @contextmanager
        def _edit():
            yield metadata
        mock_di.edit_metadata.side_effect = _edit
        # _cleanup_unreferenced is called on the real metadata; make it a no-op.
        mock_di._cleanup_unreferenced = lambda md: None

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        caplog.set_level(logging.INFO)
        response = client.post('/file_store/delete_all')

        assert response.status_code == 302
        # The user's file/folder entries were cleared in the transaction.
        assert metadata.users[auth_mock.id].files == []
        assert metadata.users[auth_mock.id].folders == []
        assert '"event": "file_store.delete_all"' in caplog.text
        assert '"files": 2' in caplog.text


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
