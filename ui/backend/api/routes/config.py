"""
Configuration API Routes
Handles LLM provider and settings management
"""

from fastapi import APIRouter, HTTPException
import yaml

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
        config = load_mcp_config()
        secrets = load_secrets()

        if request.provider not in {"google", "anthropic", "openai"}:
            raise HTTPException(status_code=400, detail="Unsupported provider")

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
        elif request.provider in secrets and "api_key" in secrets[request.provider]:
            secrets[request.provider].pop("api_key", None)

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

        with open(SECRETS_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(secrets, f, allow_unicode=True, sort_keys=False)

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
