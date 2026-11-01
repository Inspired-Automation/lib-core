from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import requests

from automation_core import Answer, Decision, DecisionUnavailable
from automation_core.context import Context
from automation_core.decide import ENDPOINT, decide

TOKEN = "jt_7f3c9a1e2b4d5f60718293a4b5c6d7e8"
CR_URL = "http://controlroom.example/controlroom"


def _ctx(job_file: Path | None) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path("/tmp/test.log"),
        is_production=False,
        notification_method="email",
        notification_recipient="test@example.com",
        job_file=job_file,
    )


def _job_file(tmp_path: Path, **extra) -> Path:
    # The shape agent 0.72.0 writes: job_id, bot, params, cr_url, cr_token.
    body = {"job_id": 7, "bot": "TestProcess", "params": {},
            "cr_url": CR_URL + "/", "cr_token": TOKEN}
    body.update(extra)
    for k in [k for k, v in body.items() if v is None]:
        del body[k]
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(body), encoding="utf-8")
    return job_file


def _decided_body(**over) -> dict:
    body = {
        "run_id": 4471, "agent": "failure_triage", "version": 3,
        "outcome": "decided", "model": "microsoft-decision-1", "error": None,
        "answers": {
            "cause": {"kind": "choice", "choice": "credentials",
                      "confidence": 0.93, "outcome": "decided",
                      "probabilities": {"credentials": 0.93, "data": 0.04,
                                        "bot_bug": 0.02, "cannot_tell": 0.01}},
            "transient": {"kind": "noul", "choice": "no", "confidence": 0.88,
                          "outcome": "decided",
                          "probabilities": {"yes": 0.12, "no": 0.88}},
        },
    }
    body.update(over)
    return body


class FakeResponse:
    def __init__(self, status: int, body=None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakePost:
    """Stands in for requests.post: records every call and answers from a
    script of responses or exceptions, in order."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers,
                           "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def post(monkeypatch):
    """Install a FakePost as requests.post for Context.decide() and hand
    it to the test to script."""
    def install(*script) -> FakePost:
        fake = FakePost(*script)
        monkeypatch.setattr(requests, "post", fake)
        return fake
    return install


class TestDecided:
    def test_decided_answer_and_request_shape(self, tmp_path, post):
        fake = post(FakeResponse(200, _decided_body()))
        ctx = _ctx(_job_file(tmp_path))
        d = ctx.decide("failure_triage", error_text="LoginFailed: bad password",
                       bot="TestProcess")
        assert isinstance(d, Decision)
        assert d.decided is True
        assert d.outcome == "decided"
        assert d.run_id == 4471 and d.version == 3
        assert d.agent == "failure_triage"
        assert d.model == "microsoft-decision-1"
        assert d["cause"] == "credentials"
        assert d["transient"] == "no"
        assert d.choices == {"cause": "credentials", "transient": "no"}
        assert "cause" in d and "severity" not in d
        a = d.answer("cause")
        assert isinstance(a, Answer)
        assert a.kind == "choice" and a.confidence == pytest.approx(0.93)
        assert a.probabilities["data"] == pytest.approx(0.04)
        assert a.decided is True
        assert d.raw == _decided_body()
        # The request: the agent's endpoint, bearer token, inputs and job id.
        [call] = fake.calls
        assert call["url"] == CR_URL + ENDPOINT + "failure_triage"
        assert call["headers"]["Authorization"] == "Bearer " + TOKEN
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["headers"]["User-Agent"].startswith("automation-core/")
        assert call["json"] == {"inputs": {"error_text": "LoginFailed: bad password",
                                           "bot": "TestProcess"},
                                "job_id": 7}
        assert call["timeout"] == pytest.approx(30.0)

    def test_explicit_inputs_dict_and_timeout(self, tmp_path, post):
        fake = post(FakeResponse(200, _decided_body()))
        ctx = _ctx(_job_file(tmp_path))
        ctx.decide("failure_triage", {"inputs": "clash", "timeout_s": "clash"},
                   bot="x", timeout_s=5)
        [call] = fake.calls
        assert call["json"]["inputs"] == {"inputs": "clash", "timeout_s": "clash",
                                          "bot": "x"}
        assert call["timeout"] == 5

    def test_unknown_output_name_raises_keyerror(self, tmp_path, post):
        post(FakeResponse(200, _decided_body()))
        d = _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        with pytest.raises(KeyError, match="no output 'severity'"):
            d["severity"]
        with pytest.raises(KeyError):
            d.answer("severity")

    def test_non_serialisable_input_is_a_typeerror(self, tmp_path, post):
        fake = post()
        with pytest.raises(TypeError, match="not JSON-serialisable"):
            _ctx(_job_file(tmp_path)).decide("failure_triage", when=object())
        assert fake.calls == []

    def test_blank_agent_name_rejected(self, tmp_path, post):
        post()
        with pytest.raises(ValueError):
            _ctx(_job_file(tmp_path)).decide("  ")


class TestReview:
    def test_review_answer_has_no_choice(self, tmp_path, post, caplog):
        caplog.set_level(logging.INFO)
        body = _decided_body(outcome="review")
        body["answers"]["cause"].update(choice=None, confidence=0.41,
                                        outcome="review")
        post(FakeResponse(200, body))
        d = _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert d.decided is False
        assert d["cause"] is None
        assert d["transient"] == "no"
        assert d.answer("cause").decided is False
        assert d.answer("cause").confidence == pytest.approx(0.41)
        assert d.choices == {"transient": "no"}
        # Comparing an undecided output with an option is safe and false.
        assert (d["cause"] == "credentials") is False
        assert "outcome review" in caplog.text

    def test_error_outcome_returns_decision_with_nothing_decided(self, tmp_path, post, caplog):
        caplog.set_level(logging.WARNING)
        body = {"run_id": 9, "agent": "failure_triage", "version": 3,
                "outcome": "error", "model": None, "error": "model timed out",
                "answers": {}}
        post(FakeResponse(200, body))
        d = _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert d.outcome == "error" and d.decided is False
        assert d["cause"] is None          # no answers at all: None, not KeyError
        assert d.choices == {}
        assert "model timed out" in caplog.text


class TestUnavailable:
    @pytest.mark.parametrize("status,body,needle", [
        (503, {"error": "Decisions are switched off", "outcome": "off"}, "HTTP 503"),
        (401, {"error": "token refused"}, "HTTP 401"),
        (404, {"error": "no such agent"}, "HTTP 404"),
        (413, {"error": "inputs too large"}, "HTTP 413"),
        (400, {"error": "missing input error_text"}, "HTTP 400"),
        (429, None, "HTTP 429"),
        (500, None, "HTTP 500"),
    ])
    def test_non_200_raises_with_status(self, tmp_path, post, status, body, needle):
        post(FakeResponse(status, body, text="oops"))
        with pytest.raises(DecisionUnavailable) as exc:
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert needle in str(exc.value)
        assert "failure_triage" in str(exc.value)
        if body:
            assert body["error"] in str(exc.value)
        assert TOKEN not in str(exc.value)

    def test_connection_error_is_retried_once_then_raised(self, tmp_path, post, caplog):
        caplog.set_level(logging.WARNING)
        fake = post(requests.exceptions.ConnectionError("refused"),
                    requests.exceptions.ConnectionError("refused"))
        with pytest.raises(DecisionUnavailable, match="could not be reached"):
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert len(fake.calls) == 2
        assert "trying once more" in caplog.text

    def test_connection_error_then_success(self, tmp_path, post):
        fake = post(requests.exceptions.ConnectionError("refused"),
                    FakeResponse(200, _decided_body()))
        d = _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert d["cause"] == "credentials"
        assert len(fake.calls) == 2

    def test_connect_timeout_counts_as_a_connection_error(self, tmp_path, post):
        # ConnectTimeout is both a ConnectionError and a Timeout: the request
        # never reached the Control Room, so one retry is safe.
        fake = post(requests.exceptions.ConnectTimeout("slow dns"),
                    FakeResponse(200, _decided_body()))
        _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert len(fake.calls) == 2

    def test_read_timeout_is_not_retried(self, tmp_path, post):
        fake = post(requests.exceptions.ReadTimeout("slow"))
        with pytest.raises(DecisionUnavailable, match="did not answer within 30s"):
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert len(fake.calls) == 1

    def test_hand_run_without_job_file_raises(self, post):
        fake = post()
        with pytest.raises(DecisionUnavailable, match="hand run"):
            _ctx(job_file=None).decide("failure_triage", error_text="x")
        assert fake.calls == []

    def test_old_job_file_without_token_raises(self, tmp_path, post):
        fake = post()
        job_file = _job_file(tmp_path, cr_url=None, cr_token=None)
        with pytest.raises(DecisionUnavailable, match="older than 0.72.0"):
            _ctx(job_file).decide("failure_triage", error_text="x")
        assert fake.calls == []

    def test_unreadable_job_file_raises(self, tmp_path, post):
        post()
        job_file = tmp_path / "job.json"
        job_file.write_text("{half a docu", encoding="utf-8")
        with pytest.raises(DecisionUnavailable, match="could not be read"):
            _ctx(job_file).decide("failure_triage", error_text="x")

    def test_reply_without_answers_raises(self, tmp_path, post):
        post(FakeResponse(200, {"hello": "world"}))
        with pytest.raises(DecisionUnavailable, match="no answers object"):
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")

    def test_unparseable_200_raises(self, tmp_path, post):
        post(FakeResponse(200, None, text="<html>"))
        with pytest.raises(DecisionUnavailable):
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")


class TestLogging:
    def test_token_and_inputs_never_logged(self, tmp_path, post, caplog):
        caplog.set_level(logging.DEBUG)
        secret_input = "customer 0123456789 password hunter2"
        post(requests.exceptions.ConnectionError("refused"),
             FakeResponse(200, _decided_body()))
        _ctx(_job_file(tmp_path)).decide("failure_triage", error_text=secret_input)
        assert TOKEN not in caplog.text
        assert secret_input not in caplog.text
        assert "hunter2" not in caplog.text
        assert "failure_triage" in caplog.text
        assert "cause=credentials" in caplog.text

    def test_token_not_in_exception_on_status(self, tmp_path, post):
        post(FakeResponse(401, {"error": "token refused"}))
        with pytest.raises(DecisionUnavailable) as exc:
            _ctx(_job_file(tmp_path)).decide("failure_triage", error_text="x")
        assert TOKEN not in repr(exc.value)


class TestFunction:
    def test_decide_function_takes_its_own_post(self, tmp_path):
        fake = FakePost(FakeResponse(200, _decided_body()))
        d = decide(_job_file(tmp_path), "failure_triage", {"error_text": "x"},
                   timeout_s=3, post=fake)
        assert d["cause"] == "credentials"
        assert fake.calls[0]["timeout"] == 3
        assert fake.calls[0]["json"]["job_id"] == 7

    def test_url_trailing_slash_is_trimmed(self, tmp_path):
        fake = FakePost(FakeResponse(200, _decided_body()))
        decide(_job_file(tmp_path, cr_url="http://cr/prefix///"), "a", {}, post=fake)
        assert fake.calls[0]["url"] == "http://cr/prefix" + ENDPOINT + "a"
