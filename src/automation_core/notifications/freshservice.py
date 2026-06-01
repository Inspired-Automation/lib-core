from __future__ import annotations

import requests


def create_ticket(
    config: dict, subject: str, body_html: str, *, is_critical: bool
) -> None:
    fs_cfg = config["freshservice"]
    domain: str = fs_cfg["domain"]
    api_key: str = fs_cfg["api_key"]

    url = f"https://{domain}/api/v2/tickets"
    # Priority: 2 = High (critical), 4 = Low (summary)
    priority = 2 if is_critical else 4
    payload = {
        "subject": subject,
        "description": body_html,
        "email": config.get("notifications", {}).get("default_recipient", ""),
        "priority": priority,
        "status": 2,  # Open
    }
    response = requests.post(
        url,
        auth=(api_key, "X"),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
