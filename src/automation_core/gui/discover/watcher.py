"""
Watch a process's windows and snapshot every one that appears.

The snapshot tool maps a window you already have on screen. Plenty of the
windows that matter are not like that: Energy Manager's database prompt, its
login box, the Client Group dialog and its download progress dialogs all
appear, get dismissed, and are gone. You cannot point a tool at a window that
closed twenty seconds ago, and you cannot hover over one while it is modal.

So this watches instead. It polls the top-level window list, and the moment a
window belonging to the target process appears it snapshots it and records
the event. What you get back is a timeline of the flow plus a tree for every
window in it, captured without anyone sitting and waiting.

  Map the whole launch and login sequence:
      python src/tools/ui_window_watcher.py --process EM.exe \\
             --launch "\\\\server\\share\\EM.exe" --duration 300

  Watch what a flow you drive by hand produces:
      python src/tools/ui_window_watcher.py --process EM.exe --duration 120

  Watch everything, not just one process:
      python src/tools/ui_window_watcher.py --any --duration 60

Defaults to the win32 backend, because a transient dialog has to be
snapshotted before it disappears and a UIA walk can take seconds. Pass
`--backend both` when you are watching something that will hold still.

A developer tool, not a bot: prints to the console, reads no config.yaml,
writes to the gitignored captures/ folder. Windows only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import win32gui
except ImportError as exc:
    missing = getattr(exc, "name", None) or str(exc)
    print(f"Missing dependency: {missing}")
    print("Run setup.bat from the repo root, or: pip install pywinauto pywin32")
    sys.exit(1)

from . import snapshot
from . import probe

probe.set_dpi_awareness()

RULE = "=" * 78


def _pids_for_process(process_name: str) -> set[int]:
    """Process ids whose image name matches, via tasklist.

    Shelling out to tasklist rather than adding a psutil dependency: this runs
    once per poll at most and the parsing is trivial.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    pids: set[int] = set()
    for line in (result.stdout or "").splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _safe_label(title: str, fallback: str = "window") -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in title).strip("-").lower()
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:40] or fallback


class WindowWatcher:
    """Poll for windows appearing and disappearing, snapshotting new ones."""

    def __init__(
        self,
        process_name: str | None,
        backends: tuple[str, ...],
        verify: bool,
        label_prefix: str,
        poll_sec: float = 0.25,
        snapshot_all: bool = False,
    ) -> None:
        self.process_name = process_name
        self.backends = backends
        self.verify = verify
        self.label_prefix = label_prefix
        self.poll_sec = poll_sec
        self.snapshot_all = snapshot_all

        self.seen: dict[int, str] = {}
        self.timeline: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self._pid_cache: set[int] = set()
        self._pid_checked_at = 0.0

    # -- process matching --------------------------------------------------
    def _target_pids(self) -> set[int]:
        """Cached for a second: tasklist is not free and pids rarely change."""
        if self.process_name is None:
            return set()
        now = time.monotonic()
        if now - self._pid_checked_at > 1.0:
            self._pid_cache = _pids_for_process(self.process_name)
            self._pid_checked_at = now
        return self._pid_cache

    def _is_target(self, pid: int | None) -> bool:
        if self.process_name is None:
            return True
        return pid is not None and pid in self._target_pids()

    # -- events ------------------------------------------------------------
    def _record(self, event: str, handle: int, title: str, pid: int | None) -> None:
        entry = {
            "at": datetime.now().isoformat(timespec="milliseconds"),
            "seconds_in": round(time.monotonic() - self.started, 2),
            "event": event,
            "handle": handle,
            "title": title,
            "pid": pid,
            "class_name": probe.safe(lambda: win32gui.GetClassName(handle)),
        }
        self.timeline.append(entry)
        marker = "+" if event == "appeared" else "-"
        print(
            f"  [{entry['seconds_in']:>7.2f}s] {marker} {title!r} "
            f"(hwnd={handle} pid={pid})"
        )

    def _snapshot(self, handle: int, title: str) -> None:
        label = f"{self.label_prefix}-{_safe_label(title)}"
        try:
            payload = snapshot.snapshot_window(
                handle,
                label,
                backends=self.backends,
                verify=self.verify,
                verify_control_types=probe.ACTIONABLE_CONTROL_TYPES,
            )
        except Exception as exc:
            print(f"            (snapshot failed: {type(exc).__name__}: {exc})")
            return

        self.snapshots.append(payload)
        for backend, stats in payload["stats"].items():
            if "error" in stats:
                print(f"            {backend}: FAILED {stats['error']}")
                continue
            addr = stats["addressability"]
            print(
                f"            {backend}: {stats['nodes']} nodes, "
                f"{addr['named_automation_id']} named "
                f"({addr['percent_named_automation_id']}%)"
            )

        out_path = probe.timestamped_path(f"tree_{label}")
        saved = probe.write_json(payload, out_path)
        if saved is not None:
            print(f"            saved {saved.name}")

    # -- main loop ---------------------------------------------------------
    def poll_once(self) -> None:
        current: dict[int, str] = {}
        for handle, title, pid in probe.list_windows():
            if not self._is_target(pid):
                continue
            current[handle] = title

            if handle not in self.seen:
                self._record("appeared", handle, title, pid)
                self._snapshot(handle, title)
            elif self.seen[handle] != title:
                # A retitled window is usually a new state in the same shell,
                # e.g. EM's shell gaining a document name. Worth a snapshot.
                self._record("retitled", handle, title, pid)
                if self.snapshot_all:
                    self._snapshot(handle, title)

        for handle, title in list(self.seen.items()):
            if handle not in current:
                self._record("disappeared", handle, title, None)

        self.seen = current

    def run(self, duration_sec: float) -> None:
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            try:
                self.poll_once()
            except Exception as exc:
                print(f"  (poll error, continuing: {type(exc).__name__}: {exc})")
            time.sleep(self.poll_sec)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot every window a process opens, as it opens."
    )
    parser.add_argument(
        "--process",
        help="Only watch windows owned by this image name, e.g. EM.exe",
    )
    parser.add_argument(
        "--any", action="store_true",
        help="Watch every process. Noisy; use a short --duration.",
    )
    parser.add_argument(
        "--launch",
        help="Start this executable first, then watch. Use for a launch sequence.",
    )
    parser.add_argument(
        "--kill-first", action="store_true",
        help="taskkill the --process image before launching.",
    )
    parser.add_argument("--duration", type=float, default=180.0,
                        help="Seconds to watch (default 180).")
    parser.add_argument("--poll", type=float, default=0.25,
                        help="Poll interval in seconds (default 0.25).")
    parser.add_argument("--backend", choices=("uia", "win32", "both"),
                        default="win32",
                        help="Default win32: fast enough to catch a transient dialog.")
    parser.add_argument("--verify", action="store_true",
                        help="Prove selectors for each window. Slower per snapshot.")
    parser.add_argument("--label-prefix", default="watch",
                        help="Prefix for snapshot labels and filenames.")
    parser.add_argument("--snapshot-retitles", action="store_true",
                        help="Also snapshot when a window changes its title.")
    parser.add_argument("--out", type=Path,
                        help="Write the timeline here instead of captures/.")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not args.process and not args.any:
        print("Give --process EM.exe, or --any to watch everything.")
        return 1
    process_name = None if args.any else args.process

    print(RULE)
    print(" UI window watcher")
    print(RULE)
    print(f" process:  {process_name or 'ALL'}")
    print(f" backend:  {args.backend}")
    print(f" duration: {args.duration}s, polling every {args.poll}s")
    print()

    if args.kill_first and process_name:
        print(f"Killing {process_name} ...")
        result = subprocess.run(
            ["taskkill", "/IM", process_name, "/F"],
            capture_output=True, text=True, check=False,
        )
        print(f"  {(result.stdout or result.stderr or '').strip()}")
        time.sleep(1.5)

    backends = (
        snapshot.BACKENDS if args.backend == "both" else (args.backend,)
    )
    watcher = WindowWatcher(
        process_name=process_name,
        backends=backends,
        verify=args.verify,
        label_prefix=args.label_prefix,
        poll_sec=args.poll,
        snapshot_all=args.snapshot_retitles,
    )

    # Seed the baseline so windows already open are not reported as new.
    if not args.launch:
        for handle, title, pid in probe.list_windows():
            if watcher._is_target(pid):
                watcher.seen[handle] = title
        print(f"Baseline: {len(watcher.seen)} window(s) already open, ignoring those.")

    launched: subprocess.Popen[bytes] | None = None
    if args.launch:
        print(f"Launching {args.launch}")
        print("(Energy Manager off the DFS share took 95 to 174 seconds in April.)")
        try:
            launched = subprocess.Popen([args.launch])
        except OSError as exc:
            print(f"Could not launch: {exc}")
            return 1

    print("\nWatching...\n")
    try:
        watcher.run(args.duration)
    except KeyboardInterrupt:
        print("\n  (interrupted)")

    print()
    print(RULE)
    print(f" {len(watcher.timeline)} event(s), {len(watcher.snapshots)} snapshot(s)")
    print(RULE)
    for entry in watcher.timeline:
        print(
            f"  {entry['seconds_in']:>7.2f}s  {entry['event']:<12} "
            f"{entry['title']!r} [{entry['class_name']}]"
        )

    payload = {
        "watched_at": datetime.now().isoformat(timespec="seconds"),
        "process": process_name,
        "launched": args.launch,
        "duration_sec": args.duration,
        "timeline": watcher.timeline,
        "snapshots": [
            {"label": s["label"], "window": s["window"], "stats": s["stats"]}
            for s in watcher.snapshots
        ],
    }
    out_path = args.out or probe.timestamped_path(f"timeline_{args.label_prefix}")
    saved = probe.write_json(payload, out_path)
    if saved is not None:
        print(f"\n Timeline saved to: {saved}")
        print(" (each window's tree was written to its own tree_*.json)")

    if launched is not None:
        print(f"\n {args.launch} left running (pid {launched.pid}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
