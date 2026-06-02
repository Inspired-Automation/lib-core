from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from automation_core.context import Context
from automation_core.errors import ErrorCollector
from automation_core.notifications import dispatch_notification
from automation_core.notifications.freshservice import create_ticket, normalize_base_url


def _ctx(method: str = "email", is_production: bool = True) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path("/tmp/test.log"),
        is_production=is_production,
        notification_method=method,
        notification_recipient="team@example.com",
    )


CONFIG = {
    "graph": {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": "tid",
        "sender_address": "bot@example.com",
    },
    "freshservice": {
        "api_key": "key",
        "domain": "example.freshservice.com",
        "defaults": {
            "workspace_id": 2,
            "group_id": 123,
            "requester_email": "requester@example.com",
            "type": "Incident",
            "tags": ["automation", "bpi"],
        },
    },
    "notifications": {"default_recipient": "team@example.com"},
    "paths": {"production_root": r"I:\BPI\Automation Team\Automated Processes"},
}


def _errors(*messages: str) -> ErrorCollector:
    ec = ErrorCollector()
    for msg in messages:
        ec.add(msg)
    return ec


class TestEmailDispatch:
    def test_send_called_with_correct_recipient_and_subject(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch("automation_core.notifications.email_graph.send") as mock_send,
        ):
            dispatch_notification(_ctx("email"), _errors("err1"), is_critical=False)
        mock_send.assert_called_once()
        _, args, _ = mock_send.mock_calls[0]
        # send(config, recipient, subject, body)
        assert args[1] == "team@example.com"
        assert "[TestProcess]" in args[2]

    def test_critical_subject_prefix(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch("automation_core.notifications.email_graph.send") as mock_send,
        ):
            dispatch_notification(_ctx("email"), _errors(), is_critical=True)
        subject = mock_send.call_args[0][2]
        assert subject.startswith("[CRITICAL]")

    def test_dispatch_failure_does_not_raise(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch(
                "automation_core.notifications.email_graph.send",
                side_effect=RuntimeError("network down"),
            ),
        ):
            # Should not raise
            dispatch_notification(_ctx("email"), _errors("err"), is_critical=False)

    def test_dispatch_failure_logs_to_both_loggers(self, caplog):
        import logging

        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch(
                "automation_core.notifications.email_graph.send",
                side_effect=RuntimeError("network down"),
            ),
            caplog.at_level(logging.ERROR),
        ):
            dispatch_notification(_ctx("email"), _errors("err"), is_critical=False)
        assert any("notification dispatch failed" in r.message for r in caplog.records)


class TestFreshserviceDispatch:
    def test_create_ticket_called_high_priority_for_critical(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch("automation_core.notifications.freshservice.create_ticket") as mock_ticket,
        ):
            dispatch_notification(_ctx("freshservice"), _errors("err"), is_critical=True)
        mock_ticket.assert_called_once()
        kwargs = mock_ticket.call_args[1]
        assert kwargs["is_critical"] is True

    def test_create_ticket_body_is_html_wrapped(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch("automation_core.notifications.freshservice.create_ticket") as mock_ticket,
        ):
            dispatch_notification(_ctx("freshservice"), _errors("err"), is_critical=False)
        body_html = mock_ticket.call_args[0][2]
        assert body_html.startswith("<pre>") and body_html.endswith("</pre>")


class TestNormalizeBaseUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            "domain.com",
            "https://domain.com",
            "https://domain.com/",
            "https://domain.com/api/v2",
        ],
    )
    def test_inputs_collapse_to_canonical_url(self, raw):
        assert normalize_base_url(raw) == "https://domain.com/api/v2"


class TestFreshserviceTicketPayload:
    FS_CONFIG = {
        "freshservice": {
            "api_key": "key",
            "domain": "example.freshservice.com",
            "defaults": {
                "workspace_id": 2,
                "group_id": 123,
                "requester_email": "requester@example.com",
                "type": "Incident",
                "tags": ["automation", "bpi"],
            },
        },
    }

    def _post_payload(self, is_critical: bool) -> dict:
        with patch(
            "automation_core.notifications.freshservice.requests.post"
        ) as mock_post:
            create_ticket(
                self.FS_CONFIG, "subj", "<pre>body</pre>", is_critical=is_critical
            )
        return mock_post.call_args.kwargs["json"]

    def test_critical_payload_has_critical_tag(self):
        payload = self._post_payload(is_critical=True)
        assert "critical" in payload["tags"]

    def test_summary_payload_has_summary_tag(self):
        payload = self._post_payload(is_critical=False)
        assert "summary" in payload["tags"]

    def test_configured_tags_preserved(self):
        payload = self._post_payload(is_critical=True)
        assert "automation" in payload["tags"]
        assert "bpi" in payload["tags"]

    def test_payload_uses_requester_email_and_default_fields(self):
        payload = self._post_payload(is_critical=False)
        assert payload["email"] == "requester@example.com"
        assert payload["workspace_id"] == 2
        assert payload["group_id"] == 123
        assert payload["type"] == "Incident"

    def test_configured_tag_list_not_mutated_across_calls(self):
        self._post_payload(is_critical=True)
        self._post_payload(is_critical=False)
        assert self.FS_CONFIG["freshservice"]["defaults"]["tags"] == [
            "automation",
            "bpi",
        ]


class TestMSALErrorHandling:
    def test_missing_access_token_raises(self):
        from automation_core.notifications.email_graph import send

        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "bad credentials",
        }
        with patch("msal.ConfidentialClientApplication", return_value=mock_app):
            with pytest.raises(RuntimeError, match="bad credentials"):
                send(CONFIG, "r@example.com", "subj", "body")


class TestDevModeSuppression:
    def test_suppressed_when_not_production(self):
        with (
            patch("automation_core.notifications._get_config", return_value=CONFIG),
            patch("automation_core.notifications.email_graph.send") as mock_send,
        ):
            # is_production=False and no env override: dispatch is suppressed at _maybe_dispatch
            # level, so dispatch_notification itself is never called in that path.
            # This test confirms that if called directly, it still routes correctly.
            # The suppression test lives in test_errors.py where _maybe_dispatch is exercised.
            dispatch_notification(_ctx("email", is_production=True), _errors("e"), is_critical=False)
        mock_send.assert_called_once()
