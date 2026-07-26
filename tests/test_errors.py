from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from automation_core.context import Context
from automation_core.errors import ErrorCollector, collect_errors
from pathlib import Path


def _ctx(
    is_production: bool = False, job_file: Path | None = None
) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path("/tmp/test.log"),
        is_production=is_production,
        notification_method="email",
        notification_recipient="test@example.com",
        job_file=job_file,
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


class TestErrorSidecar:
    """cr_errors.json: the Control Room agent's read-back channel. Written
    next to ctx.job_file, independent of notifications.enabled, and must
    never affect the run even when the write itself fails."""

    @staticmethod
    def _job_file(tmp_path: Path) -> Path:
        job_file = tmp_path / "job.json"
        job_file.write_text(json.dumps({"job_id": 1}), encoding="utf-8")
        return job_file

    @staticmethod
    def _sidecar(job_file: Path) -> Path:
        return job_file.parent / "cr_errors.json"

    def test_hand_run_writes_no_sidecar_on_non_fatal_errors(self):
        with patch("automation_core.errors._maybe_dispatch"):
            with collect_errors(_ctx()) as errors:
                errors.add("some error")
        # ctx.job_file is None: nothing to locate the sidecar next to.
        assert not (Path("/tmp") / "cr_errors.json").exists()

    def test_hand_run_writes_no_sidecar_on_exception(self):
        with patch("automation_core.errors._maybe_dispatch"):
            with pytest.raises(RuntimeError):
                with collect_errors(_ctx()):
                    raise RuntimeError("boom")
        assert not (Path("/tmp") / "cr_errors.json").exists()

    def test_no_sidecar_on_clean_error_free_exit(self, tmp_path):
        job_file = self._job_file(tmp_path)
        with collect_errors(_ctx(job_file=job_file)):
            pass
        assert not self._sidecar(job_file).exists()

    def test_sidecar_written_for_non_fatal_errors(self, tmp_path):
        job_file = self._job_file(tmp_path)
        with patch("automation_core.errors._maybe_dispatch"):
            with collect_errors(_ctx(job_file=job_file)) as errors:
                errors.add("row 4 missing SKU", details={"row": 4})
                errors.add(
                    "upstream timeout", exception=TimeoutError("read timed out")
                )

        payload = json.loads(self._sidecar(job_file).read_text(encoding="utf-8"))
        assert payload["schema"] == 1
        assert payload["is_critical"] is False
        assert payload["error_count"] == 2
        assert payload["traceback"] is None
        assert payload["errors"][0] == {
            "message": "row 4 missing SKU",
            "details": {"row": 4},
            "exception": None,
        }
        assert payload["errors"][1]["exception"] == (
            "TimeoutError: read timed out"
        )

    def test_sidecar_written_for_exception_with_traceback(self, tmp_path):
        job_file = self._job_file(tmp_path)
        with patch("automation_core.errors._maybe_dispatch"):
            with pytest.raises(RuntimeError, match="boom"):
                with collect_errors(_ctx(job_file=job_file)):
                    raise RuntimeError("boom")

        payload = json.loads(self._sidecar(job_file).read_text(encoding="utf-8"))
        assert payload["is_critical"] is True
        assert "RuntimeError: boom" in payload["traceback"]

    def test_sidecar_written_even_when_notifications_never(self, tmp_path):
        job_file = self._job_file(tmp_path)
        config = {"notifications": {"enabled": "never"}}
        with (
            patch("automation_core.errors._get_config", return_value=config),
            patch("automation_core.notifications.dispatch_notification"),
        ):
            with collect_errors(_ctx(job_file=job_file)) as errors:
                errors.add("some error")

        assert self._sidecar(job_file).exists()

    def test_sidecar_write_failure_does_not_propagate_or_block_notification(
        self, tmp_path
    ):
        job_file = self._job_file(tmp_path)
        dispatch_called = []

        def fake_dispatch(ctx, errors, **kwargs):
            dispatch_called.append(True)

        with (
            patch("os.replace", side_effect=OSError("disk full")),
            patch("automation_core.errors._get_config", return_value={}),
            patch(
                "automation_core.notifications.dispatch_notification",
                fake_dispatch,
            ),
        ):
            with collect_errors(_ctx(job_file=job_file, is_production=True)) as errors:
                errors.add("some error")

        assert dispatch_called
        assert not self._sidecar(job_file).exists()

    def test_sidecar_write_failure_does_not_block_reraise(self, tmp_path):
        job_file = self._job_file(tmp_path)
        with (
            patch("os.replace", side_effect=OSError("disk full")),
            patch("automation_core.errors._get_config", return_value={}),
            patch("automation_core.notifications.dispatch_notification"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                with collect_errors(_ctx(job_file=job_file, is_production=True)):
                    raise RuntimeError("boom")
