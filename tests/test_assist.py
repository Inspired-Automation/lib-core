from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from automation_core.assist import (
    LEFTOVER_RELEASE_WAIT_S,
    MAX_REASON_CHARS,
    RELEASE_FILENAME,
    REQUEST_FILENAME,
    STATE_FILENAME,
    Assist,
)
from automation_core.context import Context

URL = "http://node01:6080/vnc.html?autoconnect=1&path=websockify%3Ftoken%3Dabc"


def _ctx(job_file: Path | None) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path("/tmp/test.log"),
        is_production=False,
        notification_method="email",
        notification_recipient="test@example.com",
        job_file=job_file,
    )


def _job_file(tmp_path: Path) -> Path:
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps({"job_id": 7}), encoding="utf-8")
    return job_file


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(job_dir: Path, phase: str = "live", url: str | None = URL) -> None:
    # The shape agent/vncops.py writes (_write_state_file).
    (job_dir / STATE_FILENAME).write_text(
        json.dumps({"phase": phase, "url": url, "reason": "r",
                    "expires_in_s": 1800}),
        encoding="utf-8",
    )


class FakeAgent:
    """A clock plus a stand-in for the agent's progress tick. Sleeping
    advances time, and once ``live_at`` is reached the agent consumes the
    request and writes assist.json, as vncops.poll() does."""

    def __init__(self, job_dir: Path, live_at: float | None = None) -> None:
        self.job_dir = job_dir
        self.now = 0.0
        self.live_at = live_at
        self.requests_seen: list[dict] = []
        self.sleeps = 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds
        request = self.job_dir / REQUEST_FILENAME
        if self.live_at is not None and self.now >= self.live_at and request.exists():
            self.requests_seen.append(_read(request))
            request.unlink()
            _write_state(self.job_dir)


class TestNoJobFile:
    def test_hand_run_is_a_noop_with_a_warning(self, tmp_path, caplog, monkeypatch):
        monkeypatch.chdir(tmp_path)
        caplog.set_level(logging.WARNING)
        with _ctx(job_file=None).request_assist("Solve the captcha") as assist:
            assert assist.active is False
            assert assist.url is None
            assert assist.state is None
            assert assist.wait_for_url(5) is None
        assert list(tmp_path.iterdir()) == []
        assert "no Control Room job file" in caplog.text

    def test_hand_run_does_not_block(self):
        slept = []
        assist = Assist(None, "x", timeout_s=900, sleep=slept.append)
        with assist:
            pass
        assert slept == []


class TestRequest:
    def test_request_written_with_reason_in_job_dir(self, tmp_path):
        ctx = _ctx(_job_file(tmp_path))
        with ctx.request_assist("Solve the captcha", wait=False) as assist:
            assert assist.active is True
            assert _read(tmp_path / REQUEST_FILENAME) == {"reason": "Solve the captcha"}
            assert not (tmp_path / (REQUEST_FILENAME + ".tmp")).exists()

    def test_reason_is_flattened_and_capped(self, tmp_path):
        assist = Assist(tmp_path, "line one\n  line two" + "x" * 1000, wait=False)
        with assist:
            reason = _read(tmp_path / REQUEST_FILENAME)["reason"]
        assert reason.startswith("line one line two")
        assert len(reason) == MAX_REASON_CHARS

    def test_marker_fits_the_agents_read_even_with_non_ascii(self, tmp_path):
        # vncops reads 4096 bytes; a truncated JSON body would degrade to a
        # 200-character raw reason.
        with Assist(tmp_path, "\U0001F600" * 1000, wait=False):
            size = (tmp_path / REQUEST_FILENAME).stat().st_size
        assert size < 4096

    def test_blank_reason_writes_empty_object(self, tmp_path):
        with Assist(tmp_path, "   ", wait=False):
            assert _read(tmp_path / REQUEST_FILENAME) == {}

    def test_rejects_non_positive_poll(self, tmp_path):
        with pytest.raises(ValueError):
            Assist(tmp_path, "x", poll_s=0)


class TestWaitForUrl:
    def test_blocks_until_live_and_exposes_url(self, tmp_path):
        agent = FakeAgent(tmp_path, live_at=12)
        assist = Assist(tmp_path, "captcha", timeout_s=60, poll_s=2,
                        sleep=agent.sleep, clock=agent.clock)
        with assist:
            assert assist.url == URL
            assert agent.now == pytest.approx(12)
        assert agent.requests_seen == [{"reason": "captcha"}]

    def test_timeout_warns_and_does_not_raise(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING)
        agent = FakeAgent(tmp_path, live_at=None)
        ran = False
        with Assist(tmp_path, "captcha", timeout_s=30, poll_s=2,
                    sleep=agent.sleep, clock=agent.clock) as assist:
            ran = True
            assert assist.url is None
        assert ran
        assert agent.now == pytest.approx(30)
        assert "had not made it live within 30" in caplog.text

    def test_url_is_never_logged(self, tmp_path, caplog):
        caplog.set_level(logging.DEBUG)
        agent = FakeAgent(tmp_path, live_at=2)
        with Assist(tmp_path, "captcha", sleep=agent.sleep, clock=agent.clock):
            pass
        assert "token" not in caplog.text
        assert URL not in caplog.text

    def test_wait_false_returns_immediately_then_url_can_be_polled(self, tmp_path):
        agent = FakeAgent(tmp_path, live_at=None)
        with Assist(tmp_path, "captcha", wait=False, sleep=agent.sleep,
                    clock=agent.clock) as assist:
            assert agent.sleeps == 0
            assert assist.url is None
            _write_state(tmp_path)
            assert assist.url == URL

    def test_url_only_when_live(self, tmp_path):
        assist = Assist(tmp_path, "x", wait=False)
        _write_state(tmp_path, phase="starting", url=None)
        assert assist.url is None
        (tmp_path / STATE_FILENAME).write_text("{half a docu", encoding="utf-8")
        assert assist.url is None
        assert assist.state is None


class TestRelease:
    def test_release_written_on_clean_exit(self, tmp_path):
        with Assist(tmp_path, "x", wait=False):
            assert not (tmp_path / RELEASE_FILENAME).exists()
        assert _read(tmp_path / RELEASE_FILENAME) == {}

    def test_release_written_on_exception_and_exception_propagates(self, tmp_path):
        with pytest.raises(RuntimeError):
            with Assist(tmp_path, "x", wait=False):
                raise RuntimeError("page never changed")
        assert _read(tmp_path / RELEASE_FILENAME) == {
            "reason": "released by the bot after RuntimeError"
        }

    def test_unconsumed_request_is_withdrawn(self, tmp_path):
        # The agent never ticked: a request left behind would start a
        # session after the bot had moved on.
        with Assist(tmp_path, "x", wait=False):
            assert (tmp_path / REQUEST_FILENAME).exists()
        assert not (tmp_path / REQUEST_FILENAME).exists()
        assert (tmp_path / RELEASE_FILENAME).exists()

    def test_early_release_is_idempotent(self, tmp_path):
        with Assist(tmp_path, "x", wait=False) as assist:
            assist.release("page changed")
            (tmp_path / RELEASE_FILENAME).unlink()  # the agent consumed it
        assert not (tmp_path / RELEASE_FILENAME).exists()

    def test_no_release_when_request_could_not_be_written(self, tmp_path):
        missing = tmp_path / "gone"
        with Assist(missing, "x", timeout_s=900) as assist:
            assert assist.url is None
        assert not missing.exists()


class TestLeftoverRelease:
    def test_leftover_release_deleted_when_nothing_live(self, tmp_path):
        # An earlier assist ended without the agent reading the bot's
        # release: left there, it would end the new assist on its first tick.
        (tmp_path / RELEASE_FILENAME).write_text("{}", encoding="utf-8")
        agent = FakeAgent(tmp_path)
        with Assist(tmp_path, "x", wait=False, sleep=agent.sleep,
                    clock=agent.clock):
            assert not (tmp_path / RELEASE_FILENAME).exists()
            assert (tmp_path / REQUEST_FILENAME).exists()
        assert agent.sleeps == 0

    def test_waits_for_agent_to_consume_release_of_a_live_assist(self, tmp_path):
        (tmp_path / RELEASE_FILENAME).write_text("{}", encoding="utf-8")
        _write_state(tmp_path)

        class TickingAgent(FakeAgent):
            def sleep(self, seconds):
                super().sleep(seconds)
                if self.now >= 10:
                    # vncops.poll(): consume the release, stop, clear state.
                    (self.job_dir / RELEASE_FILENAME).unlink(missing_ok=True)
                    (self.job_dir / STATE_FILENAME).unlink(missing_ok=True)

        agent = TickingAgent(tmp_path)
        with Assist(tmp_path, "second", wait=False, sleep=agent.sleep,
                    clock=agent.clock) as assist:
            assert agent.now == pytest.approx(10)
            assert _read(tmp_path / REQUEST_FILENAME) == {"reason": "second"}
            # The old assist's URL is gone, so it cannot be mistaken for ours.
            assert assist.url is None

    def test_gives_up_waiting_and_deletes(self, tmp_path):
        (tmp_path / RELEASE_FILENAME).write_text("{}", encoding="utf-8")
        _write_state(tmp_path)
        agent = FakeAgent(tmp_path)
        with Assist(tmp_path, "x", wait=False, sleep=agent.sleep,
                    clock=agent.clock):
            assert agent.now == pytest.approx(LEFTOVER_RELEASE_WAIT_S)
            assert (tmp_path / REQUEST_FILENAME).exists()


class TestContextMethod:
    def test_job_dir_is_the_job_files_directory(self, tmp_path):
        job_dir = tmp_path / "jobs" / "7"
        job_dir.mkdir(parents=True)
        assist = _ctx(_job_file(job_dir)).request_assist("x", wait=False)
        assert assist.job_dir == job_dir

    def test_exported_from_top_level(self):
        import automation_core

        assert automation_core.Assist is Assist
