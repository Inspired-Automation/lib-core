"""
Tests for the long-running operation monitor.

No GUI and no real windows: `wait_for_operation` takes its "is it still
running" test as a callable, which is precisely so the control flow can be
tested without one.

The behaviours asserted here are the ones the Automation Anywhere bots got
right and a naive reimplementation gets wrong.
"""

from __future__ import annotations

import pytest

from automation_core.gui.monitor import (
    ErrorDialog,
    MonitorResult,
    Outcome,
    any_of,
    poll_count,
    wait_for_operation,
)


def running_for(n_polls: int):
    """An is_running that reports finished after n checks."""
    state = {"checks": 0}

    def is_running() -> bool:
        state["checks"] += 1
        return state["checks"] <= n_polls

    return is_running


NO_PIDS = lambda: set()          # noqa: E731
NO_SLEEP = lambda _seconds: None  # noqa: E731  run the real flow, instantly


class TestCompletion:
    def test_completes_when_the_operation_stops(self):
        result = wait_for_operation(
            is_running=running_for(3),
            process_ids=NO_PIDS,
            timeout_minutes=10,
            poll_seconds=60,
            sleep=NO_SLEEP,
        )
        assert result.ok
        assert result.outcome is Outcome.COMPLETED
        assert result.polls == 3

    def test_completion_is_checked_before_sleeping(self):
        """An operation already finished must return without ever polling.

        The AA bot checks the window first and only then sleeps, so a fast
        operation is not held for a full interval. With a 60 second poll this
        is the difference between instant and a minute. A real poll_seconds is
        used here on purpose: if the check happened after the sleep, this test
        would take a minute instead of no time at all.
        """
        slept = []

        result = wait_for_operation(
            is_running=lambda: False,
            process_ids=NO_PIDS,
            timeout_minutes=60,
            poll_seconds=60,
            on_poll=lambda n, e: slept.append(n),
        )
        assert result.ok
        assert result.polls == 0
        assert slept == [], "must not sleep when the operation is already done"


class TestTimeout:
    def test_times_out_and_says_so(self):
        result = wait_for_operation(
            is_running=lambda: True,
            process_ids=NO_PIDS,
            timeout_minutes=1,
            poll_seconds=60,
            sleep=NO_SLEEP,
            description="download",
        )
        assert result.outcome is Outcome.TIMED_OUT
        assert not result.ok
        assert "still running" in result.detail

    def test_limit_is_derived_from_minutes_and_interval(self):
        """The limit is per operation, from config. A download and an upload
        get different values, so the arithmetic has to be right.

        Tested through poll_count directly rather than by running a monitor,
        because running one at a realistic interval means actually waiting
        that long.
        """
        assert poll_count(5, 60) == 5          # 5 minutes at 60s
        assert poll_count(60, 60) == 60        # an hour
        assert poll_count(120, 30) == 240      # two hours at 30s
        assert poll_count(0.5, 60) == 1        # never zero
        assert poll_count(0, 60) == 1

    def test_a_non_positive_interval_is_rejected(self):
        """Accepting zero would turn a typo into a loop that spins a core for
        hours. Failing immediately is kinder than that.
        """
        for bad in (0, -1, -0.5):
            with pytest.raises(ValueError, match="must be positive"):
                poll_count(60, bad)

    def test_a_timeout_captures_a_screenshot(self):
        shots = []
        result = wait_for_operation(
            is_running=lambda: True,
            process_ids=NO_PIDS,
            timeout_minutes=1,
            poll_seconds=60,
            sleep=NO_SLEEP,
            capture_screenshot=lambda label: shots.append(label) or f"{label}.png",
            description="upload",
        )
        assert result.outcome is Outcome.TIMED_OUT
        assert shots == ["upload-timeout"]
        assert result.screenshots == ["upload-timeout.png"]


class TestErrorDetection:
    def test_a_matching_error_dialog_fails_the_run(self, monkeypatch):
        monkeypatch.setattr(
            "automation_core.gui.monitor.find_error_dialog",
            lambda dialogs, pids: (dialogs[0], 1234, "Internet Connection Error"),
        )
        result = wait_for_operation(
            is_running=lambda: True,
            process_ids=lambda: {42},
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
            error_dialogs=[ErrorDialog("Error", contains="Internet")],
        )
        assert result.outcome is Outcome.FAILED
        assert result.error_text == "Internet Connection Error"

    def test_an_error_dialog_is_dismissed(self, monkeypatch):
        """An undismissed modal dialog blocks the application, so the next run
        finds it wedged. Failing is not enough; it has to be cleared.
        """
        monkeypatch.setattr(
            "automation_core.gui.monitor.find_error_dialog",
            lambda dialogs, pids: (dialogs[0], 999, "Unexpected Error"),
        )
        dismissed = []
        wait_for_operation(
            is_running=lambda: True,
            process_ids=lambda: {42},
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
            error_dialogs=[ErrorDialog("Error", dismiss="OK")],
            dismiss=lambda handle, button: dismissed.append((handle, button)),
        )
        assert dismissed == [(999, "OK")]

    def test_error_checking_is_skipped_when_the_process_is_gone(self, monkeypatch):
        """No pids means nothing to scope to, and an unscoped caption match
        would pick up another application's window.
        """
        called = []
        monkeypatch.setattr(
            "automation_core.gui.monitor.find_error_dialog",
            lambda dialogs, pids: called.append(pids),
        )
        wait_for_operation(
            is_running=running_for(2),
            process_ids=lambda: set(),
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
            error_dialogs=[ErrorDialog("Error")],
        )
        assert called == [], "must not search for dialogs with no process to scope to"


class TestResilience:
    def test_is_running_raising_does_not_end_the_run(self):
        """A transient COM failure while reading window state must not be
        mistaken for the operation having finished.
        """
        state = {"n": 0}

        def flaky() -> bool:
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("transient")
            return state["n"] <= 3

        result = wait_for_operation(
            is_running=flaky,
            process_ids=NO_PIDS,
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
        )
        assert result.ok
        assert state["n"] > 1

    def test_on_poll_raising_does_not_end_the_run(self):
        def bad_heartbeat(poll, elapsed):
            raise ValueError("logging blew up")

        result = wait_for_operation(
            is_running=running_for(2),
            process_ids=NO_PIDS,
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
            on_poll=bad_heartbeat,
        )
        assert result.ok

    def test_on_poll_can_abandon_the_run(self):
        def give_up(poll, elapsed):
            if poll >= 2:
                raise StopIteration

        result = wait_for_operation(
            is_running=lambda: True,
            process_ids=NO_PIDS,
            timeout_minutes=600,
            poll_seconds=60,
            sleep=NO_SLEEP,
            on_poll=give_up,
        )
        assert result.outcome is Outcome.ABANDONED
        assert not result.ok

    def test_a_screenshot_failure_does_not_mask_the_real_outcome(self):
        def broken(label):
            raise OSError("disk full")

        result = wait_for_operation(
            is_running=lambda: True,
            process_ids=NO_PIDS,
            timeout_minutes=1,
            poll_seconds=60,
            sleep=NO_SLEEP,
            capture_screenshot=broken,
        )
        assert result.outcome is Outcome.TIMED_OUT
        assert result.screenshots == []


class TestHeartbeat:
    def test_on_poll_is_called_every_interval(self):
        """For something that runs for hours, a heartbeat is what separates
        "working" from "apparently hung".
        """
        beats = []
        wait_for_operation(
            is_running=running_for(4),
            process_ids=NO_PIDS,
            timeout_minutes=60,
            poll_seconds=60,
            sleep=NO_SLEEP,
            on_poll=lambda n, e: beats.append(n),
        )
        # running_for(4) reports running on four checks, so four
        # heartbeats, then the fifth check finds it finished.
        assert beats == [1, 2, 3, 4]


class TestAnyOf:
    def test_true_while_any_predicate_is_true(self):
        assert any_of(lambda: False, lambda: True)()
        assert not any_of(lambda: False, lambda: False)()


class TestMonitorResult:
    def test_only_completed_is_ok(self):
        for outcome in Outcome:
            result = MonitorResult(outcome, 1.0, 1)
            assert result.ok == (outcome is Outcome.COMPLETED)
