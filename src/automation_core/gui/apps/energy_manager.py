"""
Energy Manager driver.

Turns "log into Web Extensions and start a profile download" into a few
obvious lines, with every trap the application sets handled inside rather
than left for the bot author to remember.

    from automation_core.gui.apps.energy_manager import EnergyManager

    em = EnergyManager()
    em.launch_and_login(exe_path, app_username, app_password)
    web = em.open_web_extensions()
    em.set_text(web.child_window(auto_id="txtEmail"), web_username)
    em.set_text(web.child_window(auto_id="txtPassword"), web_password)
    em.click(web.child_window(auto_id="btnDownloadProfileData"))

What this class knows, so a caller does not have to:

  - Energy Manager needs **two backends**. Its WinForms MDI child forms are
    far better through win32; its DevExpress ribbon is invisible to win32
    entirely and needs UIA.
  - The application login and the Web Extensions login are **different
    credentials**.
  - Every control lookup must be **scoped to its owning MDI child form**,
    because a designer name is unique within a form, not across the app.
    `tsMain` exists three times.
  - Windows are found by handle, never by `title=` matching.
  - Clicks are posted, never sent, so a button that opens a modal dialog
    cannot hang the bot.

The selector map lives beside this module in `energy_manager.yaml` and is
the single source of truth. Re-harvest it after an Energy Manager upgrade
rather than editing it by hand.

Windows only. Requires the `gui` extra.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .. import controls, monitor, windows
from ..app import GuiApp

logger = logging.getLogger(__name__)

_MAP_PATH = Path(__file__).with_name("energy_manager.yaml")


class EnergyManagerError(RuntimeError):
    """Energy Manager did something the driver could not proceed through."""


class LoginFailed(EnergyManagerError):
    """The application rejected the credentials, or login did not complete."""


def load_map(path: Path | None = None) -> dict[str, Any]:
    """The Energy Manager knowledge base, as a dict."""
    with (path or _MAP_PATH).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class EnergyManager(GuiApp):
    """A running Energy Manager, driven safely."""

    process_name = "EM.exe"
    main_window_caption = "SystemsLink"
    default_backend = "win32"

    def __init__(self, map_path: Path | None = None) -> None:
        super().__init__()
        self.map = load_map(map_path)
        self._main_handle: int | None = None

    # -- login ------------------------------------------------------------
    def launch_and_login(
        self,
        executable: str,
        username: str,
        password: str,
        *,
        client_group: str | None = None,
        kill_first: bool = True,
        launch_timeout: float = 300.0,
    ) -> int:
        """Launch Energy Manager and log in. Returns the main window handle.

        Raises LoginFailed with the application's own error text if the
        credentials are rejected.
        """
        if kill_first and self.is_running:
            self.kill()

        self.launch(executable)
        return self.login(username, password, client_group=client_group,
                          timeout=launch_timeout)

    def login(
        self,
        username: str,
        password: str,
        *,
        client_group: str | None = None,
        timeout: float = 300.0,
        settle_timeout: float = 120.0,
    ) -> int:
        """Complete the Enter Password and Client Group dialogs.

        `username` and `password` are the **application** credentials
        (app_username / app_password), not the Web Extensions ones.
        """
        logger.info("Waiting for the Energy Manager login window")
        login_window = self.wait_for_window("Enter Password", timeout=timeout)
        login = self.window(login_window.handle)

        logger.info("Logging in as %s", username)
        controls.select(login.child_window(auto_id="cmbNames"), username)
        if client_group:
            controls.select(login.child_window(auto_id="cmbClients"), client_group)
        controls.set_text(login.child_window(auto_id="txtPassword"), password)
        controls.click(login.child_window(auto_id="cmdOk"))

        # Either an error dialog or the Client Group form appears next.
        error = self._wait_for_error(timeout=10.0)
        if error is not None:
            raise LoginFailed(f"Energy Manager rejected the login: {error}")

        self._dismiss_client_group(client_group=client_group)

        logger.info("Waiting for the main shell")
        main = self.wait_for_window(self.main_window_caption, timeout=timeout)
        self._main_handle = main.handle

        # The shell appears well before it is finished building its MDI child
        # forms, and enumerating it during that window has crashed Energy
        # Manager. Settling took about 33 seconds when measured. Do not skip
        # this: the cost is a wait, the alternative is a dead application.
        if not self.wait_until_settled(main.handle, timeout=settle_timeout):
            logger.warning(
                "Main shell never settled; UIA enumeration may be unsafe")

        logger.info("Logged in; main shell handle %s", main.handle)
        return main.handle

    def _wait_for_error(self, *, timeout: float = 10.0) -> str | None:
        """The text of an EM error dialog, if one appears. Dismisses it.

        Energy Manager's error dialogs are plain Win32 MessageBoxes with no
        designer names on anything, unlike the rest of the application, so
        they are matched on caption and static text. Always scoped to EM's
        own process, so another application's "Error" window is never
        mistaken for one of ours.
        """
        found: list[str] = []

        def error_showing() -> bool:
            window = windows.find_window("Error", pids=self.pids, exact=True)
            if window is None:
                return False
            message = self._message_box_text(window.handle)
            found.append(message)
            return True

        if not controls.wait_until(error_showing, timeout=timeout, poll=0.25):
            return None

        window = windows.find_window("Error", pids=self.pids, exact=True)
        if window is not None:
            try:
                dialog = self.window(window.handle)
                controls.click(dialog.child_window(title="OK", class_name="Button"),
                               timeout=5)
            except Exception:
                logger.warning("Could not dismiss the error dialog", exc_info=True)
        return found[0] if found else "(no message text)"

    def _message_box_text(self, handle: int) -> str:
        """The static text of a Win32 MessageBox."""
        try:
            import win32gui
        except ImportError:  # pragma: no cover - guarded at package import
            return ""

        parts: list[str] = []

        def callback(child: int, _extra: object) -> None:
            try:
                if win32gui.GetClassName(child) == "Static":
                    text = win32gui.GetWindowText(child) or ""
                    if text.strip():
                        parts.append(text.strip())
            except Exception:
                return

        try:
            win32gui.EnumChildWindows(handle, callback, None)
        except Exception:
            return ""
        return " ".join(parts)

    def _dismiss_client_group(self, *, client_group: str | None = None) -> bool:
        """Accept the Client Group dialog that follows a successful login."""
        window = windows.find_window("Client Group", pids=self.pids, exact=True)
        if window is None:
            found = controls.wait_until(
                lambda: windows.find_window("Client Group", pids=self.pids,
                                            exact=True) is not None,
                timeout=30.0,
            )
            if not found:
                logger.info("No Client Group dialog appeared")
                return False
            window = windows.find_window("Client Group", pids=self.pids, exact=True)

        logger.info("Accepting the Client Group dialog")
        dialog = self.window(window.handle)
        if client_group:
            controls.select(dialog.child_window(auto_id="cmbClients"), client_group)
        controls.click(dialog.child_window(auto_id="cmdOk"))
        return True

    # -- navigation -------------------------------------------------------
    @property
    def main_handle(self) -> int:
        if self._main_handle is None:
            info = self.wait_for_window(self.main_window_caption)
            self._main_handle = info.handle
        return self._main_handle

    def shell(self, *, backend: str | None = None) -> Any:
        """Specification for the main shell."""
        return self.window(self.main_handle, backend=backend)

    def form(self, automation_id: str, *, backend: str | None = None) -> Any:
        """A specification scoped to one MDI child form.

        Always go through this rather than searching the main window
        directly. A designer name is unique within its form, not across the
        application: `tsMain` exists three times. Scoping is also about four
        times faster, because the search space is smaller.
        """
        return self.shell(backend=backend).child_window(auto_id=automation_id)

    def open_web_extensions(self, *, timeout: float = 60.0) -> Any:
        """Open the Web Extensions form and return a specification for it.

        Uses the Ctrl+Alt+1 shortcut rather than driving the ribbon. The
        ribbon is a DevExpress control that win32 cannot see into, so
        clicking it would mean a UIA traversal that depends on the Add-Ins
        tab being active. The shortcut depends on none of that, and it is
        what the Automation Anywhere bot sent.

        A fresh login does not open this form, so it usually has to be done.
        """
        hotkey = self.map["hotkeys"]["web_extensions"]["keys"]
        if not self.send_hotkey(hotkey, window_handle=self.main_handle):
            raise EnergyManagerError(
                f"Could not send the Web Extensions shortcut {hotkey!r}"
            )

        opened = controls.wait_until(
            lambda: self._form_exists("frmWeb"),
            timeout=timeout,
            description="Web Extensions form",
        )
        if not opened:
            raise EnergyManagerError(
                "Web Extensions did not open within "
                f"{timeout}s after sending {hotkey}"
            )
        return self.form("frmWeb")

    def _form_exists(self, automation_id: str) -> bool:
        try:
            self.form(automation_id).wait("exists", timeout=1)
            return True
        except Exception:
            return False


    # -- monitoring a long operation --------------------------------------
    #: The dialogs Energy Manager raises when an operation fails. Taken from
    #: the Automation Anywhere bots, which poll for exactly these. They are
    #: plain Win32 MessageBoxes, so they are matched on caption plus static
    #: text, and always scoped to EM's own process id.
    ERROR_DIALOGS = (
        monitor.ErrorDialog("Internet Connection Error"),
        monitor.ErrorDialog("Unexpected Error"),
        monitor.ErrorDialog("Error", contains="Error"),
    )

    def wait_for_operation(
        self,
        *,
        is_running,
        timeout_minutes: float,
        poll_seconds: float = 60.0,
        description: str = "operation",
        capture_screenshot=None,
    ) -> monitor.MonitorResult:
        """Watch a download, upload or calculation through to its end.

        These run for **hours**, so this is not a `wait` with a bigger number:
        it polls, logs a heartbeat so the run does not look hung, watches for
        the error dialogs EM raises, dismisses them, and captures a screenshot
        of anything that goes wrong.

        `timeout_minutes` should come from config and differ per operation:
        the AA bots carry separate download and upload limits because the two
        do not take the same time.

        Pass `is_running` from `progress_window_running` or
        `progress_control_running` below, depending on how the operation
        reports itself. Both read window state only and send the application
        nothing.
        """
        def dismiss(handle: int, button: str) -> None:
            dialog = self.window(handle)
            controls.click(dialog.child_window(title=button, class_name="Button"),
                           timeout=10)

        return monitor.wait_for_operation(
            is_running=is_running,
            process_ids=lambda: self.pids,
            timeout_minutes=timeout_minutes,
            poll_seconds=poll_seconds,
            error_dialogs=self.ERROR_DIALOGS,
            dismiss=dismiss,
            capture_screenshot=capture_screenshot,
            description=description,
        )

    def progress_window_running(self, caption: str = "Downloading"):
        """Still-running test for an operation that opens its own window.

        Energy Manager shows a top-level `Downloading` window for a period
        download, and `Upload in Progress` for an upload.
        """
        return monitor.window_gone(caption, lambda: self.pids)

    def progress_control_running(self, handle: int):
        """Still-running test for an operation that shows an in-form progress bar.

        Import New Data opens no window at all: it puts a ProgressBar inside
        frmProfileCollectorList's toolbar, so a monitor watching the desktop
        for dialogs would wait forever. Find that control's handle first, then
        pass it here.
        """
        return monitor.control_gone(handle)

    def web_extensions_login(self, username: str, password: str) -> Any:
        """Fill the User Details group on the Web Extensions form.

        These are the **web** credentials (web_username / web_password), a
        different account from the application login. Note the field is named
        txtEmail but is labelled "Username:" and takes a username.
        """
        web = self.form("frmWeb")
        controls.set_text(web.child_window(auto_id="txtEmail"), username)
        controls.set_text(web.child_window(auto_id="txtPassword"), password)
        return web
