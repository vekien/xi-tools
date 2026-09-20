"""`xi server check` (design2 §2.7, O3/O4): read-only, never shows the password,
--json always exits 0."""
import json

import pytest
from click.testing import CliRunner

import _minischema as MS
import _srvtree as T
from _fakedb import COLUMNS, FakeServer
from xi.server import xi_check as C
from xi.server import xi_db_apply as D
from xi.xi_cli import cli

SENTINEL = "s3cr3t-sentinel"


@pytest.fixture(autouse=True)
def _no_tasklist(monkeypatch):
    from xi.server import xi_ws_widen as W
    monkeypatch.setattr(W, "process_running", lambda name="xi_map.exe": None)


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A tmp server folder (network.lua holds the sentinel too, and is never read)."""
    import xi.xi_config as cfg
    root = T.scripts_tree(T.ws_tree(tmp_path / "srv"))
    (root / "settings").mkdir()
    (root / "settings" / "network.lua").write_text(
        f"SQL_HOST = '10.1.1.1'\nSQL_LOGIN = 'lua'\nSQL_PASSWORD = '{SENTINEL}'\nSQL_DATABASE = 'luadb'\n",
        encoding="utf-8")
    monkeypatch.setattr(cfg, "XI_SERVER_DIR", str(root))
    return root


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("XI_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("XI_DB_USER", "xi")
    monkeypatch.setenv("XI_DB_PASSWORD", SENTINEL)


def _no_connect(monkeypatch):
    def boom():
        pytest.fail("server check connected with --no-db / no configuration")
    monkeypatch.setattr(D, "connect", boom)


def run(*args):
    r = CliRunner().invoke(cli, ["server", "check", *args])
    assert r.exit_code == 0, r.output
    return r


def test_no_db_never_connects_and_reports_sources(server, env, monkeypatch):
    _no_connect(monkeypatch)
    doc = json.loads(run("--json", "--no-db").output)
    assert doc["configured"] is True and doc["source"] == "env" and doc["ok"] is True
    assert doc["fieldSources"] == {"host": "env", "port": "default", "user": "env", "password": "env",
                                   "database": "default"}
    assert doc["overriddenFields"] == ["host", "password", "user"]
    assert (doc["host"], doc["port"], doc["user"], doc["database"]) == ("127.0.0.1", 3306, "xi", "xidb")
    assert doc["connected"] is None and doc["tables"] is None and doc["version"] is None
    assert doc["serverDir"] == str(server) and doc["serverDirValid"] is True
    assert "luaPath" not in doc and "password" not in doc
    assert doc["weaponSkills"]["column"] is None and doc["weaponSkills"]["source"]["state"] == "stock"


def test_the_password_never_appears(server, env, monkeypatch):
    _no_connect(monkeypatch)
    for args in (["--json", "--no-db"], ["--no-db"], ["--json", "--no-db", "--no-binary"]):
        assert SENTINEL not in run(*args).output
    srv = FakeServer()
    monkeypatch.setattr(D, "connect", lambda: srv.conn)
    for args in (["--json"], []):
        assert SENTINEL not in run(*args).output


def test_full_check_with_a_database(server, env, monkeypatch):
    srv = FakeServer()
    monkeypatch.setattr(D, "connect", lambda: srv.conn)
    doc = json.loads(run("--json").output)
    assert doc["ok"] is True and doc["connected"] is True and doc["error"] is None
    assert doc["version"] == "10.11.6-MariaDB" and doc["sqlMode"] == "STRICT_TRANS_TABLES"
    assert doc["tables"] == {"spell_list": {"present": True, "animation": "smallint(5) unsigned"},
                             "abilities": {"present": True, "animation": "smallint(5) unsigned"},
                             "weapon_skills": {"present": True, "animation": "tinyint(3) unsigned"}}
    ws = doc["weaponSkills"]
    assert ws["column"] == "tinyint(3) unsigned" and ws["columnWide"] is False and ws["next"] == ["patch"]
    assert srv.conn.writes == [] and srv.conn.closed


def test_a_missing_table_is_not_ok(server, env, monkeypatch):
    cols = {t: c for t, c in COLUMNS.items() if t != "abilities"}
    srv = FakeServer(columns=cols)
    monkeypatch.setattr(D, "connect", lambda: srv.conn)
    doc = C.server_check()
    assert doc["tables"]["abilities"] == {"present": False, "animation": None} and doc["ok"] is False


def test_connect_failure_is_ok_false_with_the_friendly_error(server, env, monkeypatch):
    def unreachable():
        raise D.DbUnavailable("Could not reach the server — is it running, and are the host and port right?")
    monkeypatch.setattr(D, "connect", unreachable)
    r = run("--json")
    doc = json.loads(r.output)
    assert doc["ok"] is False and doc["connected"] is False
    assert doc["error"] == "Could not reach the server — is it running, and are the host and port right?"
    text = run().output
    assert "connected:     no — Could not reach the server" in text


def test_not_configured(server, monkeypatch):
    _no_connect(monkeypatch)
    doc = json.loads(run("--json").output)
    assert doc["configured"] is False and doc["source"] == "default" and doc["ok"] is False
    assert doc["connected"] is None and doc["error"] == D.NOT_CONFIGURED
    assert set(doc["fieldSources"].values()) == {"default"}


def test_no_binary_and_no_server_dir(monkeypatch, env):
    _no_connect(monkeypatch)
    doc = json.loads(run("--json", "--no-db", "--no-binary").output)
    assert doc["serverDir"] is None and doc["serverDirValid"] is False
    ws = doc["weaponSkills"]
    assert ws["source"]["state"] == "no-server-dir" and ws["binary"]["method"] == "skipped"
    assert ws["process"] == {"running": None}


def test_json_matches_the_schema(server, env, monkeypatch):
    schema = MS.load("server_check.json")
    for ex in schema["examples"]:
        assert MS.errors(schema, ex) == [] and set(ex) == set(schema["required"])
    _no_connect(monkeypatch)
    doc = json.loads(run("--json", "--no-db").output)
    assert set(doc) == set(schema["required"]) and MS.errors(schema, doc) == []
    srv = FakeServer()
    monkeypatch.setattr(D, "connect", lambda: srv.conn)
    assert MS.errors(schema, json.loads(run("--json").output)) == []


def test_text_mode(server, env, monkeypatch):
    srv = FakeServer()
    monkeypatch.setattr(D, "connect", lambda: srv.conn)
    lines = run().output.splitlines()
    assert lines[0] == "Local server"
    assert lines[1] == f"  folder:        {server}  (scripts/actions found)"
    assert lines[2] == "  database:      xidb@127.0.0.1:3306 as xi  (host, user, password: xi-tools .env · port, database: default)"
    assert lines[3] == "  connected:     10.11.6-MariaDB · sql_mode STRICT_TRANS_TABLES"
    assert lines[-1] == "  weapon skills: stock — 0–255 only (xi server ws-widen writes the C++ patch and SQL)"
