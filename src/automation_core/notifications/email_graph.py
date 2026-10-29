from __future__ import annotations

from ..email.graph import send_email


def send(
    config: dict, recipient: str, subject: str, html: str, *, importance: str = "normal"
) -> None:
    """Send an error notification. A thin wrapper so dispatch and its tests
    have one patch point; the Graph work is in ``automation_core.email.graph``."""
    send_email(subject, html, recipient, importance=importance, config=config)
