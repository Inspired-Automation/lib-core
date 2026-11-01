"""Decision agents: asking the Control Room a question with fixed answers.

A good share of what a bot does between the steps that need a desktop is
choosing: did this run fail because the portal changed or because the
password did, is this email a remittance advice or noise, which of three
paths should the next step take. A decision agent is a named question (or
a few questions over the same input) with a fixed set of options, answered
by a decision model (Microsoft-Decision-1 on Foundry, through the Control
Room) rather than by a keyword list nobody dares touch. It does not
generate text: it picks an option and says how sure it is.

The bot asks through the Control Room, never the model directly, and that
is the point of the design rather than a detour:

  - the model credential stays on the Control Room, where the Graph key
    and the GitHub token already live; a bot holds nothing;
  - every call is one logged run against the job that asked, with the
    inputs, every option's probability and the answer, so "why did the bot
    do that" has an answer on the job page;
  - the wording, the options and the confidence threshold are edited in
    the console with an audit trail, not shipped inside the bot, and the
    Control Room tests every change against the agent's labelled examples.

The Control Room answers; it never acts on a bot's behalf. What to do with
the answer is the bot's decision, and the Control Room's own consumers (the
failure triage that runs after a FAILED job, say) are suggestions a person
can see, not actions.

    decision = ctx.decide("failure_triage", error_text=text, bot=ctx.process_name)
    if decision["cause"] == "credentials":
        ...   # the model was sure enough; a None here means "not decided"

``decision[name]`` is the decided choice or None. It is None whenever the
agent's threshold was not met, the model picked the built-in "cannot tell"
option, or the model call failed, so a bot cannot mistake "ask a person"
for an answer. ``decision.answer(name)`` has the confidence and the
probabilities for a bot that wants to make its own call, and
``decision.raw`` is the whole response.

``DecisionUnavailable`` is raised when no answer could be had at all: no job
file (a hand run), a job file without the Control Room's URL and token (an
agent older than 0.72.0), the Decisions kill switch off in the console, an
unknown agent, a token the Control Room refuses, or the Control Room
unreachable. A bot that can carry on without the decision catches it; one
that cannot lets it fail the run, which is the right outcome.

The wire protocol is the Control Room's (``/api/v1/decisions/<name>`` in
``control_room/app.py``). The token is the job's own, minted when the job
was claimed and dead once it is terminal, good for nothing but this
endpoint, so it is read fresh from job.json on every call and never logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)

# How long one call may take. The Control Room gives the model ten seconds
# and one retry of its own, so thirty covers that with a margin; a bot on
# its critical path can pass something shorter.
DEFAULT_TIMEOUT_S = 30.0

ENDPOINT = "/api/v1/decisions/"

# Keys agent 0.72.0 writes into job.json beside job_id, bot and params.
URL_KEY = "cr_url"
TOKEN_KEY = "cr_token"

OUTCOME_DECIDED = "decided"


class DecisionUnavailable(RuntimeError):
    """No answer could be had from the Control Room. The message says why
    and names the agent; it never carries the token or the inputs."""


@dataclass(frozen=True)
class Answer:
    """One output of a decision agent, as the Control Room reported it."""

    name: str
    kind: str
    choice: str | None
    confidence: float | None
    probabilities: dict[str, float] | None
    outcome: str

    @property
    def decided(self) -> bool:
        """True when the Control Room stood behind ``choice``: the model
        was at least as sure as the agent's threshold and did not say it
        could not tell."""
        return self.outcome == OUTCOME_DECIDED and self.choice is not None


@dataclass(frozen=True)
class Decision:
    """The Control Room's answer to one ``ctx.decide()`` call."""

    run_id: int | None
    agent: str
    version: int | None
    outcome: str
    answers: dict[str, Answer]
    model: str | None
    raw: dict

    @property
    def decided(self) -> bool:
        """True when every output was decided."""
        return self.outcome == OUTCOME_DECIDED

    @property
    def choices(self) -> dict[str, str]:
        """The decided choices only, by output name."""
        return {name: a.choice for name, a in self.answers.items()
                if a.decided and a.choice is not None}

    def answer(self, name: str) -> Answer:
        """The full answer for one output. Raises KeyError for an output
        the agent does not have: that is a typo in the bot, and a silent
        None would hide it."""
        try:
            return self.answers[name]
        except KeyError:
            raise KeyError(
                f"decision agent {self.agent!r} has no output {name!r}; it "
                f"answered {sorted(self.answers) or 'nothing'}"
            ) from None

    def __getitem__(self, name: str) -> str | None:
        """The decided choice for one output, or None when it was not
        decided (below threshold, "cannot tell", or an error), so a bot can
        compare it with an option without checking first. A name the agent
        did not answer raises KeyError, unless the Control Room reported no
        answers at all (a failed model call), when it is None like the rest.
        """
        if not self.answers:
            return None
        a = self.answer(name)
        return a.choice if a.decided else None

    def __contains__(self, name: object) -> bool:
        return name in self.answers


def read_endpoint(job_file: Path | str | None, agent: str) -> tuple[str, str, Any]:
    """The Control Room base URL, the job token and the job id, read fresh
    from job.json. Raises DecisionUnavailable when there is no job file or
    it carries no URL and token."""
    if job_file is None:
        raise DecisionUnavailable(
            f"cannot ask decision agent {agent!r}: this run has no Control "
            "Room job file (a hand run), so there is nothing to ask"
        )
    try:
        with open(job_file, encoding="utf-8") as fh:
            job = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DecisionUnavailable(
            f"cannot ask decision agent {agent!r}: the job file could not "
            f"be read ({exc.__class__.__name__})"
        ) from exc
    url = job.get(URL_KEY) if isinstance(job, dict) else None
    token = job.get(TOKEN_KEY) if isinstance(job, dict) else None
    if not (isinstance(url, str) and url and isinstance(token, str) and token):
        raise DecisionUnavailable(
            f"cannot ask decision agent {agent!r}: the job file has no "
            f"{URL_KEY}/{TOKEN_KEY}, so this job was started by a Control "
            "Room agent older than 0.72.0 or outside the Control Room"
        )
    return url.rstrip("/"), token, job.get("job_id")


def _user_agent() -> str:
    # Imported here, not at module level: the package's __init__ imports
    # this module, so a top-level import of the version would be circular.
    from . import __version__

    return f"automation-core/{__version__}"


def _parse(agent: str, body: Any) -> Decision:
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise DecisionUnavailable(
            f"decision agent {agent!r}: the Control Room's reply was not a "
            "decision (no answers object)"
        )
    answers: dict[str, Answer] = {}
    for name, a in body["answers"].items():
        if not isinstance(a, dict):
            continue
        probs = a.get("probabilities")
        answers[str(name)] = Answer(
            name=str(name),
            kind=str(a.get("kind") or ""),
            choice=a.get("choice") if isinstance(a.get("choice"), str) else None,
            confidence=(float(a["confidence"])
                        if isinstance(a.get("confidence"), (int, float)) else None),
            probabilities=({str(k): float(v) for k, v in probs.items()
                            if isinstance(v, (int, float))}
                           if isinstance(probs, dict) else None),
            outcome=str(a.get("outcome") or ""),
        )
    return Decision(
        run_id=body.get("run_id") if isinstance(body.get("run_id"), int) else None,
        agent=str(body.get("agent") or agent),
        version=body.get("version") if isinstance(body.get("version"), int) else None,
        outcome=str(body.get("outcome") or ""),
        answers=answers,
        model=body.get("model") if isinstance(body.get("model"), str) else None,
        raw=body,
    )


def decide(
    job_file: Path | str | None,
    agent: str,
    inputs: Mapping[str, Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    post: Callable[..., Any] | None = None,
) -> Decision:
    """Ask the Control Room's decision agent ``agent`` about ``inputs``.

    ``Context.decide()`` is the way to call this; it is a function so a
    test can hand it a job file and a fake ``post``. Retries once on a
    connection error (the request never reached the Control Room, so
    nothing was logged twice); never on a timeout or an HTTP status.
    """
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("decide() needs the agent's name")
    agent = agent.strip()
    fields = dict(inputs)
    try:
        json.dumps(fields)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"decision agent {agent!r}: an input is not JSON-serialisable "
            f"({exc})"
        ) from exc
    url, token, job_id = read_endpoint(job_file, agent)
    sender = post if post is not None else requests.post
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _user_agent(),
    }
    body = {"inputs": fields, "job_id": job_id}
    target = f"{url}{ENDPOINT}{agent}"
    response = None
    for attempt in (1, 2):
        try:
            response = sender(target, json=body, headers=headers, timeout=timeout_s)
            break
        except requests.exceptions.ReadTimeout as exc:
            # The Control Room may have logged the run by now; asking again
            # would be a second run for one question.
            raise DecisionUnavailable(
                f"decision agent {agent!r}: the Control Room did not answer "
                f"within {timeout_s:g}s"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            if attempt == 1:
                _log.warning(
                    "decision agent %r: could not reach the Control Room "
                    "(%s); trying once more", agent, exc.__class__.__name__,
                )
                continue
            raise DecisionUnavailable(
                f"decision agent {agent!r}: the Control Room could not be "
                f"reached ({exc.__class__.__name__})"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise DecisionUnavailable(
                f"decision agent {agent!r}: the request failed "
                f"({exc.__class__.__name__})"
            ) from exc
    assert response is not None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code != 200:
        detail = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            detail = f": {payload['error']}"
        raise DecisionUnavailable(
            f"decision agent {agent!r}: the Control Room answered HTTP "
            f"{response.status_code}{detail}"
        )
    decision = _parse(agent, payload)
    chosen = ", ".join(f"{k}={v}" for k, v in decision.choices.items()) or "nothing decided"
    if decision.outcome == "error":
        _log.warning(
            "decision agent %r: run %s ended in error (%s); no choice was made",
            agent, decision.run_id, decision.raw.get("error") or "no detail",
        )
    else:
        _log.info(
            "decision agent %r: run %s, outcome %s, %s",
            agent, decision.run_id, decision.outcome or "unknown", chosen,
        )
    return decision
