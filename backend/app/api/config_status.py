"""
Config-status endpoint.

Exposes which groups of environment-driven configuration are fully
populated. The frontend uses this to show "configuration missing" banners
for features that depend on secrets which aren't provided yet (the RAG
stack, SMTP, speech). A group is only reported as configured (`true`) when
every variable it needs is set to a non-empty value.

The `rag` group (document upload + chat) is not a simple "all of these
vars are set" check like `smtp`/`speech` below - embeddings are always
Azure OpenAI (`AZURE_EM_*`), but the CHAT half depends on which provider is
selected via `LLM_PROVIDER` ("groq", the default, or "azure"): `rag` is
`true` only when embeddings are configured AND the *currently selected*
chat provider's own vars are set (`GROQ_API_KEY` for "groq", or
`LLM_ENDPOINT`/`LLM_ENDPOINT_APIKEY`/`LLM_MODEL_NAME` for "azure") - not
both providers' credentials at once. See app/engine/llm_provider.py.
"""

from fastapi import APIRouter

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/api/config", tags=["config"])


def _all_set(settings: Settings, var_names: list[str]) -> bool:
    """Return True only if every named setting is a non-empty value."""
    for name in var_names:
        value = getattr(settings, name, None)
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
    return True


def _normalized_llm_provider(settings: Settings) -> str:
    value = (settings.LLM_PROVIDER or "").strip().lower()
    return "azure" if value == "azure" else "groq"


def _rag_configured(settings: Settings) -> bool:
    embeddings_ok = _all_set(
        settings, ["AZURE_EM_ENDPOINT", "AZURE_EM_API_KEY", "AZURE_EM_API_VERSION", "AZURE_EM_MODEL"]
    )
    if not embeddings_ok:
        return False
    if _normalized_llm_provider(settings) == "azure":
        return _all_set(settings, ["LLM_ENDPOINT", "LLM_ENDPOINT_APIKEY", "LLM_MODEL_NAME"])
    return _all_set(settings, ["GROQ_API_KEY"])


# Group -> the settings attributes that must ALL be non-empty for the
# group to be considered configured. `rag` is handled separately above
# since it isn't a plain "all of these" check (see module docstring).
CONFIG_GROUPS: dict[str, list[str]] = {
    "smtp": [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
    ],
    "speech": ["GROQ_API_KEY"],
}


@router.get("/status")
def get_config_status() -> dict[str, bool | str]:
    """Return which configuration groups are fully populated, plus which
    chat provider is currently active.

    Example response:
        {"rag": true, "smtp": false, "speech": true, "llm_provider": "groq"}
    """
    settings = get_settings()
    result: dict[str, bool | str] = {
        "rag": _rag_configured(settings),
        **{group: _all_set(settings, var_names) for group, var_names in CONFIG_GROUPS.items()},
        "llm_provider": _normalized_llm_provider(settings),
    }
    return result
