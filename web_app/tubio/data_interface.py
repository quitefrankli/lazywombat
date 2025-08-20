import binascii
import json

from dataclasses import dataclass, field
from werkzeug.datastructures import FileStorage
from pathlib import Path

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.users import User
from web_app.config import ConfigManager


@dataclass
class UserMetadata:
    username: str
    # list of audio crcs
    favourites: list[int] = field(default_factory=list)

@dataclass
class AudioMetadata:
    # this is also the filename to be saved on disk
    # technically it's possible to have multiple audios with the same crc
    # but the chances of such collision are extremely low
    crc: int
    title: str

@dataclass
class Metadata:
    # username -> UserMetadata
    users: dict[str, UserMetadata] = field(default_factory=dict)
    # audio crc -> AudioMetadata
    audios: dict[int, AudioMetadata] = field(default_factory=dict)

class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.app_dir = ConfigManager().save_data_path / "tubio"
        self.app_audio_dir = self.app_dir / "audio"
        self.app_metadata_file = self.app_dir / "metadata.json"

    def save_audio(self, title: str, data: bytes) -> None:
        crc = binascii.crc32(data)
        metadata = self.get_metadata()
        if crc in metadata.audios:
            raise ValueError(f"Audio with crc {crc} already exists.")
        metadata.audios[crc] = AudioMetadata(crc=crc, title=title)
        self.save_metadata(metadata)
        self.atomic_write(self.app_audio_dir / f"{crc}.m4a", data=data, mode='wb')

    def delete_audio(self, crc: int) -> None:
        metadata = self.get_metadata()
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")
        metadata.audios.pop(crc)
        self.save_metadata(metadata)
        self.atomic_delete(self.app_audio_dir / f"{crc}.m4a")

    def get_metadata(self) -> Metadata:
        if not self.app_metadata_file.exists():
            return Metadata()
        with open(self.app_metadata_file, 'r') as f:
            data = f.read()

        return Metadata(**json.loads(data))
    
    def save_metadata(self, metadata: Metadata) -> None:
        json_data = json.dumps(metadata.__dict__, indent=4)
        self.atomic_write(self.app_metadata_file, data=json_data, mode='w', encoding='utf-8')

    def get_audio_path(self, crc: int) -> Path:
        """DEPRECATED want to stream eventually"""

        metadata = self.get_metadata()
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")
        
        return self.app_audio_dir / f"{crc}.m4a"