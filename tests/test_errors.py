from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from automation_core.context import Context
from automation_core.errors import ErrorCollector, collect_errors
from pathlib import Path


def _ctx(is_production: bool = False) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path("/tmp/test.log"),
        is_production=is_production,
        notification_method="email",
        notification_recipient="test@example.com",
    )


class TestErrorCollector:
    def test_initial_count_is_zero(self):
        ec = ErrorCollector()
        assert ec.count == 0
        assert not ec.has_errors

    def test_add_increments_count(self):
        ec = ErrorCollector()
        ec.add("something went wrong")
        assert ec.count == 1
        assert ec.has_errors

    def test_add_multiple(self):
        ec = ErrorCollector()
        ec.add("error one")
        ec.add("error two")
        assert ec.count == 2

    def test_add_stores_message(self):
        ec = ErrorCollector()
        ec.add("my message", details={"key": "val"})
        stored = ec.all()[0]
        assert stored["message"] == "my message"
        assert stored["details"] == {"key": "val"}

    def test_add_with_exception(self):
        ec = ErrorCollector()
        exc = ValueError("boom")
        ec.add("failed", exception=exc)
        assert ec.all()[0]["exception"] is exc


class TestCollectErrors:
    def test_no_errors_no_dispatch(self):
        with patch("automation_core.errors._maybe_dispatch") as mock_dispatch:
            with collect_errors(_ctx()):
                pass
            mock_dispatch.assert_not_called()

    def test_non_fatal_errors_dispatch_summary(self):
        with patch("automation_core.errors._maybe_dispatch") as mock_dispatch:
            with collect_errors(_ctx()) as errors:
                errors.add("non-fatal")
            mock_dispatch.assert_called_once()
            _, kwargs = mock_dispatch.call_args
            assert kwargs["is_critical"] is False

    def test_uncaught_exception_dispatches_critical_and_reraises(self):
        with patch("automation_core.errors._maybe_dispatch") as mock_dispatch:
            with pytest.raises(RuntimeError, match="boom"):
                with collect_errors(_ctx()) as errors:
                    raise RuntimeError("boom")
            mock_dispatch.assert_called_once()
            _, kwargs = mock_dispatch.call_args
            assert kwargs["is_critical"] is True

    def test_dev_mode_suppresses_dispatch(self, capsys):
        # is_production=False and no AUTOMATION_FORCE_NOTIFY
        dispatch_called = []

        def fake_dispatch(ctx, errors, **kwargs):
            dispatch_called.append(True)

        with (
            patch("automation_core.errors._get_config", return_value={}),
            patch("automation_core.notifications.dispatch_notification", fake_dispatch),
        ):
            with collect_errors(_ctx(is_production=False)) as errors:
                errors.add("some error")

        assert not dispatch_called

    def test_force_notify_overrides_dev_mode(self, monkeypatch):
        monkeypatch.setenv("AUTOMATION_FORCE_NOTIFY", "1")
        dispatch_called = []

        def fake_dispatch(ctx, errors, **kwargs):
            dispatch_called.append(True)

        with (
            patch("automation_core.errors._get_config", return_value={}),
            patch("automation_core.notifications.dispatch_notification", fake_dispatch),
        ):
            with collect_errors(_ctx(is_production=False)) as errors:
                errors.add("some error")

        assert dispatch_called


class TestNotificationsEnabled:
    """notifications.enabled = auto | always | never, and its interaction with
    AUTOMATION_FORCE_NOTIFY, exercised through _maybe_dispatch via collect_errors."""

    @staticmethod
    def _run(enabled, *, is_production, force_notify, monkeypatch):
        if force_notify:
            monkeypatch.setenv("AUTOMATION_FORCE_NOTIFY", "1")
        else:
            monkeypatch.delenv("AUTOMATION_FORCE_NOTIFY", raising=False)

        config = {"notifications": {"enabled": enabled}} if enabled is not None else {}
        dispatch_called = []

        def fake_dispatch(ctx, errors, **kwargs):
            dispatch_called.append(True)

        with (
            patch("automation_core.errors._get_config", return_value=config),
            patch("automation_core.notifications.dispatch_notification", fake_dispatch),
        ):
            with collect_errors(_ctx(is_production=is_production)) as errors:
                errors.add("some error")

        return bool(dispatch_called)

    def test_auto_dev_no_env_suppressed(self, monkeypatch):
        assert not self._run("auto", is_production=False, force_notify=False, monkeypatch=monkeypatch)

    def test_auto_dev_env_dispatched(self, monkeypatch):
        assert self._run("auto", is_production=False, force_notify=True, monkeypatch=monkeypatch)

    def test_auto_production_dispatched(self, monkeypatch):
        assert self._run("auto", is_production=True, force_notify=False, monkeypatch=monkeypatch)

    def test_always_dev_no_env_dispatched(self, monkeypatch):
        assert self._run("always", is_production=False, force_notify=False, monkeypatch=monkeypatch)

    def test_never_production_suppressed(self, monkeypatch):
        assert not self._run("never", is_production=True, force_notify=False, monkeypatch=monkeypatch)

    def test_never_env_set_still_suppressed(self, monkeypatch):
        # env var does not override "never"
        assert not self._run("never", is_production=False, force_notify=True, monkeypatch=monkeypatch)
