import binascii
import json

from werkzeug.datastructures import FileStorage
from pathlib import Path
from pydantic import BaseModel, Field

from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.users import User
from web_app.config import ConfigManager


class UserMetadata(BaseModel):
    # list of audio crcs
    favourites: list[int] = []

class AudioMetadata(BaseModel):
    # this is also the filename to be saved on disk
    # technically it's possible to have multiple audios with the same crc
    # but the chances of such collision are extremely low
    crc: int
    title: str
    yt_video_id: str = ''  # optional, if the audio is from YouTube

class Metadata(BaseModel):
    # username -> UserMetadata
    users: dict[str, UserMetadata] = {}
    # audio crc -> AudioMetadata
    audios: dict[int, AudioMetadata] = {}

class DataInterface(BaseDataInterface):
    def __init__(self) -> None:
        super().__init__()
        self.app_dir = ConfigManager().save_data_path / "tubio"
        self.app_audio_dir = self.app_dir / "audio"
        self.app_metadata_file = self.app_dir / "metadata.json"

    def save_audio(self, title: str, data: bytes, yt_video_id: str = "") -> None:
        crc = binascii.crc32(data)
        metadata = self.get_metadata()
        if crc in metadata.audios:
            raise ValueError(f"Audio with crc {crc} already exists.")
        metadata.audios[crc] = AudioMetadata(crc=crc, title=title, yt_video_id=yt_video_id)
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
        self.atomic_write(self.app_metadata_file, 
                          data=metadata.model_dump_json(indent=4), 
                          mode="w", 
                          encoding='utf-8')

    def get_audio_path(self, crc: int) -> Path:
        """DEPRECATED want to stream eventually"""

        metadata = self.get_metadata()
        if crc not in metadata.audios:
            raise ValueError(f"Audio with crc {crc} does not exist.")
        
        return self.app_audio_dir / f"{crc}.m4a"