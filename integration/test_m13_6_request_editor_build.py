from pathlib import Path

from integration.m13_6_request_editor_build import write_request


def test_editor_build_request_is_atomic_and_explicit(tmp_path):
    path = write_request(tmp_path / "request.json")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "m13_6_one_shot_editor_build" in text
    assert "BCI_M9_2.apk" in text
    assert not (tmp_path / "request.json.tmp").exists()
