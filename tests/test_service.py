import json

from openajazz import service, state


def test_music_argv_round_trips_options():
    argv = service.music_argv("pulse", "ff0000", 1.5, 24, "/dev/hidraw7", dry_run=True)
    assert argv[-10:] == ["music", "--quiet", "--style", "pulse", "--sensitivity", "1.5",
                          "--fps", "24", "--color", "ff0000"]
    assert "--device" in argv and "-n" in argv


def test_unit_file_runs_quiet_music_and_restarts():
    text = service.unit_text(["/usr/bin/openajazz", "music", "--quiet", "--style", "wave"])
    assert "ExecStart=/usr/bin/openajazz music --quiet --style wave" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=graphical-session.target" in text


def test_no_background_run_without_pid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "music.pid")
    assert service.running_pid() is None
    (tmp_path / "music.pid").write_text("999999999")  # stale pid
    assert service.running_pid() is None
    assert not (tmp_path / "music.pid").exists()


def test_settings_migrate_from_old_name(tmp_path, monkeypatch):
    old, new = tmp_path / "ak820ctl", tmp_path / "openajazz"
    (old / "presets").mkdir(parents=True)
    (old / "state.json").write_text(json.dumps({"remaps": {"0:57": 41}}))
    (old / "presets" / "mine.json").write_text("{}")
    monkeypatch.setattr(state, "OLD_CONFIG_DIR", old)
    monkeypatch.setattr(state, "CONFIG_DIR", new)
    monkeypatch.setattr(state, "STATE_FILE", new / "state.json")
    assert state.load()["remaps"] == {"0:57": 41}
    assert (new / "presets" / "mine.json").exists()


def test_missing_numpy_says_how_to_install(monkeypatch):
    import sys
    import pytest
    from openajazz import music
    monkeypatch.setitem(sys.modules, "numpy", None)  # import numpy -> ImportError
    with pytest.raises(music.AudioError) as e:
        music.Analyzer()
    assert "uv tool install --upgrade 'openajazz[music]'" in str(e.value)
    assert "pipx" in str(e.value)
