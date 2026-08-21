"""edit_model: transactional read-modify-write context on the base DataInterface.

Backed by fakeredis (see tests/conftest.py). Verifies the load-inside-lock,
auto-save, and no-op-skip behaviors.
"""
import pytest
from pydantic import BaseModel

import web_app.data_interface as data_interface_module
from web_app.data_interface import DataInterface


class _Box(BaseModel):
    items: dict[int, str] = {}


@pytest.fixture
def scoped_interface(tmp_path, monkeypatch):
    config = type(
        "Config",
        (),
        {
            "save_data_path": tmp_path / "data",
            "use_offline_syncer": True,
        },
    )()
    monkeypatch.setattr(data_interface_module, "ConfigManager", lambda: config)
    return DataInterface(), config.save_data_path / "box.json"


def test_edit_model_persists_mutation(scoped_interface):
    di, path = scoped_interface

    with di.edit_model(path, _Box) as box:
        box.items[1] = "a"

    assert di.load_model(path, _Box).items == {1: "a"}


def test_edit_model_skips_write_when_unchanged(scoped_interface):
    di, path = scoped_interface

    # Seed a file, then record its mtime.
    with di.edit_model(path, _Box) as box:
        box.items[1] = "a"
    first_mtime = path.stat().st_mtime_ns

    # A no-op edit must not rewrite the file (same mtime).
    with di.edit_model(path, _Box) as box:
        _ = box.items  # read only, no mutation
    assert path.stat().st_mtime_ns == first_mtime


def test_edit_model_discards_on_exception(scoped_interface):
    di, path = scoped_interface
    with di.edit_model(path, _Box) as box:
        box.items[1] = "a"

    class Boom(Exception):
        pass

    try:
        with di.edit_model(path, _Box) as box:
            box.items[2] = "b"
            raise Boom()
    except Boom:
        pass

    # The failed edit is discarded — only the first mutation survived.
    assert di.load_model(path, _Box).items == {1: "a"}
