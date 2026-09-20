"""Credentials come from xi-tools' .env (XI_DB_*) only; the server's network.lua is
never read for them (design2-overrides O3). The zone editor's explicit network.lua
pre-fill still works."""
from pathlib import Path

import pytest

from xi.server import xi_commands as sc


def _network_lua(root: Path, password="s3cr3t-sentinel") -> Path:
    p = root / "settings" / "network.lua"
    p.parent.mkdir(parents=True)
    p.write_text(f"xi.settings.network = {{\n  SQL_HOST = '10.9.8.7',\n  SQL_PORT = 3399,\n"
                 f"  SQL_LOGIN = \"lua-user\",\n  SQL_PASSWORD = \"{password}\",\n  SQL_DATABASE = \"luadb\",\n}}\n",
                 encoding="utf-8")
    return root


@pytest.fixture
def server_dir(tmp_path, monkeypatch):
    import xi.xi_config as cfg
    d = _network_lua(tmp_path / "srv")
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", str(d))
    return d


def test_resolve_never_reads_network_lua(server_dir):
    assert sc._resolve(None, None, None, None, None) == ("127.0.0.1", 3306, "root", "", "xidb")
    rc = sc.resolved_creds()
    assert rc["source"] == "default" and rc["hasOverride"] is False
    assert rc["overriddenFields"] == [] and rc["luaPath"] is None
    assert "password" not in rc
    assert sc.db_configured() is False


def test_env_keys_win_and_blanks_are_ignored(server_dir, monkeypatch):
    monkeypatch.setenv("XI_DB_HOST", " 192.168.0.5 ")
    monkeypatch.setenv("XI_DB_PORT", "3307")
    monkeypatch.setenv("XI_DB_USER", "")
    monkeypatch.setenv("XI_DB_PASSWORD", "pw")
    assert sc._resolve(None, None, None, None, None) == ("192.168.0.5", 3307, "root", "pw", "xidb")
    assert sc._resolve("h", 1, "u", "p", "d") == ("h", 1, "u", "p", "d")      # explicit args last
    rc = sc.resolved_creds()
    assert rc["source"] == "env" and rc["hasOverride"] is True
    assert rc["overriddenFields"] == ["host", "password", "port"]
    assert sc.field_sources() == {"host": "env", "port": "env", "user": "default",
                                  "password": "env", "database": "default"}
    assert sc.db_configured() is True


def test_configured_needs_host_user_or_database(monkeypatch):
    monkeypatch.setenv("XI_DB_PASSWORD", "pw")
    monkeypatch.setenv("XI_DB_PORT", "3307")
    assert sc.db_configured() is False              # a port or password alone is not a server
    assert sc.resolved_creds()["source"] == "env"
    monkeypatch.setenv("XI_DB_NAME", "xidb")
    assert sc.db_configured() is True


def test_xi_config_db_creds_agrees(monkeypatch):
    import xi.xi_config as cfg
    assert cfg.db_creds() == dict(host="127.0.0.1", port=3306, user="root", password="", database="xidb")
    monkeypatch.setenv("XI_DB_USER", "xi")
    monkeypatch.setenv("XI_DB_PORT", "not-a-number")
    h, p, u, pw, db = sc._resolve(None, None, None, None, None)
    assert cfg.db_creds() == dict(host=h, port=p, user=u, password=pw, database=db) == dict(
        host="127.0.0.1", port=3306, user="xi", password="", database="xidb")


def test_apply_env_overrides_refreshes_and_clears(monkeypatch):
    import xi.xi_config as cfg
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", None)
    try:
        cfg.apply_env_overrides({"XI_DB_HOST": "10.0.0.2", "XI_DB_PASSWORD": "x"})
        assert (cfg.DB_HOST, cfg.DB_PASSWORD) == ("10.0.0.2", "x")
        cfg.apply_env_overrides({"XI_DB_HOST": "", "XI_DB_PASSWORD": ""})
        assert (cfg.DB_HOST, cfg.DB_PASSWORD) == ("127.0.0.1", "")
    finally:
        cfg.apply_env_overrides({"XI_DB_HOST": "", "XI_DB_PASSWORD": ""})


def test_friendly_db_error_moved_and_bridge_uses_it():
    from xi.zone import xi_bridge
    assert xi_bridge._friendly_db_error is sc.friendly_db_error
    assert sc.friendly_db_error(Exception("(2003, \"Can't connect to MySQL server on '127.0.0.1'\")")) == \
        "Could not reach the server — is it running, and are the host and port right?"
    assert "Wrong username or password" in sc.friendly_db_error(Exception("auth_gssapi_client not configured"))


def test_zone_editor_status_calls_the_env_login_override(tmp_path, monkeypatch):
    """The zone editor's setup wizard knows "network.lua" / "override" only, and gates its
    player spawn marker on "override": env.status hands it that for XI_DB_*; `xi server
    check` and resolved_creds() keep "env"."""
    from xi.zone import xi_bridge
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")                      # never the real xi-tools .env
    monkeypatch.setenv("XI_ENV_FILE", str(env))
    st = xi_bridge._env_status({})
    assert st["db"]["source"] == "default" and st["db"]["hasOverride"] is False
    monkeypatch.setenv("XI_DB_HOST", "192.168.0.5")
    st = xi_bridge._env_status({})
    assert st["db"]["source"] == "override" and st["db"]["hasOverride"] is True
    assert st["db"]["host"] == "192.168.0.5" and "password" not in st["db"]
    assert sc.resolved_creds()["source"] == "env"


def test_zone_editor_prefill_still_reads_network_lua(tmp_path):
    from xi.zone import xi_bridge
    d = _network_lua(tmp_path / "srv", password="pf")
    out = xi_bridge._env_server_creds({"path": str(d)})
    assert out["ok"] and out["values"]["XI_DB_HOST"] == "10.9.8.7" and out["values"]["XI_DB_PORT"] == "3399"
