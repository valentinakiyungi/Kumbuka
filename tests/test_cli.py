import socket

from src import cli


def test_key_is_saved_privately_and_other_settings_kept(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HISTORY_WINDOW=10\n")
    cli.write_key(env, "abc123")
    assert cli.read_env(env) == {"HISTORY_WINDOW": "10", "GEMINI_API_KEY": "abc123"}
    assert oct(env.stat().st_mode & 0o777) == "0o600"


def test_read_env_ignores_comments_and_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nGEMINI_API_KEY="xyz"\n\nBAD LINE\n')
    assert cli.read_env(env) == {"GEMINI_API_KEY": "xyz"}


def test_home_dir_respects_kumbuka_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KUMBUKA_HOME", str(tmp_path / "k"))
    assert cli.home_dir() == tmp_path / "k"


def test_free_port_skips_a_busy_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        assert cli.free_port(busy, "127.0.0.1") != busy
