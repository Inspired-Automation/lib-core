"""
Tests for the Energy Manager knowledge base.

The YAML map is what a bot addresses controls through, so it is worth
guarding: a typo in it produces a bot that fails at runtime against a live
application, which is the most expensive place to find out.

These tests assert the map's shape and the facts that were established by
measuring the live application. They do not need pywinauto or a GUI: the map
is loaded with PyYAML directly, so they run anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MAP_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "automation_core"
    / "gui"
    / "apps"
    / "energy_manager.yaml"
)


@pytest.fixture(scope="module")
def em_map() -> dict:
    with MAP_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class TestMapShape:
    def test_map_file_ships_with_the_package(self):
        assert MAP_PATH.exists(), f"knowledge base missing at {MAP_PATH}"

    def test_top_level_sections(self, em_map):
        for section in ("meta", "backends", "never_use", "credentials",
                        "flows", "windows", "forms", "hotkeys", "ribbon",
                        "not_yet_mapped"):
            assert section in em_map, f"missing section {section!r}"

    def test_meta_identifies_the_build(self, em_map):
        """The class-name suffix changes when EM is rebuilt, which is the
        signal to re-harvest the map.
        """
        assert em_map["meta"]["process_name"] == "EM.exe"
        assert em_map["meta"]["build_marker"]
        assert em_map["meta"]["harvested"]


class TestBackends:
    def test_forms_use_win32_and_drawn_controls_use_uia(self, em_map):
        """Measured, not assumed.

        The generalisation matters: it is not "ribbons need UIA", it is
        "anything DRAWN rather than created as a child window needs UIA".
        Confirmed three times over: the DevExpress ribbon, the WinForms
        ToolStrip on frmProfileCollectorList (win32 reports zero children),
        and DevExpress grid cells.
        """
        regions = em_map["backends"]["regions"]
        assert regions["forms"]["backend"] == "win32"
        assert regions["drawn_controls"]["backend"] == "uia"

    def test_drawn_controls_lists_what_it_covers(self, em_map):
        applies = em_map["backends"]["regions"]["drawn_controls"]["applies_to"]
        joined = " ".join(applies).lower()
        assert "rbnmain" in joined, "the DevExpress ribbon"
        assert "tsmain" in joined, "the WinForms ToolStrip"
        assert "grid" in joined, "DevExpress grid cells"

    def test_every_region_explains_itself(self, em_map):
        for name, region in em_map["backends"]["regions"].items():
            assert region.get("why"), f"region {name!r} has no justification"


class TestMapIsParseable:
    """The map is prose-heavy, and prose contains colons and quotes. A block
    scalar that is not quoted properly turns a sentence into a YAML mapping
    and breaks the whole file, which is a silent way to ship a broken map.
    """

    def test_file_parses(self):
        with MAP_PATH.open(encoding="utf-8") as handle:
            assert yaml.safe_load(handle) is not None

    def test_prose_fields_survive_their_punctuation(self, em_map):
        for entry in em_map["not_yet_mapped"]:
            assert isinstance(entry, str), (
                f"not_yet_mapped entry parsed as {type(entry).__name__}, not a "
                "string: a colon in the prose was read as YAML structure"
            )
        for entry in em_map["never_use"]:
            assert isinstance(entry.get("why"), str)


class TestCredentials:
    def test_two_distinct_credentials(self, em_map):
        """The application login and the Web Extensions login are different
        accounts. An earlier spike used one value for both, so its login
        could never have worked.
        """
        creds = em_map["credentials"]
        assert creds["application"]["username_key"] == "app_username"
        assert creds["application"]["password_key"] == "app_password"
        assert creds["web_extensions"]["username_key"] == "web_username"
        assert creds["web_extensions"]["password_key"] == "web_password"

    def test_credential_keys_are_all_distinct(self, em_map):
        keys = [
            em_map["credentials"][group][field]
            for group in ("application", "web_extensions")
            for field in ("username_key", "password_key")
        ]
        assert len(set(keys)) == len(keys)


class TestWindows:
    def test_login_window(self, em_map):
        login = em_map["windows"]["login"]
        assert login["caption"] == "Enter Password"
        assert login["automation_id"] == "LoginForm"
        for control in ("cmbNames", "txtPassword", "cmdOk", "cmbClients"):
            assert control in login["controls"]

    def test_client_group_window_follows_login(self, em_map):
        group = em_map["windows"]["client_group"]
        assert group["caption"] == "Client Group"
        assert group["automation_id"] == "ClientGroupForm"
        assert "cmdOk" in group["controls"]

    def test_error_dialog_is_a_plain_messagebox(self, em_map):
        """EM's error dialogs are classic Win32 MessageBoxes with no designer
        names, unlike the rest of the application, so they need a different
        matching strategy.
        """
        error = em_map["windows"]["error"]
        assert error["class_name"] == "#32770"
        assert "process id" in error["note"]


class TestForms:
    def test_web_extensions_controls(self, em_map):
        controls = em_map["forms"]["frmWeb"]["controls"]
        for control in ("txtEmail", "txtPassword", "btnDownloadProfileData",
                        "cmdUpload", "cmdClose", "grpUserDetails"):
            assert control in controls

    def test_txt_email_is_actually_a_username(self, em_map):
        """The field is named txtEmail but labelled "Username:" and takes a
        username, which is exactly the sort of thing that wastes an hour.
        """
        field = em_map["forms"]["frmWeb"]["controls"]["txtEmail"]
        assert field["label"] == "Username:"

    def test_datasets_caption_is_flagged_as_unstable(self, em_map):
        """frmDataSets' caption carries the current data set name, so it must
        never be used as a selector.
        """
        form = em_map["forms"]["frmDataSets"]
        assert form["caption_varies"] is True
        assert "caption" not in form

    def test_tsmain_is_recorded_as_ambiguous(self, em_map):
        """tsMain exists in three forms, which is why every lookup must be
        scoped to its owning form.
        """
        owners = [
            name for name, form in em_map["forms"].items()
            if "tsMain" in (form.get("controls") or {})
        ]
        assert len(owners) >= 2, "tsMain's ambiguity should be visible in the map"


class TestHotkeys:
    def test_web_extensions_shortcut(self, em_map):
        hotkey = em_map["hotkeys"]["web_extensions"]
        assert hotkey["keys"] == "Ctrl+Alt+1"
        assert hotkey["send_keys"] == "^%1"
        assert hotkey["opens"] == "frmWeb"

    def test_send_keys_matches_the_translator(self, em_map):
        """The stored spec must agree with what the translator produces, so
        the map cannot drift from the code.
        """
        from automation_core.gui.keys import to_send_keys

        for name, entry in em_map["hotkeys"].items():
            assert to_send_keys(entry["keys"]) == entry["send_keys"], name

    def test_every_hotkey_names_what_it_opens(self, em_map):
        for name, entry in em_map["hotkeys"].items():
            assert entry.get("opens"), f"hotkey {name!r} does not say what it opens"


class TestHonesty:
    def test_unmapped_areas_are_recorded(self, em_map):
        """A map that quietly omits what it does not cover is worse than one
        that says so, because it gets believed.
        """
        unmapped = em_map["not_yet_mapped"]
        assert unmapped, "an incomplete map must say what is missing"
        joined = " ".join(unmapped).lower()
        assert "progress" in joined or "completion" in joined
        assert "grid" in joined

    def test_never_use_records_the_traps(self, em_map):
        fields = {entry["field"] for entry in em_map["never_use"]}
        assert "class_name" in fields
        assert "control_id" in fields
        for entry in em_map["never_use"]:
            assert entry.get("why"), f"{entry['field']} has no explanation"
