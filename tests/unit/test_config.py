from web_app.config import ConfigManager


def test_app_config_sections_expose_grouped_values():
    cfg = ConfigManager()

    assert cfg.loft.gallery_video_max_height_px == 720
    assert cfg.tubio.max_search_pages == 3


def test_app_config_sections_are_mutable():
    cfg = ConfigManager()
    original = cfg.loft.gallery_image_max_retries

    try:
        cfg.loft.gallery_image_max_retries = 7

        assert cfg.loft.gallery_image_max_retries == 7
    finally:
        cfg.loft.gallery_image_max_retries = original


def test_log_file_path_tracks_active_data_root(tmp_path):
    cfg = ConfigManager()
    previous_debug_mode = cfg.debug_mode
    previous_production_root = cfg.production_data_root
    previous_debug_root = cfg.debug_data_root

    try:
        cfg.production_data_root = tmp_path / "production-data"
        cfg.debug_data_root = tmp_path / "debug-data"

        cfg.debug_mode = False
        assert cfg.log_file_path == tmp_path / "production-data/logs/web_app.log"

        cfg.debug_mode = True
        assert cfg.log_file_path == tmp_path / "debug-data/logs/web_app.log"
    finally:
        cfg.debug_mode = previous_debug_mode
        cfg.production_data_root = previous_production_root
        cfg.debug_data_root = previous_debug_root
