from __future__ import annotations

import requests

from ..config import ConfigurationError

_REQUIRED_DEFAULT_FIELDS = (
    "workspace_id",
    "group_id",
    "requester_email",
    "type",
    "tags",
)


def normalize_base_url(domain: str) -> str:
    """Return the canonical Freshservice API base URL for a configured domain.

    Accepts any reasonable input format and collapses it to
    ``https://<host>/api/v2``. Strips leading/trailing whitespace, a leading
    ``http(s)://`` scheme, trailing slashes, and a trailing ``/api/v2``.
    """
    cleaned = domain.strip()
    lowered = cleaned.lower()
    if lowered.startswith("https://"):
        cleaned = cleaned[len("https://") :]
    elif lowered.startswith("http://"):
        cleaned = cleaned[len("http://") :]
    cleaned = cleaned.rstrip("/")
    if cleaned.lower().endswith("/api/v2"):
        cleaned = cleaned[: -len("/api/v2")].rstrip("/")
    return f"https://{cleaned}/api/v2"


def validate_defaults(config: dict) -> dict:
    """Validate and return the required ``freshservice.defaults`` block.

    Raises :class:`ConfigurationError` if the block is missing, or if any of
    the required fields (``workspace_id``, ``group_id``, ``requester_email``,
    ``type``, ``tags``) is missing or empty.
    """
    defaults = (config.get("freshservice") or {}).get("defaults")
    if not defaults:
        raise ConfigurationError(
            "Freshservice notifications require a 'freshservice.defaults' block in "
            "team.yaml with workspace_id, group_id, requester_email, type, and tags."
        )
    for field in _REQUIRED_DEFAULT_FIELDS:
        value = defaults.get(field)
        if value is None or (isinstance(value, (str, list)) and len(value) == 0):
            raise ConfigurationError(
                f"Freshservice 'freshservice.defaults' is missing required field: '{field}'."
            )
    return defaults


def create_ticket(
    config: dict, subject: str, body_html: str, *, is_critical: bool
) -> None:
    fs_cfg = config["freshservice"]
    domain: str = fs_cfg["domain"]
    api_key: str = fs_cfg["api_key"]
    defaults: dict = fs_cfg["defaults"]

    url = f"{normalize_base_url(domain)}/tickets"
    # Priority: 2 = High (critical), 4 = Low (summary)
    priority = 2 if is_critical else 4
    # Copy so repeated dispatches never mutate the configured tag list.
    tags = [*defaults["tags"], "critical" if is_critical else "summary"]
    payload = {
        "subject": subject,
        "description": body_html,
        "email": defaults["requester_email"],
        "priority": priority,
        "status": 2,  # Open
        "workspace_id": defaults["workspace_id"],
        "group_id": defaults["group_id"],
        "type": defaults["type"],
        "tags": tags,
    }
    response = requests.post(
        url,
        auth=(api_key, "X"),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
