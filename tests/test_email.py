from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from automation_core.context import Context
from automation_core.email import (
    AttachedFile,
    Banner,
    Button,
    Column,
    Email,
    EmailType,
    FolderLink,
    Group,
    Kpi,
    Section,
    Status,
    Table,
    WebLink,
    send_email,
    split_recipients,
    subject_line,
)
from automation_core.email import graph
from automation_core.email.formatting import (
    file_url,
    format_date,
    format_datetime,
    format_money,
    format_number,
    format_percent,
    format_size,
    format_time,
    percent_of,
)
from automation_core.errors import ErrorCollector
from automation_core.notifications.alert_email import build_alert_email


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestFormatting:
    def test_numbers_have_separators(self):
        assert format_number(27674) == "27,674"
        assert format_number(1234.567, 2) == "1,234.57"

    def test_percent(self):
        assert format_percent(55.83) == "55.8%"
        assert percent_of(1, 8) == "12.5%"
        assert percent_of(5, 0) == "0.0%"

    def test_money(self):
        assert format_money(1234.5) == "£1,234.50"
        assert format_money(-3) == "-£3.00"

    def test_sizes(self):
        assert format_size(820 * 1024) == "820 KB"
        assert format_size(5 * 1024 * 1024) == "5 MB"
        assert format_size(int(4.2 * 1024 * 1024)) == "4.2 MB"
        assert format_size(10) == "1 KB"

    def test_winter_is_gmt(self):
        assert format_time(_utc(2026, 1, 15, 14, 30)) == "14:30 GMT"

    def test_summer_is_bst(self):
        assert format_time(_utc(2026, 7, 1, 13, 30)) == "14:30 BST"

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            # 2026: BST starts 29 March 01:00 UTC, ends 25 October 01:00 UTC.
            (_utc(2026, 3, 29, 0, 59), "29/03/2026 00:59 GMT"),
            (_utc(2026, 3, 29, 1, 0), "29/03/2026 02:00 BST"),
            (_utc(2026, 10, 25, 0, 59), "25/10/2026 01:59 BST"),
            (_utc(2026, 10, 25, 1, 0), "25/10/2026 01:00 GMT"),
        ],
    )
    def test_clock_change_boundaries(self, moment, expected):
        assert format_datetime(moment) == expected

    def test_weekday(self):
        assert format_datetime(_utc(2026, 10, 27, 14, 30), weekday=True) == "Tue 27/10/2026 14:30 GMT"

    def test_date_uses_uk_day(self):
        # 23:30 UTC in summer is already the next day in the UK.
        assert format_date(_utc(2026, 7, 1, 23, 30)) == "02/07/2026"
        assert format_date(date(2026, 10, 27)) == "27/10/2026"

    def test_unc_file_url(self):
        path = r"\\inspiredenergysolutions.local\DFS\Public\!IES\BPI\Automation Team\Reports"
        assert file_url(path) == (
            "file://///inspiredenergysolutions.local/DFS/Public/!IES/BPI/Automation%20Team/Reports"
        )

    def test_drive_file_url(self):
        assert file_url(r"C:\Reports\My file.xlsx") == "file:///C:/Reports/My%20file.xlsx"


def _report(**overrides) -> Email:
    fields = dict(
        type=EmailType.REPORT,
        subject="Thames Water reads: data imports MI report | 27/10/2026",
        preheader="11,832 reads imported to date. 37,733 rows failed and are in the report.",
        title="Data imports MI report",
        meta="Generated Tue 27/10/2026 14:30 GMT · All-time read rows",
        summary="11,832 reads imported.",
        process_name="Data imports MI report (Thames Water reads)",
    )
    fields.update(overrides)
    return Email(**fields)


def _rows_in(html: str) -> int:
    tbody = html[html.index("<tbody>") : html.index("</tbody>")]
    return tbody.count("<tr>")


class TestRender:
    def test_shell_and_footer_filled(self):
        html = _report().render()
        assert html.startswith("<!DOCTYPE html>")
        assert 'lang="en-GB"' in html
        assert "Process: Data imports MI report (Thames Water reads)" in html
        assert "<title>Thames Water reads: data imports MI report | 27/10/2026</title>" in html
        assert "This email was sent by Inspired Automation." in html
        # Every placeholder in the shell was filled, and none of the template's
        # example components leaked through.
        assert "{{" not in html
        assert "KPI_1_VALUE" not in html
        # Outlook's conditional comment survives the comment stripping.
        assert "<!--[if mso]>" in html
        assert "<!-- " not in html

    def test_values_are_escaped(self):
        html = _report(
            title="A & B <script>",
            summary="x < y & z",
            process_name="P<1>",
        ).render()
        assert "A &amp; B &lt;script&gt;" in html
        assert "x &lt; y &amp; z" in html
        assert "Process: P&lt;1&gt;" in html
        assert "<script>" not in html

    def test_component_order(self):
        html = _report(
            status=Banner(Status.WARNING, "3 meters have had no import in the last 7 days."),
            kpis=[Kpi(1, "One"), Kpi(2, "Two")],
            sections=[Section("Results", paragraphs=["Body."])],
            files=[WebLink("https://example.com", "Open the dashboard")],
            about="About text.",
        ).render()
        order = ["Automated report", "Needs attention", "11,832 reads imported.", "One", "Results",
                 "Open the dashboard", "About this report", "This email was sent"]
        positions = [html.index(marker) for marker in order]
        assert positions == sorted(positions)

    @pytest.mark.parametrize("email_type", list(EmailType))
    def test_header_band_carries_wordmark_and_type(self, email_type):
        kwargs = {"type": email_type}
        if email_type is not EmailType.REPORT:
            status = Status.WARNING if email_type is not EmailType.NOTIFICATION else Status.INFO
            kwargs["status"] = Banner(status, "Sentence.")
            kwargs["sections"] = [Section("What to do", steps=["Do it."])]
            kwargs["button"] = Button("Review it", "https://example.com")
        html = _report(**kwargs).render()
        band = html[html.index('<span style="font-weight:600;">Inspired</span> Automation') :]
        # The type sits in the band, once, and before the title: there is no
        # second copy of it above the title any more.
        assert html.count(f">{email_type.label}<") == 1
        assert band.index(email_type.label) < band.index("Data imports MI report")

    @pytest.mark.parametrize(("count", "width"), [(2, 284), (3, 186), (4, 284)])
    def test_kpi_layouts(self, count, width):
        html = _report(kpis=[Kpi(i * 1000, f"K{i}") for i in range(count)]).render()
        assert html.count(f'width="{width}" valign="top"') == count
        assert "3,000" in html or count < 4

    @pytest.mark.parametrize("count", [1, 5])
    def test_kpi_count_enforced(self, count):
        with pytest.raises(ValueError, match="two to four"):
            _report(kpis=[Kpi(i, "K") for i in range(count)])

    def test_alert_needs_error_or_warning_banner_and_steps(self):
        steps = [Section("What to do", steps=["Do it."])]
        with pytest.raises(ValueError, match="status banner"):
            _report(type=EmailType.ALERT, sections=steps)
        with pytest.raises(ValueError, match="ERROR, WARNING"):
            _report(type=EmailType.ALERT, sections=steps, status=Banner(Status.SUCCESS, "ok"))
        with pytest.raises(ValueError, match="What to do"):
            _report(type=EmailType.ALERT, status=Banner(Status.ERROR, "bad"))
        _report(type=EmailType.ALERT, sections=steps, status=Banner(Status.ERROR, "bad"))

    def test_request_needs_button(self):
        with pytest.raises(ValueError, match="button"):
            _report(type=EmailType.REQUEST, status=Banner(Status.WARNING, "Review"))
        html = _report(
            type=EmailType.REQUEST,
            status=Banner(Status.WARNING, "Review"),
            button=Button("Review the exceptions", "https://example.com/?a=1&b=2"),
        ).render()
        assert "Action needed" in html
        assert 'href="https://example.com/?a=1&amp;b=2"' in html

    def test_list_length_enforced(self):
        with pytest.raises(ValueError, match="list items"):
            _report(sections=[Section("Steps", steps=[str(i) for i in range(7)])])

    def test_summary_required(self):
        with pytest.raises(ValueError, match="summary"):
            _report(summary=[])

    def test_long_subject_warns_but_renders(self, caplog):
        with caplog.at_level(logging.WARNING):
            _report(subject="x" * 81).render()
        assert any("subject is 81 characters" in r.message for r in caplog.records)

    def test_short_preheader_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            _report(preheader="Too short").render()
        assert any("preheader" in r.message for r in caplog.records)


class TestTable:
    def _html(self, table: Table) -> str:
        return _report(sections=[Section("Data", table=table)]).render()

    def test_numeric_columns_right_aligned_with_separators(self):
        html = self._html(Table(["Category", "Rows", "% of rows"], [["Imported", 11832, "23.9%"]]))
        assert '<th scope="col" align="left"' in html
        assert html.count('<th scope="col" align="right"') == 2
        assert '<td align="right" style="padding:10px 8px 10px 8px;' in html
        assert ">11,832</td>" in html

    def test_text_column_with_ints_is_not_reformatted(self):
        html = self._html(Table(["Site", Column("MPAN")], [["A", 1234567890123]]))
        assert ">1234567890123</td>" in html
        assert html.count('<th scope="col" align="right"') == 0

    def test_cut_to_fifteen_rows_with_note(self):
        rows = [[f"Row {i}", i] for i in range(40)]
        html = self._html(Table(["Name", "Value"], rows))
        assert _rows_in(html) == 15
        assert "Showing 15 of 40 rows. The full list is in the attached report." in html

    def test_total_rows_from_caller(self):
        rows = [[f"Row {i}", i] for i in range(15)]
        html = self._html(Table(["Name", "Value"], rows, total_rows=2341))
        assert "Showing 15 of 2,341 rows." in html

    def test_no_note_when_nothing_cut(self):
        html = self._html(Table(["Name", "Value"], [["a", 1]]))
        assert "Showing" not in html

    def test_groups_and_total(self):
        html = self._html(
            Table(
                ["Category", "Rows"],
                [Group("Failed matching", [["Multiple matches", 2], ["No match", 3]])],
                total=["Total", 5],
            )
        )
        assert "Failed matching</td>" in html
        assert 'colspan="2"' in html
        assert "border-top:2px solid #18073a; font-weight:600;" in html

    def test_group_rows_count_toward_the_cap(self):
        html = self._html(
            Table(
                ["Category", "Rows"],
                [
                    Group("First", [[f"a{i}", i] for i in range(10)]),
                    Group("Second", [[f"b{i}", i] for i in range(10)]),
                    Group("Third", [["c", 1]]),
                ],
            )
        )
        assert "Second</td>" in html
        assert "Third</td>" not in html
        assert "Showing 15 of 21 rows." in html


class TestFiles:
    def test_folder_link_has_encoded_href_and_path_block(self):
        path = r"\\server\share\Automation Team\Reports"
        html = _report(files=[FolderLink(path)]).render()
        assert 'href="file://///server/share/Automation%20Team/Reports"' in html
        assert ">Open the report folder</a>" in html
        assert "\\\\server\\share\\Automation Team\\Reports</td>" in html

    def test_attached_file_line(self, tmp_path):
        report = tmp_path / "MI.xlsx"
        report.write_bytes(b"x" * (5 * 1024 * 1024))
        html = _report(files=[AttachedFile.from_path(report)]).render()
        assert "MI.xlsx (5 MB), attached" in html


class TestSubjectLine:
    def test_routine_report(self):
        assert subject_line("Thames Water reads: data imports MI report", date(2026, 10, 27)) == (
            "Thames Water reads: data imports MI report | 27/10/2026"
        )

    def test_failed_with_time(self):
        assert subject_line("Sugar to HubSpot sync", _utc(2026, 10, 27, 6, 0), prefix="Failed") == (
            "Failed: Sugar to HubSpot sync | 27/10/2026 06:00 GMT"
        )

    def test_unknown_prefix_rejected(self):
        with pytest.raises(ValueError):
            subject_line("x", date(2026, 1, 1), prefix="Urgent")


GRAPH_CONFIG = {
    "graph": {
        "client_id": "cid",
        "client_secret": "sec",
        "tenant_id": "tid",
        "sender_address": "bot@example.com",
    }
}


def _ok(json_body: dict | None = None) -> MagicMock:
    response = MagicMock(ok=True, status_code=200)
    response.json.return_value = json_body or {}
    return response


@pytest.fixture
def graph_http():
    with (
        patch.object(graph, "get_access_token", return_value="tok"),
        patch.object(graph.requests, "post") as post,
        patch.object(graph.requests, "put") as put,
        patch.object(graph.requests, "delete") as delete,
    ):
        yield post, put, delete


class TestSendEmail:
    def test_split_recipients(self):
        assert split_recipients("a@x.com; b@x.com, c@x.com") == ["a@x.com", "b@x.com", "c@x.com"]
        assert split_recipients(["a@x.com", "b@x.com;c@x.com"]) == ["a@x.com", "b@x.com", "c@x.com"]
        assert split_recipients("") == []

    def test_small_message_uses_send_mail(self, graph_http, tmp_path):
        post, put, _ = graph_http
        post.return_value = _ok()
        attachment = tmp_path / "r.xlsx"
        attachment.write_bytes(b"data")
        send_email(
            "Subj", "<html></html>", "a@x.com; b@x.com", bcc="c@x.com",
            attachments=[attachment], importance="high", config=GRAPH_CONFIG,
        )
        post.assert_called_once()
        assert post.call_args.args[0].endswith("/users/bot@example.com/sendMail")
        message = post.call_args.kwargs["json"]["message"]
        assert message["body"] == {"contentType": "HTML", "content": "<html></html>"}
        assert message["importance"] == "high"
        assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == ["a@x.com", "b@x.com"]
        assert message["bccRecipients"][0]["emailAddress"]["address"] == "c@x.com"
        assert message["attachments"][0]["name"] == "r.xlsx"
        put.assert_not_called()

    def test_large_attachment_uses_draft_and_upload_session(self, graph_http, tmp_path):
        post, put, delete = graph_http
        post.side_effect = [
            _ok({"id": "MSG1"}),  # create draft
            _ok({"uploadUrl": "https://upload.example/session"}),  # createUploadSession
            _ok(),  # send
        ]
        put.return_value = _ok()
        size = graph.UPLOAD_CHUNK_BYTES * 2 + 100
        big = tmp_path / "big.xlsx"
        big.write_bytes(b"x" * size)

        send_email("Subj", "<html></html>", "a@x.com", attachments=[big], config=GRAPH_CONFIG)

        urls = [c.args[0] for c in post.call_args_list]
        assert urls[0].endswith("/users/bot@example.com/messages")
        assert urls[1].endswith("/messages/MSG1/attachments/createUploadSession")
        assert urls[2].endswith("/messages/MSG1/send")
        assert put.call_count == 3
        ranges = [c.kwargs["headers"]["Content-Range"] for c in put.call_args_list]
        chunk = graph.UPLOAD_CHUNK_BYTES
        assert ranges == [
            f"bytes 0-{chunk - 1}/{size}",
            f"bytes {chunk}-{2 * chunk - 1}/{size}",
            f"bytes {2 * chunk}-{size - 1}/{size}",
        ]
        # The pre-authenticated upload URL must not get the bearer token.
        assert all("Authorization" not in c.kwargs["headers"] for c in put.call_args_list)
        delete.assert_not_called()

    def test_failed_draft_send_deletes_draft_and_raises(self, graph_http, tmp_path):
        post, put, delete = graph_http
        failed = MagicMock(ok=False, status_code=400, text="bad request body")
        post.side_effect = [_ok({"id": "MSG1"}), _ok({"uploadUrl": "u"}), failed]
        put.return_value = _ok()
        big = tmp_path / "big.xlsx"
        big.write_bytes(b"x" * (graph.SIMPLE_ATTACHMENT_LIMIT + 1))

        with pytest.raises(graph.requests.HTTPError, match="bad request body"):
            send_email("Subj", "<p></p>", "a@x.com", attachments=[big], config=GRAPH_CONFIG)
        assert delete.call_args.args[0].endswith("/messages/MSG1")

    def test_graph_error_text_is_kept(self, graph_http):
        post, _, _ = graph_http
        post.return_value = MagicMock(ok=False, status_code=400, text="ErrorInvalidRecipients")
        with pytest.raises(graph.requests.HTTPError, match="ErrorInvalidRecipients"):
            send_email("Subj", "<p></p>", "a@x.com", config=GRAPH_CONFIG)

    def test_no_recipients_rejected(self, graph_http):
        with pytest.raises(ValueError, match="recipient"):
            send_email("Subj", "<p></p>", " ; ", config=GRAPH_CONFIG)


def _alert_ctx(job_id: int | None = 555) -> Context:
    return Context(
        process_name="TestProcess",
        log_file=Path(r"\\server\logs\TestProcess_20261027_143000_job555.log"),
        is_production=True,
        notification_method="email",
        notification_recipient="team@example.com",
        job_id=job_id,
    )


def _alert(errors: ErrorCollector, *, is_critical: bool, exc_info=None, job_id=555) -> Email:
    return build_alert_email(
        _alert_ctx(job_id),
        errors,
        is_critical=is_critical,
        exc_info=exc_info,
        now=_utc(2026, 10, 27, 14, 30),
        hostname="RDS01",
        username="svc_bot",
    )


class TestAlertEmail:
    def test_critical_alert(self):
        try:
            raise ValueError("bad <input>")
        except ValueError:
            exc_info = sys.exc_info()
        message = _alert(ErrorCollector(), is_critical=True, exc_info=exc_info)
        html = message.render()
        assert message.subject == "Failed: TestProcess | 27/10/2026 14:30 GMT"
        assert "TestProcess failed" in html
        assert "Failed</strong>" in html
        assert "The run stopped on an unhandled ValueError." in html
        assert "TestProcess stopped on RDS01 at 14:30 GMT with ValueError: bad &lt;input&gt;." in html
        assert "Traceback (most recent call last):" in html
        assert "Control Room job 555" in html
        assert 'href="file://///server/logs/TestProcess_20261027_143000_job555.log"' in html

    def test_summary_alert_lists_issues(self):
        errors = ErrorCollector()
        errors.add("Row 4 failed", details={"mpan": "1234567890123"})
        errors.add("Row 9 failed", exception=KeyError("site"))
        message = _alert(errors, is_critical=False, job_id=None)
        html = message.render()
        assert message.subject == "Action needed: TestProcess: 2 issues during run | 27/10/2026 14:30 GMT"
        assert "Needs attention</strong>" in html
        assert "2 issues need checking." in html
        assert "Row 4 failed" in html
        assert "{&quot;mpan&quot;: &quot;1234567890123&quot;}" in html
        assert "KeyError: &#x27;site&#x27;" in html
        assert "Hand run" in html
        assert "Traceback" not in html

    def test_single_issue_grammar(self):
        errors = ErrorCollector()
        errors.add("one")
        html = _alert(errors, is_critical=False).render()
        assert "1 issue needs checking." in html

    def test_many_issues_cut_to_fifteen(self):
        errors = ErrorCollector()
        for i in range(20):
            errors.add(f"issue {i}")
        html = _alert(errors, is_critical=False).render()
        assert "Showing 15 of 20 rows. The full list is in the log file." in html

    def test_long_traceback_cut_from_the_top(self, monkeypatch):
        # Python collapses repeated frames, so a deep recursion does not make
        # a long traceback; lower the limit instead.
        monkeypatch.setattr("automation_core.notifications.alert_email.MAX_TRACEBACK_LINES", 2)
        try:
            raise RuntimeError("deep")
        except RuntimeError:
            exc_info = sys.exc_info()
        html = _alert(ErrorCollector(), is_critical=True, exc_info=exc_info).render()
        assert re.search(r"\(\d+ earlier lines not shown", html)
        assert "RuntimeError: deep" in html
