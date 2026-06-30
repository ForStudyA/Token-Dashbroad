from __future__ import annotations

import json
import sqlite3

import yaml

from hermes_token_dash.adapters.hermes import HermesAdapter


def _write_config(path, *, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {
                "model": {
                    "provider": "deepseek",
                    "default": "deepseek-v4-flash",
                    "base_url": base_url,
                },
                "custom_providers": [
                    {"name": "deepseek", "base_url": base_url},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _read_config(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_set_proxy_url_updates_profile_configs_and_env(monkeypatch, tmp_path):
    from hermes_token_dash.adapters import hermes as hermes_mod

    home = tmp_path / "hermes"
    root_cfg = home / "config.yaml"
    profile_cfg = home / "profiles" / "coding" / "config.yaml"
    _write_config(root_cfg, base_url="https://api.deepseek.com")
    _write_config(profile_cfg, base_url="https://profile.example/v1")

    monkeypatch.setattr(hermes_mod, "HERMES_HOMES", [home])
    monkeypatch.setattr(hermes_mod, "ORIGINALS_PATH", tmp_path / "originals.json")

    adapter = HermesAdapter()

    assert adapter.set_proxy_url("http://127.0.0.1:8765/v1") is True

    for cfg_path in (root_cfg, profile_cfg):
        cfg = _read_config(cfg_path)
        assert cfg["model"]["base_url"] == "http://127.0.0.1:8765/v1"
        assert cfg["custom_providers"][0]["base_url"] == "http://127.0.0.1:8765/v1"
        env_text = (cfg_path.parent / ".env").read_text(encoding="utf-8")
        assert "DEEPSEEK_BASE_URL=http://127.0.0.1:8765/v1" in env_text
        assert "XIAOMI_BASE_URL=http://127.0.0.1:8765/v1" in env_text

    saved = json.loads((tmp_path / "originals.json").read_text(encoding="utf-8"))
    assert str(root_cfg) in saved["configs"]
    assert str(profile_cfg) in saved["configs"]


def test_restore_original_removes_proxy_env_vars(monkeypatch, tmp_path):
    from hermes_token_dash.adapters import hermes as hermes_mod

    home = tmp_path / "hermes"
    cfg_path = home / "config.yaml"
    _write_config(cfg_path, base_url="http://127.0.0.1:8765/v1")
    (home / ".env").write_text(
        "DEEPSEEK_BASE_URL=http://127.0.0.1:8765/v1\n"
        "XIAOMI_BASE_URL=http://127.0.0.1:8765/v1\n"
        "OTHER=value\n",
        encoding="utf-8",
    )
    originals_path = tmp_path / "originals.json"
    originals_path.write_text(
        json.dumps(
            {
                "configs": {
                    str(cfg_path): {
                        "custom_providers": {"deepseek": "https://api.deepseek.com"},
                        "model_base_url": "https://api.deepseek.com",
                    }
                },
                "env": {str(home / ".env"): {}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_mod, "HERMES_HOMES", [home])
    monkeypatch.setattr(hermes_mod, "ORIGINALS_PATH", originals_path)

    adapter = HermesAdapter()

    assert adapter.restore_original() is True

    cfg = _read_config(cfg_path)
    assert cfg["model"]["base_url"] == "https://api.deepseek.com"
    assert cfg["custom_providers"][0]["base_url"] == "https://api.deepseek.com"
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_BASE_URL" not in env_text
    assert "XIAOMI_BASE_URL" not in env_text
    assert "OTHER=value" in env_text


def test_set_proxy_url_updates_persisted_session_base_urls(monkeypatch, tmp_path):
    from hermes_token_dash.adapters import hermes as hermes_mod

    home = tmp_path / "hermes"
    _write_config(home / "config.yaml", base_url="https://api.deepseek.com")
    db_path = home / "state.db"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE sessions (id TEXT, billing_base_url TEXT)")
        con.execute(
            "INSERT INTO sessions (id, billing_base_url) VALUES (?, ?)",
            ("s1", "https://api.deepseek.com/v1"),
        )
        con.execute(
            "INSERT INTO sessions (id, billing_base_url) VALUES (?, ?)",
            ("s2", ""),
        )

    monkeypatch.setattr(hermes_mod, "HERMES_HOMES", [home])
    monkeypatch.setattr(hermes_mod, "ORIGINALS_PATH", tmp_path / "originals.json")

    adapter = HermesAdapter()

    assert adapter.set_proxy_url("http://127.0.0.1:8765/v1") is True

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT id, billing_base_url FROM sessions ORDER BY id"
        ).fetchall()
    assert rows == [
        ("s1", "http://127.0.0.1:8765/v1"),
        ("s2", ""),
    ]

    saved = json.loads((tmp_path / "originals.json").read_text(encoding="utf-8"))
    state_originals = saved["state_dbs"][str(db_path)]
    assert list(state_originals.values()) == ["https://api.deepseek.com/v1"]


def test_restore_original_restores_persisted_session_base_urls(monkeypatch, tmp_path):
    from hermes_token_dash.adapters import hermes as hermes_mod

    home = tmp_path / "hermes"
    _write_config(home / "config.yaml", base_url="http://127.0.0.1:8765/v1")
    db_path = home / "state.db"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE sessions (id TEXT, billing_base_url TEXT)")
        con.execute(
            "INSERT INTO sessions (id, billing_base_url) VALUES (?, ?)",
            ("s1", "http://127.0.0.1:8765/v1"),
        )
    originals_path = tmp_path / "originals.json"
    originals_path.write_text(
        json.dumps(
            {
                "configs": {},
                "env": {},
                "state_dbs": {str(db_path): {"1": "https://api.deepseek.com/v1"}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(hermes_mod, "HERMES_HOMES", [home])
    monkeypatch.setattr(hermes_mod, "ORIGINALS_PATH", originals_path)

    adapter = HermesAdapter()

    assert adapter.restore_original() is True

    with sqlite3.connect(db_path) as con:
        base_url = con.execute("SELECT billing_base_url FROM sessions").fetchone()[0]
    assert base_url == "https://api.deepseek.com/v1"
