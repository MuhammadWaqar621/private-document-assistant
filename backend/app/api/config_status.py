"""
Config-status endpoint.

Exposes which groups of environment-driven configuration are fully
populated. The frontend uses this to show "configuration missing" banners
for features that depend on secrets which aren't provided yet (Azure
OpenAI, Google OAuth, SMTP). A group is only reported as configured
(`true`) when every variable in that group is set to a non-empty value.
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


# Group -> the settings attributes that must ALL be non-empty for the
# group to be considered configured.
CONFIG_GROUPS: dict[str, list[str]] = {
    "azure_ai": [
        "AZURE_EM_ENDPOINT",
        "AZURE_EM_API_KEY",
        "AZURE_EM_API_VERSION",
        "AZURE_EM_MODEL",
        "LLM_ENDPOINT_MINI_MODEL",
        "LLM_ENDPOINT_MINI_MODEL_APIKEY",
        "MINI_MODEL_NAME",
    ],
    "google_oauth": [
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
    ],
    "smtp": [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
    ],
}


@router.get("/status")
def get_config_status() -> dict[str, bool]:
    """Return which configuration groups are fully populated.

    Example response:
        {"azure_ai": true, "google_oauth": false, "smtp": false}
    """
    settings = get_settings()
    return {
        group: _all_set(settings, var_names)
        for group, var_names in CONFIG_GROUPS.items()
    }
