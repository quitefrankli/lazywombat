from pathlib import Path


class ConfigManager:
    _instance = None  # Class-level variable to store the single instance
    PROJECT_NAME = "lazywombat"
    PROJECT_LOCAL_SAVE_DIRECTORY = Path.home() / f".{PROJECT_NAME}" / "data"

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