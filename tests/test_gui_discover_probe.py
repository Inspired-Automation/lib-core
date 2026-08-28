"""
Unit tests for the pure logic in automation_core.gui.discover.probe.

Everything here runs without a GUI, a live application or COM. The parts
that need a real window (point probing, selector verification, tree walking)
are exercised by pointing the discovery tools at a live application.

The fixtures are real values measured from Energy Manager and from
charmap.exe, not invented ones, so a change that breaks the WinForms
handling shows up here rather than in a bot three months from now.

Hotkey translation is not tested here: it lives in automation_core.gui.keys
and is covered by test_gui_keys.py.
"""

from __future__ import annotations

import ast
import re
import unittest

from automation_core.gui.discover import probe

# Measured from Energy Manager, 2026-08-27.
EM_EDIT_CLASS = "WindowsForms10.EDIT.app.0.1a0e24_r7_ad1"
EM_BUTTON_CLASS = "WindowsForms10.BUTTON.app.0.1a0e24_r7_ad1"
EM_WINDOW_CLASS = "WindowsForms10.Window.8.app.0.1a0e24_r7_ad1"
EM_STATIC_CLASS = "WindowsForms10.STATIC.app.0.1a0e24_r7_ad1"


def node(
    control_type: str | None,
    name: str | None = None,
    automation_id: str | None = None,
    class_name: str | None = None,
    control_id: int | None = None,
    children: list[dict] | None = None,
    **extra: object,
) -> dict:
    """Build a tree node of the shape walk_tree produces."""
    built = {
        "control_type": control_type,
        "name": name,
        "automation_id": automation_id,
        "class_name": class_name,
        "control_id": control_id,
        "children": children or [],
    }
    built.update(extra)
    return built


class TestIsSynthesisedAutoId(unittest.TestCase):
    """UIA fabricates an automation_id from the Win32 control id for classic
    dialogs, so a purely numeric id carries no more information than the
    control id does. Telling the two apart drives the backend recommendation.
    """

    def test_numeric_id_is_synthesised(self) -> None:
        self.assertTrue(probe.is_synthesised_auto_id("103"))
        self.assertTrue(probe.is_synthesised_auto_id("0"))
        self.assertTrue(probe.is_synthesised_auto_id(103))

    def test_named_id_is_not_synthesised(self) -> None:
        for value in ("cmdOk", "txtPassword", "frmMain", "btn103", "103a"):
            with self.subTest(value=value):
                self.assertFalse(probe.is_synthesised_auto_id(value))

    def test_empty_is_not_synthesised(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertFalse(probe.is_synthesised_auto_id(value))


class TestNodeLabel(unittest.TestCase):
    def label(self, n: dict) -> str:
        # flatten_tree of a childless node yields exactly one path, "/<label>".
        return next(iter(probe.flatten_tree(n))).lstrip("/")

    def test_named_automation_id_wins(self) -> None:
        self.assertEqual(
            self.label(node("Button", name="Ok", automation_id="cmdOk")),
            "Button[cmdOk]",
        )

    def test_name_used_when_automation_id_is_numeric(self) -> None:
        self.assertEqual(
            self.label(node("Button", name="Select", automation_id="103")),
            "Button[Select]",
        )

    def test_numeric_id_used_when_there_is_no_name(self) -> None:
        self.assertEqual(self.label(node("Edit", automation_id="701")), "Edit#701")

    def test_control_id_is_the_last_resort(self) -> None:
        self.assertEqual(self.label(node("Edit", control_id=132)), "Edit#132")

    def test_bare_control_type_when_nothing_identifies_it(self) -> None:
        self.assertEqual(self.label(node("Pane")), "Pane")

    def test_class_name_stands_in_for_missing_control_type(self) -> None:
        n = node(None, name="Character Grid", class_name="CharGridWClass")
        self.assertEqual(self.label(n), "CharGridWClass[Character Grid]")


class TestFlattenTree(unittest.TestCase):
    def test_paths_are_nested(self) -> None:
        tree = node(
            "Window",
            name="SystemsLink",
            children=[node("Button", name="Ok", automation_id="cmdOk")],
        )
        flat = probe.flatten_tree(tree)
        self.assertIn("/Window[SystemsLink]", flat)
        self.assertIn("/Window[SystemsLink]/Button[cmdOk]", flat)
        self.assertEqual(len(flat), 2)

    def test_duplicate_siblings_get_occurrence_indices(self) -> None:
        tree = node("Window", name="W",
                    children=[node("Pane"), node("Pane"), node("Pane")])
        flat = probe.flatten_tree(tree)
        for expected in ("/Window[W]/Pane", "/Window[W]/Pane(2)",
                         "/Window[W]/Pane(3)"):
            self.assertIn(expected, flat)
        self.assertEqual(len(flat), 4)

    def test_first_occurrence_is_unsuffixed(self) -> None:
        """A control that gains a duplicate sibling between two snapshots
        keeps its original path, so the diff reports one addition rather than
        renaming the control that was already there.
        """
        before = probe.flatten_tree(node("Window", name="W", children=[node("Pane")]))
        after = probe.flatten_tree(
            node("Window", name="W", children=[node("Pane"), node("Pane")])
        )
        self.assertIn("/Window[W]/Pane", before)
        self.assertIn("/Window[W]/Pane", after)
        self.assertEqual(sorted(set(after) - set(before)), ["/Window[W]/Pane(2)"])

    def test_sibling_label_prefix_does_not_bleed(self) -> None:
        """Regression: keying used to be rewritten with a startswith test, so
        a second 'Button' sibling would also rewrite paths belonging to a
        sibling called 'Button2', silently corrupting both subtrees.
        """
        tree = node(
            "Window",
            name="W",
            children=[
                node("Button2", children=[node("Text", name="inside-button2")]),
                node("Button", children=[node("Text", name="inside-button-a")]),
                node("Button", children=[node("Text", name="inside-button-b")]),
            ],
        )
        flat = probe.flatten_tree(tree)

        self.assertIn("/Window[W]/Button2", flat)
        self.assertIn("/Window[W]/Button2/Text[inside-button2]", flat)
        self.assertIn("/Window[W]/Button/Text[inside-button-a]", flat)
        self.assertIn("/Window[W]/Button(2)/Text[inside-button-b]", flat)
        self.assertNotIn("/Window[W]/Button2(2)", flat)
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(len(flat), 7)

    def test_all_paths_unique_in_a_wide_tree(self) -> None:
        tree = node(
            "Window",
            name="W",
            children=[
                node("Pane", children=[node("Button"), node("Button")]),
                node("Pane", children=[node("Button"), node("Button")]),
            ],
        )
        flat = probe.flatten_tree(tree)
        self.assertEqual(len(flat), 7)
        self.assertEqual(len(flat), len(set(flat)))


class TestVolatileClassName(unittest.TestCase):
    """WinForms class names end in a per-process suffix that changes on
    rebuild, so the literal string must never become a selector.
    """

    def test_winforms_classes_are_volatile(self) -> None:
        for class_name in (EM_EDIT_CLASS, EM_BUTTON_CLASS,
                           EM_WINDOW_CLASS, EM_STATIC_CLASS):
            with self.subTest(class_name=class_name):
                self.assertTrue(probe.is_volatile_class_name(class_name))

    def test_ordinary_classes_are_not_volatile(self) -> None:
        for class_name in ("Button", "Edit", "ComboBox", "#32770",
                           "CharGridWClass", "RICHEDIT50W", None, ""):
            with self.subTest(class_name=class_name):
                self.assertFalse(probe.is_volatile_class_name(class_name))

    def test_regex_wildcards_only_the_volatile_tail(self) -> None:
        compiled = re.compile(probe.class_name_regex(EM_BUTTON_CLASS))
        # Still matches after a rebuild changes the suffix.
        self.assertTrue(compiled.match(EM_BUTTON_CLASS))
        self.assertTrue(compiled.match("WindowsForms10.BUTTON.app.0.deadbeef_r9_ad2"))
        # Must not match a different control type.
        self.assertFalse(compiled.match(EM_EDIT_CLASS))

    def test_no_regex_for_a_stable_class_name(self) -> None:
        self.assertIsNone(probe.class_name_regex("Button"))
        self.assertIsNone(probe.class_name_regex(None))


class TestControlTypeNormalisation(unittest.TestCase):
    """The win32 backend never reports a UIA control type. For a plain Win32
    dialog it reports nothing at all and only the window class is available;
    for a .NET app it reports the full CLR type name. Both have to be mapped
    before any control-type filter means anything.
    """

    def test_dotnet_types_reduce_to_the_last_segment(self) -> None:
        cases = {
            "System.Windows.Forms.Button": "Button",
            "System.Windows.Forms.TextBox": "Edit",
            "System.Windows.Forms.CheckBox": "CheckBox",
            "System.Windows.Forms.RadioButton": "RadioButton",
            "System.Windows.Forms.Label": "Text",
            "System.Windows.Forms.GroupBox": "Pane",
            "System.Windows.Forms.ToolStrip": "ToolBar",
            "DevExpress.XtraEditors.SimpleButton": "Button",
            "DevExpress.XtraEditors.CheckEdit": "CheckBox",
            "DevExpress.XtraEditors.MRUEdit": "Edit",
            "DevExpress.XtraBars.Ribbon.RibbonControl": "ToolBar",
            "SystemsLink.SLGrid": "DataGrid",
        }
        for clr_type, expected in cases.items():
            with self.subTest(clr_type=clr_type):
                self.assertEqual(probe.dotnet_control_kind(clr_type), expected)

    def test_nested_type_keeps_the_inner_name(self) -> None:
        """'ToolStripComboBox+ToolStripComboBoxControl' is a ComboBox; the
        outer half of the name does not say that.
        """
        self.assertEqual(
            probe.dotnet_control_kind(
                "System.Windows.Forms.ToolStripComboBox+ToolStripComboBoxControl"
            ),
            "ComboBox",
        )

    def test_systemslink_forms_are_windows(self) -> None:
        for clr_type in ("SystemsLink.frmMain", "SystemsLink.frmWeb",
                         "SystemsLink.frmDataSets"):
            with self.subTest(clr_type=clr_type):
                self.assertEqual(probe.dotnet_control_kind(clr_type), "Window")

    def test_unknown_and_bare_types_yield_nothing(self) -> None:
        for value in (None, "", "Button", "Wibble.Unknown.Thing"):
            with self.subTest(value=value):
                self.assertEqual(probe.dotnet_control_kind(value), "")

    def test_win32_classes_map_including_winforms_prefix(self) -> None:
        cases = {
            "Button": "Button",
            "Edit": "Edit",
            "ComboBox": "ComboBox",
            "Static": "Text",
            "#32770": "Window",
            "SysTreeView32": "Tree",
            "RICHEDIT50W": "Edit",
            "SysDateTimePick32": "Edit",
            EM_BUTTON_CLASS: "Button",
            EM_EDIT_CLASS: "Edit",
            "CharGridWClass": "",
        }
        for class_name, expected in cases.items():
            with self.subTest(class_name=class_name):
                self.assertEqual(probe.win32_control_kind(class_name), expected)

    def test_normalised_prefers_uia_then_clr_then_class(self) -> None:
        # A genuine UIA control type passes straight through.
        self.assertEqual(
            probe.normalised_control_type({"control_type": "Button"}, "uia"),
            "Button",
        )
        # A CLR type name is mapped rather than passed through.
        self.assertEqual(
            probe.normalised_control_type(
                {"control_type": "System.Windows.Forms.TextBox"}, "win32"
            ),
            "Edit",
        )
        # With no control type at all, fall back to the window class.
        self.assertEqual(
            probe.normalised_control_type(
                {"control_type": "", "class_name": "Button"}, "win32"
            ),
            "Button",
        )

    def test_dotnet_controls_pass_the_actionable_filter(self) -> None:
        """Regression: the filter used to compare a CLR type name against UIA
        control types, matched nothing, and silently selected only controls
        that happened to have a designer name.
        """
        button = node(
            "System.Windows.Forms.Button",
            name="Upload Database",
            automation_id=None,
            class_name=EM_BUTTON_CLASS,
            depth=3,
        )
        self.assertTrue(
            probe.node_is_worth_verifying(
                button, probe.ACTIONABLE_CONTROL_TYPES, "win32"
            )
        )
        label = node(
            "System.Windows.Forms.Label",
            name="Some caption",
            class_name=EM_STATIC_CLASS,
            depth=3,
        )
        self.assertFalse(
            probe.node_is_worth_verifying(
                label, probe.ACTIONABLE_CONTROL_TYPES, "win32"
            ),
            "a label is not actionable",
        )

    def test_named_controls_qualify_whatever_their_type(self) -> None:
        """grpUserDetails is a GroupBox, not a control, but it is the anchor a
        bot searches within, so its selector still needs proving.
        """
        group = node(
            "System.Windows.Forms.GroupBox",
            automation_id="grpUserDetails",
            class_name=EM_WINDOW_CLASS,
            depth=2,
        )
        self.assertTrue(
            probe.node_is_worth_verifying(
                group, probe.ACTIONABLE_CONTROL_TYPES, "win32"
            )
        )


class TestWin32Candidates(unittest.TestCase):
    def test_winforms_designer_name_ranks_first_and_highest(self) -> None:
        candidates = probe._win32_candidates(
            {
                "automation_id": "cmdUpload",
                "window_text": "Upload Database",
                "class_name": EM_BUTTON_CLASS,
                "control_id": 728198,
            }
        )
        self.assertEqual(candidates[0]["criteria"], {"auto_id": "cmdUpload"})
        self.assertEqual(candidates[0]["stability"], probe.STABILITY_NAMED)

    def test_winforms_control_id_is_demoted_to_positional(self) -> None:
        """Measured: tsMain reports control_id 2297934, derived from its window
        handle, so it is a different number on the next run. It resolves
        uniquely and is still worthless.
        """
        candidates = probe._win32_candidates(
            {
                "automation_id": None,
                "window_text": "Upload Database",
                "class_name": EM_BUTTON_CLASS,
                "control_id": 728198,
            }
        )
        entry = next(
            c for c in candidates if c["criteria"] == {"control_id": 728198}
        )
        self.assertEqual(entry["stability"], probe.STABILITY_POSITIONAL)
        self.assertIn("window handle", entry["basis"])

    def test_volatile_class_name_is_never_offered_literally(self) -> None:
        candidates = probe._win32_candidates(
            {
                "automation_id": "cmdUpload",
                "window_text": "Upload Database",
                "class_name": EM_BUTTON_CLASS,
                "control_id": 728198,
            }
        )
        for candidate in candidates:
            with self.subTest(criteria=candidate["criteria"]):
                self.assertNotIn("class_name", candidate["criteria"])

    def test_classic_dialog_control_id_stays_trusted(self) -> None:
        candidates = probe._win32_candidates(
            {
                "automation_id": None,
                "window_text": "&Select",
                "class_name": "Button",
                "control_id": 103,
            }
        )
        self.assertEqual(
            candidates[0]["criteria"], {"control_id": 103, "class_name": "Button"}
        )
        self.assertEqual(candidates[0]["stability"], probe.STABILITY_NUMERIC)


class TestUiaCandidates(unittest.TestCase):
    def test_named_automation_id_outranks_a_synthesised_one(self) -> None:
        named = probe._uia_candidates(
            {"automation_id": "txtEmail", "name": "", "control_type": "Edit",
             "class_name": EM_EDIT_CLASS}
        )
        synthesised = probe._uia_candidates(
            {"automation_id": "103", "name": "Select", "control_type": "Button",
             "class_name": "Button"}
        )
        self.assertEqual(named[0]["stability"], probe.STABILITY_NAMED)
        self.assertEqual(synthesised[0]["stability"], probe.STABILITY_NUMERIC)

    def test_volatile_class_offered_only_as_a_pattern(self) -> None:
        candidates = probe._uia_candidates(
            {"automation_id": None, "name": None, "control_type": "Edit",
             "class_name": EM_EDIT_CLASS}
        )
        criteria = [c["criteria"] for c in candidates]
        self.assertNotIn(
            {"class_name": EM_EDIT_CLASS, "control_type": "Edit"}, criteria
        )
        self.assertTrue(
            any("class_name_re" in c for c in criteria),
            "expected a class_name_re candidate for a volatile class name",
        )


class TestRecommend(unittest.TestCase):
    @staticmethod
    def proof(
        criteria: dict,
        count: int,
        stability: int = probe.STABILITY_NAMED,
        basis: str = "test basis",
        elapsed_ms: float = 1.0,
    ) -> dict:
        return {
            "criteria": criteria,
            "match_count": count,
            "elapsed_ms": elapsed_ms,
            "error": None,
            "stability": stability,
            "basis": basis,
        }

    def test_stability_beats_ordering(self) -> None:
        """A caption selector listed first must not win over a named one."""
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[
                self.proof({"title": "Upload Database"}, 1,
                           probe.STABILITY_CAPTION, "visible name"),
                self.proof({"auto_id": "cmdUpload"}, 1,
                           probe.STABILITY_NAMED, "designer name"),
            ],
            win32_proof=[],
        )
        self.assertEqual(result["selector"], {"auto_id": "cmdUpload"})
        self.assertEqual(result["confidence"], "high")

    def test_win32_wins_a_tie_on_equal_stability(self) -> None:
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[self.proof({"auto_id": "cmdUpload"}, 1,
                                  probe.STABILITY_NAMED, "automation_id",
                                  elapsed_ms=48.0)],
            win32_proof=[self.proof({"auto_id": "cmdUpload"}, 1,
                                    probe.STABILITY_NAMED, "designer name",
                                    elapsed_ms=0.3)],
        )
        self.assertEqual(result["backend"], "win32")
        self.assertIn("faster", result["reason"])

    def test_handle_derived_control_id_loses_to_a_caption(self) -> None:
        """The WinForms trap: control_id resolves uniquely but is worthless."""
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[],
            win32_proof=[
                self.proof({"control_id": 2297934}, 1,
                           probe.STABILITY_POSITIONAL,
                           "control id derived from the window handle"),
                self.proof({"title": "Main"}, 1,
                           probe.STABILITY_CAPTION, "window text"),
            ],
        )
        self.assertEqual(result["selector"], {"title": "Main"})

    def test_non_unique_matches_are_never_eligible(self) -> None:
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[self.proof({"auto_id": "cmdOk"}, 0),
                       self.proof({"control_type": "Button"}, 12)],
            win32_proof=[self.proof({"class_name": "Button"}, 5)],
        )
        self.assertIsNone(result["backend"])
        self.assertIsNone(result["selector"])
        self.assertEqual(result["confidence"], "none")
        self.assertEqual(result["alternatives"], [])

    def test_positional_only_is_flagged_as_a_last_resort(self) -> None:
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[self.proof({"control_type": "Button"}, 1,
                                  probe.STABILITY_POSITIONAL,
                                  "control type only")],
            win32_proof=[],
        )
        self.assertEqual(result["confidence"], "low")
        self.assertIn("last resort", result["reason"])

    def test_runners_up_are_recorded_as_alternatives(self) -> None:
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[self.proof({"auto_id": "cmdUpload"}, 1,
                                  probe.STABILITY_NAMED, "automation_id")],
            win32_proof=[self.proof({"auto_id": "cmdUpload"}, 1,
                                    probe.STABILITY_NAMED, "designer name")],
        )
        self.assertEqual(result["backend"], "win32")
        self.assertEqual(len(result["alternatives"]), 1)
        self.assertEqual(result["alternatives"][0]["backend"], "uia")

    def test_stability_label_is_reported(self) -> None:
        result = probe.recommend(
            uia_meta={},
            win32_meta={},
            uia_proof=[self.proof({"auto_id": "txtEmail"}, 1,
                                  probe.STABILITY_NAMED, "automation_id")],
            win32_proof=[],
        )
        self.assertEqual(result["stability"], probe.STABILITY_NAMED)
        self.assertEqual(
            result["stability_label"],
            probe.STABILITY_LABELS[probe.STABILITY_NAMED],
        )


class TestPywinautoSnippet(unittest.TestCase):
    def test_snippet_uses_a_specification_not_a_wrapper(self) -> None:
        """The April spike died on child_window() being called on a resolved
        wrapper. The emitted snippet must show the specification pattern, and
        must resolve late via wait().
        """
        snippet = probe.pywinauto_snippet(
            {"auto_id": "cmdOk", "control_type": "Button"}, "uia", "Enter Password"
        )
        self.assertIn('Desktop(backend="uia").window(', snippet)
        self.assertIn(".child_window(", snippet)
        self.assertIn('wait("exists ready"', snippet)
        self.assertNotIn("wrapper_object", snippet)

    def test_criteria_are_rendered_as_keyword_arguments(self) -> None:
        snippet = probe.pywinauto_snippet(
            {"control_id": 103}, "win32", "Character Map"
        )
        self.assertIn("control_id=103", snippet)
        self.assertIn('backend="win32"', snippet)

    def test_window_title_is_regex_escaped(self) -> None:
        """A caption like 'Download (3)' is a regex that matches nothing
        useful if pasted in raw.

        Checks the contract that matters rather than the exact text: pull the
        emitted string literal back out, evaluate it the way Python would when
        the snippet is pasted into a bot, and confirm the resulting pattern
        matches the real caption.
        """
        title = "Download (3) [new]"
        snippet = probe.pywinauto_snippet({"auto_id": "cmdOk"}, "win32", title)

        literal = re.search(r"title_re=('(?:[^'\\]|\\.)*')", snippet)
        self.assertIsNotNone(literal, f"no title_re literal in:\n{snippet}")

        pattern = ast.literal_eval(literal.group(1))
        self.assertNotEqual(pattern, title, "caption was not escaped at all")
        self.assertTrue(
            re.match(pattern, title),
            f"emitted pattern {pattern!r} does not match {title!r}",
        )

    def test_plain_title_round_trips(self) -> None:
        snippet = probe.pywinauto_snippet({"auto_id": "frmMain"}, "win32",
                                          "SystemsLink - IES 1")
        literal = re.search(r"title_re=('(?:[^'\\]|\\.)*')", snippet)
        pattern = ast.literal_eval(literal.group(1))
        self.assertTrue(re.match(pattern, "SystemsLink - IES 1"))


if __name__ == "__main__":
    unittest.main()
