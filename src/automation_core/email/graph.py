"""Sending an email through Microsoft Graph, with attachments of any size up to 150 MB.

Graph has two routes, and which one a message needs depends on its size
(spec 5). A small message goes in one ``sendMail`` call with its attachments
inline. A larger one has to be built as a draft, its big attachments uploaded
in chunks through an upload session, and the draft then sent. ``send_email``
picks the route itself, so a bot does not need to know where the line is.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Iterable

import msal
import requests

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

# Graph caps a sendMail request at about 4 MB, and base64 makes attachments a
# third bigger, so inline sending stops well short of the 3 MB in the docs.
INLINE_TOTAL_LIMIT = int(2.5 * 1024 * 1024)
# Above this a single attachment cannot be POSTed and needs an upload session.
SIMPLE_ATTACHMENT_LIMIT = 3 * 1024 * 1024
# Graph's hard limit for one attachment.
MAX_ATTACHMENT_BYTES = 150 * 1024 * 1024
# The spec says to link rather than attach above this; sending still works.
LINK_INSTEAD_BYTES = 10 * 1024 * 1024
# Upload chunks must be a multiple of 320 KiB. Ten of them is about 3.1 MiB.
UPLOAD_CHUNK_BYTES = 320 * 1024 * 10

REQUEST_TIMEOUT = 60

log = logging.getLogger(__name__)


def split_recipients(recipients: str | Iterable[str]) -> list[str]:
    """Addresses from ``"a@x.com; b@x.com"`` (``;`` or ``,``) or a list.

    Graph needs one entry per address: the whole string as a single address
    gets a 400 Bad Request.
    """
    items = [recipients] if isinstance(recipients, str) else list(recipients)
    addresses = []
    for item in items:
        addresses.extend(part.strip() for part in item.replace(",", ";").split(";"))
    return [address for address in addresses if address]


def get_access_token(graph_config: dict) -> str:
    """App-only Graph token from the ``graph`` block of team.yaml."""
    app = msal.ConfidentialClientApplication(
        client_id=graph_config["client_id"],
        client_credential=graph_config["client_secret"],
        authority=f"https://login.microsoftonline.com/{graph_config['tenant_id']}",
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"MSAL token acquisition failed: {result.get('error_description', result)}"
        )
    return result["access_token"]


def _check(response: requests.Response, action: str) -> requests.Response:
    # Graph explains a 4xx in the body; raise_for_status() alone drops it.
    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} from Graph {action}: {response.text}",
            response=response,
        )
    return response


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _file_attachment(path: Path) -> dict:
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": path.name,
        "contentType": _content_type(path),
        "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def send_email(
    subject: str,
    html: str,
    to: str | Iterable[str],
    *,
    cc: str | Iterable[str] = (),
    bcc: str | Iterable[str] = (),
    attachments: Iterable[Path] = (),
    importance: str = "normal",
    config: dict | None = None,
) -> None:
    """Send an HTML email from the team's Graph sender address.

    ``html`` is normally ``Email.render()``. ``to``, ``cc`` and ``bcc`` take a
    list or a ``;``/``,`` separated string. ``importance`` is ``"high"`` only
    for an Alert (spec 5). ``config`` defaults to ``load_config()``; pass it
    when you already hold it.

    Raises ``requests.HTTPError`` with Graph's explanation on a rejected
    request, ``RuntimeError`` if no token can be had, and ``ValueError`` for
    no recipients or an attachment over 150 MB.
    """
    if config is None:
        from ..config import load_config  # noqa: PLC0415

        config = load_config()
    graph_config = config["graph"]

    to_list = split_recipients(to)
    if not to_list:
        raise ValueError("send_email needs at least one recipient")
    if importance not in ("low", "normal", "high"):
        raise ValueError('importance must be "low", "normal" or "high"')

    files = [Path(path) for path in attachments]
    sizes = {path: path.stat().st_size for path in files}
    for path, size in sizes.items():
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"{path.name} is over Graph's 150 MB attachment limit; link to it instead")
        if size > LINK_INSTEAD_BYTES:
            log.warning(
                "automation_core.email: %s is over 10 MB; spec 6.8 says to link to it instead",
                path.name,
            )

    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html},
        "importance": importance,
        "toRecipients": [{"emailAddress": {"address": a}} for a in to_list],
    }
    if cc_list := split_recipients(cc):
        message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc_list]
    if bcc_list := split_recipients(bcc):
        message["bccRecipients"] = [{"emailAddress": {"address": a}} for a in bcc_list]

    token = get_access_token(graph_config)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    mailbox = f"{GRAPH_ROOT}/users/{graph_config['sender_address']}"

    if sum(sizes.values()) <= INLINE_TOTAL_LIMIT:
        if files:
            message["attachments"] = [_file_attachment(path) for path in files]
        _check(
            requests.post(
                f"{mailbox}/sendMail",
                headers=headers,
                json={"message": message, "saveToSentItems": True},
                timeout=REQUEST_TIMEOUT,
            ),
            "sendMail",
        )
        return

    _send_via_draft(mailbox, headers, message, files, sizes)


def _send_via_draft(
    mailbox: str, headers: dict, message: dict, files: list[Path], sizes: dict[Path, int]
) -> None:
    draft = _check(
        requests.post(f"{mailbox}/messages", headers=headers, json=message, timeout=REQUEST_TIMEOUT),
        "create draft",
    ).json()
    draft_url = f"{mailbox}/messages/{draft['id']}"
    try:
        for path in files:
            if sizes[path] < SIMPLE_ATTACHMENT_LIMIT:
                _check(
                    requests.post(
                        f"{draft_url}/attachments",
                        headers=headers,
                        json=_file_attachment(path),
                        timeout=REQUEST_TIMEOUT,
                    ),
                    f"add attachment {path.name}",
                )
            else:
                _upload_large_attachment(draft_url, headers, path, sizes[path])
        _check(
            requests.post(f"{draft_url}/send", headers=headers, timeout=REQUEST_TIMEOUT),
            "send draft",
        )
    except Exception:
        # Without this, every failed send leaves a half-built draft in the
        # sender's mailbox. Best effort: the original error is what matters.
        try:
            requests.delete(draft_url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            log.warning("automation_core.email: could not delete draft after a failed send")
        raise


def _upload_large_attachment(draft_url: str, headers: dict, path: Path, size: int) -> None:
    session = _check(
        requests.post(
            f"{draft_url}/attachments/createUploadSession",
            headers=headers,
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": path.name,
                    "size": size,
                    "contentType": _content_type(path),
                }
            },
            timeout=REQUEST_TIMEOUT,
        ),
        f"create upload session for {path.name}",
    ).json()
    upload_url = session["uploadUrl"]
    with path.open("rb") as handle:
        start = 0
        while start < size:
            chunk = handle.read(UPLOAD_CHUNK_BYTES)
            end = start + len(chunk) - 1
            # The upload URL carries its own credentials. Sending the
            # Authorization header with it gets a 401, so it is left off.
            _check(
                requests.put(
                    upload_url,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                    data=chunk,
                    timeout=REQUEST_TIMEOUT,
                ),
                f"upload {path.name}",
            )
            start = end + 1
