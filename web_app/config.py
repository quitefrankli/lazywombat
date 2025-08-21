from pathlib import Path
from datetime import timedelta


class ConfigManager:
    _instance = None  # Class-level variable to store the single instance

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            # If no instance exists, create a new one
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __init__ will be called every time, even for existing instances,
        # but the configuration loading logic should only run once.
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.use_offline_syncer = True
        self.debug_mode = False
        self.tudio_search_prefix = "music " # helps narrow down search results
        self.tudio_max_results = 10
        self.tudio_max_video_length = timedelta(minutes=10)

    @property
    def project_name(self) -> str:
        return "lazywombat" if not self.debug_mode else "lazywombat_debug"

    @property
    def save_data_path(self) -> Path:
        return Path.home() / f".{self.project_name}" / "data"
    
    @property
    def tubio_cookie_path(self) -> Path:
        return self.save_data_path / "cookies.txt"
