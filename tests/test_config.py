import pytest

from pcagent.config import AppConfig, ConfigError, load_config


def write(tmp_path, text, name="pcagent.yaml"):
    f = tmp_path / name
    f.write_text(text)
    return f


def test_load_minimal(tmp_path, monkeypatch):
    monkeypatch.delenv("PCAGENT_MODEL", raising=False)
    cfg = load_config(write(tmp_path, "packs:\n  - use: demo\n"))
    assert isinstance(cfg, AppConfig)
    assert cfg.packs[0].use == "demo" and cfg.packs[0].settings == {}
    assert cfg.model == "anthropic/claude-opus-5-5"
    assert cfg.judge.type == "jev"
    assert cfg.tracing.exporter == "none" and cfg.tracing.capture_content is False


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("PAS_URL", "https://pas.example/mcp")
    cfg = load_config(write(tmp_path, "packs:\n  - use: x\n    settings: {url: '${PAS_URL}'}\n"))
    assert cfg.packs[0].settings["url"] == "https://pas.example/mcp"


def test_unset_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(ConfigError, match="NOPE_NOT_SET"):
        load_config(write(tmp_path, "packs: []\ncheckpointer: ${NOPE_NOT_SET}\n"))


def test_servers_yaml_migration_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PCAGENT_CONFIG", raising=False)
    (tmp_path / "servers.yaml").write_text("servers: {}\n")
    with pytest.raises(ConfigError) as e:
        load_config()
    assert "pcagent.yaml" in str(e.value) and "servers.yaml" in str(e.value)


def test_pcagent_config_env_picks_file(tmp_path, monkeypatch):
    f = write(tmp_path, "packs: []\nmodel: openai/gpt-x\n", "other.yaml")
    monkeypatch.setenv("PCAGENT_CONFIG", str(f))
    monkeypatch.delenv("PCAGENT_MODEL", raising=False)
    assert load_config().model == "openai/gpt-x"


def test_pcagent_model_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PCAGENT_MODEL", "ollama/x")
    assert load_config(write(tmp_path, "packs: []\nmodel: a/b\n")).model == "ollama/x"


def test_empty_checkpointer_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPTY_DB", "")
    assert load_config(write(tmp_path, "packs: []\ncheckpointer: '${EMPTY_DB}'\n")).checkpointer is None
