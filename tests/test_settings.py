import json

import pytest
from fastapi import HTTPException


@pytest.fixture
def settings_files(tmp_path, monkeypatch):
    from api.routes import config
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "SECRETS_PATH", tmp_path / "secrets.yaml")
    monkeypatch.setattr(config, "configuration_is_locked", lambda: False)
    monkeypatch.setattr(config, "load_mcp_config", lambda: {"llm_provider": "openai", "openai": {"default_model": "deepseek-flash"}})
    monkeypatch.setattr(config, "load_secrets", lambda: {"openai": {"api_key": "saved-private-test-key", "base_url": "https://api.deepseek.com"}})
    return config


@pytest.mark.asyncio
async def test_settings_returns_key_presence_without_credentials(settings_files, monkeypatch):
    config = settings_files
    monkeypatch.setattr(config, "get_llm_provider", lambda: "openai")
    monkeypatch.setattr(config, "get_llm_models", lambda provider=None: {"default": "deepseek-flash" if provider in (None, "openai") else "other-model"})
    response = await config.get_settings()
    assert response.api_key_configured == {"openai": True, "google": False, "anthropic": False}
    assert response.provider_models["google"]["default"] == "other-model"
    assert "saved-private-test-key" not in json.dumps(response.model_dump())


@pytest.mark.asyncio
async def test_changing_api_host_does_not_reuse_saved_key(settings_files):
    from models.requests import LLMConfigUpdateRequest
    config = settings_files
    with pytest.raises(HTTPException) as error:
        await config.set_llm_config(LLMConfigUpdateRequest(provider="openai", default_model="model", base_url="https://other.example/v1"))
    assert error.value.status_code == 400
    assert "new API host" in error.value.detail
    assert not config.CONFIG_PATH.exists() and not config.SECRETS_PATH.exists()


@pytest.mark.asyncio
async def test_same_host_api_path_keeps_saved_key(settings_files):
    import yaml
    from models.requests import LLMConfigUpdateRequest
    config = settings_files
    await config.set_llm_config(LLMConfigUpdateRequest(provider="openai", default_model="deepseek-flash", base_url="https://api.deepseek.com/v1"))
    saved = yaml.safe_load(config.SECRETS_PATH.read_text())
    assert saved["openai"]["api_key"] == "saved-private-test-key"
    assert saved["openai"]["base_url"] == "https://api.deepseek.com/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"default_model": " "},
    {"base_url": "not-a-url"},
    {"base_url": "https://user:password@api.deepseek.com"},
    {"provider": "anthropic", "base_url": ""},
])
async def test_invalid_settings_are_rejected_before_writing(settings_files, changes):
    from models.requests import LLMConfigUpdateRequest
    data = {"provider": "openai", "default_model": "deepseek-flash", "base_url": "https://api.deepseek.com", **changes}
    with pytest.raises(HTTPException) as error:
        await settings_files.set_llm_config(LLMConfigUpdateRequest(**data))
    assert error.value.status_code == 400
    assert not settings_files.CONFIG_PATH.exists() and not settings_files.SECRETS_PATH.exists()


@pytest.mark.asyncio
async def test_active_run_locks_configuration(settings_files, monkeypatch):
    from models.requests import LLMConfigUpdateRequest
    monkeypatch.setattr(settings_files, "configuration_is_locked", lambda: True)
    with pytest.raises(HTTPException) as error:
        await settings_files.set_llm_config(LLMConfigUpdateRequest(provider="openai", default_model="deepseek-flash"))
    assert error.value.status_code == 409
    assert not settings_files.CONFIG_PATH.exists()
