from __future__ import annotations

import msal
import requests


def send(config: dict, recipient: str, subject: str, body: str) -> None:
    graph_cfg = config["graph"]
    app = msal.ConfidentialClientApplication(
        client_id=graph_cfg["client_id"],
        client_credential=graph_cfg["client_secret"],
        authority=f"https://login.microsoftonline.com/{graph_cfg['tenant_id']}",
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"MSAL token acquisition failed: {result.get('error_description', result)}"
        )

    sender = graph_cfg["sender_address"]
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
    headers = {
        "Authorization": f"Bearer {result['access_token']}",
        "Content-Type": "application/json",
    }
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        }
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
