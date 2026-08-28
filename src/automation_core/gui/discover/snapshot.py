"""
Whole-window UI tree snapshots, and diffs between them.

The hover inspector needs a human at the keyboard, which makes it a poor
instrument for mapping an application: you can only record controls you
thought to point at. This tool dumps an entire window's control tree to
JSON without any interaction, under both backends at once, so it finds the
controls nobody thought to look for.

Two snapshots can then be diffed. That is how you answer "what did that
click actually do", which is the question the April spike's progress
monitor was guessing at when it decided any window whose title contained
"complete" meant success.

  Map a window, both backends:
      python src/tools/ui_tree_snapshot.py --title "SystemsLink" --label main-shell

  List what is open:
      python src/tools/ui_tree_snapshot.py --list

  Compare two states:
      python src/tools/ui_tree_snapshot.py --diff captures/tree_before.json \
                                                  captures/tree_after.json

The headline output is the addressability summary: of every control in the
window, how many carry an application-assigned automation_id, how many only
have the numeric id UIA synthesises from a Win32 dialog control, and how
many have no stable identity at all. That ratio is what decides whether
selector-based automation is viable for an application or whether it needs
coordinate and image fallbacks.

A developer tool, not a bot: prints to the console, reads no config.yaml,
writes to the gitignored captures/ folder. Windows only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import win32gui
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.win32_element_info import HwndElementInfo
except ImportError as exc:
    missing = getattr(exc, "name", None) or str(exc)
    print(f"Missing dependency: {missing}")
    print("Run setup.bat from the repo root, or:")
    print("    pip install pywinauto pywin32")
    sys.exit(1)

from . import probe

probe.set_dpi_awareness()

RULE = "=" * 78
BACKENDS = ("uia", "win32")


# ---------------------------------------------------------------------------
# Capturing
# ---------------------------------------------------------------------------
def snapshot_window(
    handle: int,
    label: str,
    backends: tuple[str, ...] = BACKENDS,
    max_depth: int = 30,
    max_nodes: int = 20000,
    verify: bool = False,
    verify_control_types: frozenset[str] | None = None,
    verify_limit: int | None = None,
) -> dict[str, Any]:
    """Walk a top-level window under each backend and return one payload.

    With verify=True, every worthwhile control also gets a proven selector,
    so the snapshot becomes a verified selector map rather than a description.
    That is the whole point of doing this headlessly: proving 600 selectors by
    hovering over them is not a plan.
    """
    title = probe.safe(lambda: win32gui.GetWindowText(handle))
    payload: dict[str, Any] = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "window": {
            "handle": handle,
            "title": title,
            "class_name": probe.safe(lambda: win32gui.GetClassName(handle)),
        },
        "trees": {},
        "stats": {},
    }

    for backend in backends:
        info_cls = UIAElementInfo if backend == "uia" else HwndElementInfo
        started = time.perf_counter()
        try:
            tree = probe.walk_tree(
                info_cls(handle), max_depth=max_depth, max_nodes=max_nodes
            )
        except Exception as exc:
            payload["trees"][backend] = None
            payload["stats"][backend] = {
                "error": f"{type(exc).__name__}: {exc}",
            }
            continue
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        flat = probe.flatten_tree(tree)
        payload["trees"][backend] = tree
        payload["stats"][backend] = {
            "nodes": len(flat),
            "walk_ms": elapsed_ms,
            "addressability": addressability(flat),
        }

    # Hotkeys come from the UIA tree only: Win32 has no equivalent property.
    uia_tree = payload["trees"].get("uia")
    if uia_tree is not None:
        payload["hotkeys"] = collect_hotkeys(uia_tree)

    if verify:
        payload["verified"] = {}
        for backend in backends:
            tree = payload["trees"].get(backend)
            if tree is None:
                continue
            payload["verified"][backend] = probe.verify_tree_selectors(
                handle,
                tree,
                backend,
                control_types=verify_control_types,
                limit=verify_limit,
            )
    return payload


def addressability(flat: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How many controls in this window can actually be addressed, and how.

    The distinction that matters is between an automation_id the application
    chose (`cmdOk`) and one UIA manufactured from a Win32 dialog control id
    (`103`). Both are stable. Only the first tells you the application was
    built with automation in mind.
    """
    named_auto_id = 0
    synthesised_auto_id = 0
    name_only = 0
    control_id_only = 0
    unidentifiable = 0

    for node in flat.values():
        auto_id = node.get("automation_id")
        if auto_id and not probe.is_synthesised_auto_id(auto_id):
            named_auto_id += 1
        elif auto_id:
            synthesised_auto_id += 1
        elif node.get("name"):
            name_only += 1
        elif node.get("control_id"):
            control_id_only += 1
        else:
            unidentifiable += 1

    total = len(flat) or 1
    return {
        "total_nodes": len(flat),
        "named_automation_id": named_auto_id,
        "synthesised_automation_id": synthesised_auto_id,
        "name_only": name_only,
        "control_id_only": control_id_only,
        "unidentifiable": unidentifiable,
        "percent_named_automation_id": round(100.0 * named_auto_id / total, 1),
        "percent_unidentifiable": round(100.0 * unidentifiable / total, 1),
    }


# ---------------------------------------------------------------------------
# Console rendering
# ---------------------------------------------------------------------------
def print_tree(node: dict[str, Any], indent: int = 0, max_depth: int = 40) -> None:
    if indent > max_depth:
        return
    pad = "  " * indent
    ctype = node.get("control_type") or node.get("class_name") or "?"
    name = node.get("name") or ""
    auto_id = node.get("automation_id") or ""
    ctrl_id = node.get("control_id") or ""

    bits: list[str] = []
    if auto_id:
        flag = "~" if node.get("automation_id_is_synthesised") else ""
        bits.append(f"auto_id={flag}{auto_id}")
    if ctrl_id and str(ctrl_id) != str(auto_id):
        bits.append(f"ctrl_id={ctrl_id}")
    if not node.get("visible", True):
        bits.append("hidden")
    if not node.get("enabled", True):
        bits.append("disabled")

    detail = f"  [{' '.join(bits)}]" if bits else ""
    shown_name = f" {name!r}" if name else ""
    print(f"{pad}{ctype}{shown_name}{detail}")

    if node.get("truncated"):
        print(f"{pad}  ... truncated")
    for child in node.get("children", []):
        print_tree(child, indent + 1, max_depth)


def print_addressability(stats: dict[str, Any]) -> None:
    for backend, data in stats.items():
        if "error" in data:
            print(f"  {backend:<6} FAILED: {data['error']}")
            continue
        addr = data.get("addressability", {})
        print(
            f"  {backend:<6} {data['nodes']:>5} nodes in {data['walk_ms']:>7}ms"
        )
        print(
            f"         named automation_id      {addr['named_automation_id']:>5}"
            f"  ({addr['percent_named_automation_id']}%)"
        )
        print(
            f"         synthesised (win32 id)   {addr['synthesised_automation_id']:>5}"
        )
        print(f"         name only                {addr['name_only']:>5}")
        print(f"         control_id only          {addr['control_id_only']:>5}")
        print(
            f"         no stable identity       {addr['unidentifiable']:>5}"
            f"  ({addr['percent_unidentifiable']}%)"
        )


def collect_hotkeys(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Every element in a UIA tree that advertises a keyboard shortcut.

    A hotkey beats a selector wherever one exists: it needs no traversal of a
    drawn ribbon, does not care which tab is active or where the window sits,
    and is identical under both backends. Energy Manager's Web Extensions
    button reports Ctrl+Alt+1, which is exactly the shortcut its tooltip
    shows and exactly what the Automation Anywhere bot sent.

    They are patchy: most EM ribbon buttons advertise nothing at all, so this
    supplements the selector map rather than replacing it.
    """
    found: list[dict[str, Any]] = []
    for path, node in probe.flatten_tree(tree).items():
        keys = node.get("hotkeys")
        if not keys:
            continue
        hotkey = keys.get("access_key") or keys.get("accelerator_key")
        # "none" is DevExpress's way of saying there is no shortcut.
        if not hotkey or str(hotkey).strip().lower() == "none":
            continue
        found.append({
            "path": path,
            "name": node.get("name"),
            "control_type": node.get("control_type"),
            "automation_id": node.get("automation_id"),
            "hotkey": hotkey,
            "send_keys": node.get("send_keys"),
            "source": "access_key" if keys.get("access_key") else "accelerator_key",
        })
    return found


def print_hotkeys(hotkeys: list[dict[str, Any]]) -> None:
    if not hotkeys:
        print("  (none advertised)")
        return
    for entry in hotkeys:
        spec = entry["send_keys"] or "(not translatable)"
        print(f"  {entry['hotkey']:<18} {spec:<14} "
              f"{entry['control_type']} {entry['name']!r}")


def print_verification(verified: dict[str, Any]) -> None:
    """Report how many controls ended up with a selector worth writing down."""
    for backend, result in verified.items():
        considered = result["nodes_considered"]
        resolved = result["uniquely_addressable"]
        print(
            f"  {backend:<6} {resolved}/{considered} controls uniquely "
            f"addressable in {result['elapsed_ms']}ms"
        )
        order = ("high", "medium", "low", "none")
        counts = result["by_confidence"]
        for level in order:
            if level in counts:
                print(f"         {level:<8} {counts[level]:>5}")
        for level, count in counts.items():
            if level not in order:
                print(f"         {level:<8} {count:>5}")
        if result["nodes_truncated"]:
            print(
                f"         NOTE: stopped at the --verify-limit, so this is a "
                f"sample of {considered}, not the whole window"
            )


def viability_note(stats: dict[str, Any]) -> str:
    """Plain-English read on whether selector automation will work here."""
    best_named = 0
    best_backend = None
    for backend, data in stats.items():
        if "error" in data:
            continue
        pct = data.get("addressability", {}).get("percent_named_automation_id", 0)
        if pct >= best_named:
            best_named = pct
            best_backend = backend

    if best_named >= 50:
        return (
            f"Selector automation looks viable: {best_named}% of controls carry an "
            f"application-assigned automation_id under {best_backend}."
        )
    if best_named > 0:
        return (
            f"Mixed: only {best_named}% of controls have an application-assigned "
            "automation_id. Expect to anchor on parent containers and index, and "
            "to verify every selector before relying on it."
        )
    return (
        "No control carries an application-assigned automation_id. This "
        "application was not built for automation: address controls by win32 "
        "class plus control id where possible, and expect some coordinate or "
        "image fallback. Snapshot each screen state so the structure is at "
        "least written down."
    )


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------
COMPARED_PROPS = ("name", "control_type", "automation_id", "class_name",
                  "control_id", "enabled", "visible")


def diff_trees(
    before: dict[str, Any],
    after: dict[str, Any],
    backend: str,
) -> dict[str, Any]:
    """Compare one backend's tree between two snapshot payloads."""
    tree_a = (before.get("trees") or {}).get(backend)
    tree_b = (after.get("trees") or {}).get(backend)
    if tree_a is None or tree_b is None:
        return {"error": f"one or both snapshots have no {backend} tree"}

    flat_a = probe.flatten_tree(tree_a)
    flat_b = probe.flatten_tree(tree_b)

    paths_a, paths_b = set(flat_a), set(flat_b)
    added = sorted(paths_b - paths_a)
    removed = sorted(paths_a - paths_b)

    changed: list[dict[str, Any]] = []
    for path in sorted(paths_a & paths_b):
        node_a, node_b = flat_a[path], flat_b[path]
        deltas = {
            prop: {"before": node_a.get(prop), "after": node_b.get(prop)}
            for prop in COMPARED_PROPS
            if node_a.get(prop) != node_b.get(prop)
        }
        if deltas:
            changed.append({"path": path, "changes": deltas})

    return {
        "backend": backend,
        "nodes_before": len(flat_a),
        "nodes_after": len(flat_b),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def print_diff(result: dict[str, Any], limit: int = 60) -> None:
    if "error" in result:
        print(f"  {result['error']}")
        return
    backend = result["backend"]
    print(
        f"\n  {backend}: {result['nodes_before']} nodes -> "
        f"{result['nodes_after']} nodes"
    )

    for heading, key, marker in (
        ("Appeared", "added", "+"),
        ("Disappeared", "removed", "-"),
    ):
        items = result[key]
        if not items:
            continue
        print(f"\n  {heading} ({len(items)}):")
        for path in items[:limit]:
            print(f"    {marker} {path}")
        if len(items) > limit:
            print(f"    ... {len(items) - limit} more")

    if result["changed"]:
        print(f"\n  Changed ({len(result['changed'])}):")
        for entry in result["changed"][:limit]:
            print(f"    ~ {entry['path']}")
            for prop, delta in entry["changes"].items():
                print(f"        {prop}: {delta['before']!r} -> {delta['after']!r}")
        if len(result["changed"]) > limit:
            print(f"    ... {len(result['changed']) - limit} more")

    if not (result["added"] or result["removed"] or result["changed"]):
        print("    (identical)")


def load_snapshot(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot a window's control tree to JSON, or diff two snapshots."
    )
    parser.add_argument("--title", help="Snapshot the first window whose title contains this.")
    parser.add_argument("--handle", type=int, help="Snapshot this window handle exactly.")
    parser.add_argument("--label", default="snapshot",
                        help="Name for this screen state, used in the filename.")
    parser.add_argument("--backend", choices=("uia", "win32", "both"), default="both")
    parser.add_argument("--max-depth", type=int, default=30)
    parser.add_argument("--max-nodes", type=int, default=20000)
    parser.add_argument("--list", action="store_true", help="List open windows and exit.")
    parser.add_argument("--print-tree", action="store_true",
                        help="Also print the tree to the console.")
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), type=Path,
                        help="Diff two snapshot JSON files and exit.")
    parser.add_argument("--out", type=Path, help="Write here instead of captures/.")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds to wait before capturing, to set the screen up.")
    parser.add_argument(
        "--verify", action="store_true",
        help="Prove a selector for every worthwhile control, not just describe it.",
    )
    parser.add_argument(
        "--verify-actionable-only", action="store_true",
        help="With --verify, only prove clickable and typeable controls. Much "
             "faster on a large window, and enough to drive one.",
    )
    parser.add_argument(
        "--verify-limit", type=int, default=None,
        help="With --verify, stop after this many controls (reported if hit).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.diff:
        before_path, after_path = args.diff
        try:
            before, after = load_snapshot(before_path), load_snapshot(after_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not read snapshots: {exc}")
            return 1
        print(RULE)
        print(" Snapshot diff")
        print(RULE)
        print(f" before: {before.get('label')!r} at {before.get('captured_at')}")
        print(f" after:  {after.get('label')!r} at {after.get('captured_at')}")
        for backend in BACKENDS:
            print_diff(diff_trees(before, after, backend))
        return 0

    windows = probe.list_windows()
    if args.list or (not args.title and args.handle is None):
        if not windows:
            print("No visible titled windows found.")
            return 1
        if args.list:
            print("\nOpen windows:")
            print("-" * 78)
            for handle, title, pid in windows:
                shown = (title[:56] + "...") if len(title) > 59 else title
                print(f"  {shown:<60} pid={pid} hwnd={handle}")
            return 0
        handle, title, _pid = probe.choose_window(windows)
    elif args.handle is not None:
        handle = args.handle
        title = probe.safe(lambda: win32gui.GetWindowText(handle)) or ""
        if not title and not win32gui.IsWindow(handle):
            print(f"Handle {handle} is not a window.")
            return 1
    else:
        match = probe.find_window_by_title(windows, args.title)
        if match is None:
            print(f"No open window title contains {args.title!r}.")
            print("Run with --list to see what is open.")
            return 1
        handle, title, _pid = match

    if args.delay > 0:
        print(f"Waiting {args.delay}s before capturing...")
        time.sleep(args.delay)

    backends = BACKENDS if args.backend == "both" else (args.backend,)

    print(RULE)
    print(" UI tree snapshot")
    print(RULE)
    print(f" window: {title!r} (hwnd={handle})")
    print(f" label:  {args.label}")
    print()

    if args.verify:
        print("Verifying selectors (this executes each candidate for real)...")

    payload = snapshot_window(
        handle,
        args.label,
        backends=backends,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        verify=args.verify,
        verify_control_types=(
            probe.ACTIONABLE_CONTROL_TYPES if args.verify_actionable_only else None
        ),
        verify_limit=args.verify_limit,
    )

    print("Addressability:")
    print_addressability(payload["stats"])
    print()
    print(f" {viability_note(payload['stats'])}")

    if payload.get("hotkeys") is not None:
        print()
        print("Keyboard shortcuts advertised:")
        print_hotkeys(payload["hotkeys"])

    if args.verify:
        print()
        print("Verified selectors:")
        print_verification(payload.get("verified", {}))

    if args.print_tree:
        for backend in backends:
            tree = payload["trees"].get(backend)
            if tree is None:
                continue
            print()
            print(f"--- {backend} tree ---")
            print_tree(tree)

    out_path = args.out or probe.timestamped_path(f"tree_{args.label}")
    saved = probe.write_json(payload, out_path)
    if saved is not None:
        print(f"\n Saved to: {saved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
