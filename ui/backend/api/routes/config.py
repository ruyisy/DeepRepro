"""
Configuration API Routes
Handles LLM provider and settings management
"""

from fastapi import APIRouter, HTTPException
import yaml
from urllib.parse import urlparse
from utils.checkpoints import atomic_write

from settings import (
    load_mcp_config,
    load_secrets,
    get_llm_provider,
    get_llm_models,
    is_indexing_enabled,
    CONFIG_PATH,
    SECRETS_PATH,
)
from models.requests import LLMProviderUpdateRequest, LLMConfigUpdateRequest
from models.responses import ConfigResponse, SettingsResponse


router = APIRouter()


def configuration_is_locked():
    from services.workflow_service import workflow_service
    return bool(workflow_service.get_active_tasks())


def check_configuration_unlocked():
    if configuration_is_locked():
        raise HTTPException(status_code=409, detail="Pause the running task before changing model settings.")


def api_origin(provider, base_url):
    defaults = {"openai": "https://api.openai.com", "anthropic": "https://api.anthropic.com", "google": "https://generativelanguage.googleapis.com"}
    parsed = urlparse(base_url or defaults[provider])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(status_code=400, detail="Enter an HTTP(S) API base URL without credentials, query parameters or a fragment.")
    try:
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise HTTPException(status_code=400, detail="The API base URL has an invalid port.")


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Get current application settings"""
    config = load_mcp_config()
    provider = get_llm_provider()
    models = get_llm_models(provider)
    secrets = load_secrets()
    api_base_urls = {
        name: str(provider_config.get("base_url", ""))
        for name, provider_config in secrets.items()
        if isinstance(provider_config, dict)
    }

    return SettingsResponse(
        llm_provider=provider,
        models=models,
        indexing_enabled=is_indexing_enabled(),
        document_segmentation=config.get("document_segmentation", {}),
        api_base_urls=api_base_urls,
        providers=["google", "anthropic", "openai"],
        api_key_configured={name: bool(secrets.get(name, {}).get("api_key")) for name in ("openai", "google", "anthropic")},
        provider_models={name: get_llm_models(name) for name in ("openai", "google", "anthropic")},
        configuration_locked=configuration_is_locked(),
    )


@router.get("/llm-providers", response_model=ConfigResponse)
async def get_llm_providers():
    """Get available LLM providers and their configurations"""
    secrets = load_secrets()

    # Get available providers (those with API keys configured)
    available_providers = []
    for provider in ["google", "anthropic", "openai"]:
        if secrets.get(provider, {}).get("api_key"):
            available_providers.append(provider)

    current_provider = get_llm_provider()
    models = get_llm_models(current_provider)

    return ConfigResponse(
        llm_provider=current_provider,
        available_providers=available_providers,
        models=models,
        indexing_enabled=is_indexing_enabled(),
    )


@router.put("/llm-provider")
async def set_llm_provider(request: LLMProviderUpdateRequest):
    """Update the preferred LLM provider"""
    check_configuration_unlocked()
    secrets = load_secrets()

    # Verify provider has an API key
    if not secrets.get(request.provider, {}).get("api_key"):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{request.provider}' does not have an API key configured",
        )

    # Update config file
    try:
        config = load_mcp_config()
        config["llm_provider"] = request.provider

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False)

        return {
            "status": "success",
            "message": f"LLM provider updated to '{request.provider}'",
            "provider": request.provider,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update configuration: {str(e)}",
        )


@router.put("/llm-config")
async def set_llm_config(request: LLMConfigUpdateRequest):
    """Update the preferred LLM provider and model configuration."""
    try:
        check_configuration_unlocked()
        config = load_mcp_config()
        secrets = load_secrets()

        if request.provider not in {"google", "anthropic", "openai"}:
            raise HTTPException(status_code=400, detail="Unsupported provider")

        saved = secrets.get(request.provider, {})
        if not request.default_model.strip():
            raise HTTPException(status_code=400, detail="Enter a default model ID.")
        if not (request.api_key.strip() or saved.get("api_key")):
            raise HTTPException(status_code=400, detail="Enter an API key for this connection.")
        new_origin = api_origin(request.provider, request.base_url.strip())
        if saved.get("api_key") and not request.api_key.strip() and new_origin != api_origin(request.provider, saved.get("base_url", "")):
            raise HTTPException(status_code=400, detail="The API address changed. Enter a key for the new API host; the saved key will not be reused automatically.")

        config["llm_provider"] = request.provider
        provider_config = config.setdefault(request.provider, {})

        if request.default_model.strip():
            provider_config["default_model"] = request.default_model.strip()
        if request.planning_model.strip():
            provider_config["planning_model"] = request.planning_model.strip()
        if request.subplan_model.strip():
            provider_config["subplan_model"] = request.subplan_model.strip()
        if request.implementation_model.strip():
            provider_config["implementation_model"] = request.implementation_model.strip()

        if request.base_url.strip():
            secrets.setdefault(request.provider, {})["base_url"] = request.base_url.strip()
        elif request.provider in secrets and "base_url" in secrets[request.provider]:
            secrets[request.provider].pop("base_url", None)

        if request.api_key.strip():
            secrets.setdefault(request.provider, {})["api_key"] = request.api_key.strip()
        # The UI deliberately never reads credentials back. A blank field means
        # keep the saved key, as promised by its placeholder.

        atomic_write(SECRETS_PATH, yaml.safe_dump(secrets, allow_unicode=True, sort_keys=False))
        SECRETS_PATH.chmod(0o600)
        atomic_write(CONFIG_PATH, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))

        return {
            "status": "success",
            "message": f"LLM configuration updated to '{request.provider}'",
            "provider": request.provider,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update LLM configuration: {str(e)}",
        )
