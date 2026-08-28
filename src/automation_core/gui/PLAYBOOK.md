# GUI automation playbook (pywinauto)

How to drive a Windows desktop application from Python, for the BPI
Automation Team. Written while migrating the Automation Anywhere Energy
Manager bots, but the rules here are general: none of them are specific to
Energy Manager, and every one of them was arrived at by measuring rather
than by reading documentation.

This file ships inside `automation_core.gui`, beside the driver it describes,
so the two cannot drift apart.

Where everything lives:

- **The driver** is `automation_core.gui` (`windows`, `controls`, `keys`,
  `app`). Every rule below is already enforced there, so using the driver is
  following this document. You only need the reasoning when something new
  comes up.
- **Application knowledge** is `automation_core/gui/apps/<application>.yaml`:
  verified selectors, hotkeys, flows, the selectors that must never be used,
  and an explicit list of what has not been mapped.
- **The discovery tools** are `automation_core.gui.discover`:

```
python -m automation_core.gui.discover.snapshot --title "MyApp" --verify
python -m automation_core.gui.discover.watcher  --process MyApp.exe
```

If you are starting a new GUI automation project, read this file first, then
run the discovery tools against your target application before writing a
single selector.

---

## 1. The one mistake that costs the most

`child_window()` is a method on pywinauto's `WindowSpecification`, **not** on
a resolved wrapper.

```python
# WRONG. This is what killed every run of the April 2026 spike.
window = Desktop(backend="uia").windows()[0]      # a resolved UIAWrapper
control = window.child_window(auto_id="cmdOk")    # AttributeError
```

```
AttributeError: 'UIAWrapper' object has no attribute 'child_window'
```

`Desktop(...).windows()` returns *resolved wrappers*. `Desktop(...).window(...)`
returns an *unresolved specification*. Only the specification can be searched.

```python
# RIGHT. Build a chain of specifications, resolve at the point of use.
window  = Desktop(backend="win32").window(title_re="^SystemsLink.*")
form    = window.child_window(auto_id="frmWeb")
control = form.child_window(auto_id="txtEmail")
control.wait("exists ready", timeout=30).set_edit_text("someone@example.com")
```

**Rule: never hold a resolved wrapper if you still need to search inside it.**
Keep specifications, resolve late with `.wait(...)`, and the timeouts work as
a bonus, because a specification is re-evaluated each time it is resolved.

(`title_re` is used here and below to keep the examples readable. In
production, find the top-level window by handle instead: see section 4a for
why matching on a title can silently stop working.)

The April spike built every finder on `Desktop(...).windows()`, so its entire
helper layer could never have worked. It failed three times identically and
the session ended there, which is why every selector it recorded below the
login dialog was pure guesswork.

---

## 2. Choose the backend by measuring, not by reputation

The received wisdom is "`win32` for old apps, `uia` for anything modern".
That is not reliable. Measure both.

Two applications, same tooling, opposite answers:

| | charmap.exe (classic Win32) | Energy Manager (.NET WinForms) |
|---|---|---|
| Nodes found, `uia` | 27 in 67ms | 223 in 6739ms |
| Nodes found, `win32` | 23 in 7ms | 77 in 42ms |
| Named `automation_id`, `uia` | 22% (all synthesised) | 24.2% |
| Named `automation_id`, `win32` | 0% | **71.4%** |
| Single lookup, `uia` | 30-56ms | ~12ms |
| Single lookup, `win32` | 0.1-1.2ms | ~2.5ms |
| Better backend | either; win32 far faster | **depends on the region** |

For Energy Manager's forms the win32 backend is both faster *and* more
informative than UIA, which is the opposite of what its .NET age suggests.
The reason is in the next section.

### Drawn controls are invisible to win32

This is the single most useful generalisation in this document, and it took
three separate discoveries to arrive at it.

**If a control's items are drawn rather than created as child windows, win32
cannot see them at all, and you need UIA for that region.** Confirmed three
times in one application:

| Control | win32 sees | UIA sees |
|---|---|---|
| DevExpress `RibbonControl` (`rbnMain`) | a childless leaf | every tab and button |
| WinForms `ToolStrip` (`tsMain`) | **zero children** | `Import New Data`, `Import For Period`, `Show Unmatched Meters` |
| DevExpress `GridControl` cells | scrollbars only | `DataItem` per cell |

The WinForms `ToolStrip` case is the instructive one, because it is not a
third-party control: modern WinForms draws `ToolStrip` items itself. So this
is not a DevExpress quirk to be worked around, it is how a large slice of
.NET UI is built.

The practical consequence: **a control being absent from the win32 tree tells
you nothing about whether it exists.** Before concluding a command is
unautomatable and reaching for screen coordinates, snapshot the same window
under UIA.

### One application can need both backends

Do not stop once one backend looks good. Energy Manager's ribbon is a
DevExpress `RibbonControl`, and DevExpress draws its ribbon items rather than
creating a child window per button. So:

- **win32 sees `rbnMain` as a childless leaf.** Every ribbon button is
  invisible to it. There is nothing to select.
- **UIA sees the whole thing**: `Pane "The Ribbon"` -> `Tab "Ribbon Tabs"` ->
  `TabItem "Add-Ins"`, and a `ToolBar "Add ins"` holding `Button
  "Web Extensions"` and its siblings.

So a working Energy Manager bot drives the ribbon through UIA and the forms
through win32, in the same run. That is a supported thing to do: the two
backends are independent `Desktop` objects over the same process.

```python
ribbon_root = Desktop(backend="uia").window(title_re="^SystemsLink")
forms_root  = Desktop(backend="win32").window(title_re="^SystemsLink")
```

Had we tested only win32, we would have concluded the ribbon was
unautomatable and reached for screen coordinates. Had we tested only UIA, we
would have accepted a 6.7-second tree walk and 24% addressability on the
forms.

**Rule: run the snapshot tool with `--backend both`, and check each region of
the UI separately. A single verdict for a whole application is usually
wrong.**

### UIA is 30-100x slower per lookup

This is not a micro-optimisation. A bot doing 200 lookups pays 6 seconds
under UIA and 0.2 seconds under win32, and it pays that on every run. Where
both backends resolve a control equally well, prefer win32.

---

## 3. What each backend actually knows

The two backends do not describe the same world, and the differences change
how you write code.

### `automation_id` means three different things

| Situation | What `automation_id` holds | Trustworthy? |
|---|---|---|
| WPF / modern app | the id the developer set | yes, high |
| .NET WinForms, win32 backend | the **designer control name** (`cmdUpload`), read via `WM_GETCONTROLNAME` | yes, high |
| Classic Win32 dialog, uia backend | the numeric dialog control id, stringified (`"103"`) | stable, but meaningless |
| Classic Win32 dialog, win32 backend | empty | no |

A purely numeric `automation_id` is UIA papering over a Win32 dialog. It is
no more stable than the win32 `control_id` and costs far more to query. The
tools flag these: a leading `~` in the inspector's hover label, and
`automation_id_is_synthesised: true` in the JSON.

### `control_type` is a UIA concept

`HwndElementInfo.control_type` returns `''` for a classic Win32 app, because
control type does not exist in Win32. All you get is the window class.

But for a **.NET** app the win32 backend answers with the full CLR type name
(`System.Windows.Forms.Button`, `DevExpress.XtraEditors.SimpleButton`) via
`WM_GETCONTROLTYPE`. That is richer than UIA's vocabulary, but it is not
UIA's vocabulary, so any code comparing against `"Button"` silently matches
nothing. `automation_core.gui.discover.probe.normalised_control_type()` maps all three forms.

### Hidden controls: appear/disappear versus visibility toggle

Toggling charmap's "Advanced view" produced two completely different
descriptions of one event:

- **uia**: 13 controls *appeared*. UIA omits hidden elements from the tree.
- **win32**: the same 9 controls changed `visible: False -> True`. They were
  there all along.

This dictates how a bot waits:

```python
# Under uia, the element does not exist until it is shown:
control.wait("exists ready", timeout=30)

# Under win32 it always exists, so an exists-check passes instantly and you
# would act on a hidden control. Wait for visibility:
control.wait("visible enabled ready", timeout=30)
```

**Rule: on the win32 backend, always wait for `visible`, never just
`exists`.**

---

## 4. Selectors that resolve uniquely and are still wrong

Uniqueness is the entry requirement, not the standard. A selector can match
exactly one element today and be the wrong thing to write down. The tools
grade every candidate on how stable its anchor is:

| Grade | Anchor | Example |
|---|---|---|
| **named** | a name the application's author chose | `auto_id="cmdUpload"` |
| **numeric** | a numeric id stable for that framework | `control_id=103` in a classic dialog |
| **caption** | visible text, or a class-name pattern | `title="Download Profile Data"` |
| **positional** | control type or index alone | `control_type="Button"`, `found_index=3` |

Three traps, all of which resolve uniquely:

**WinForms class names are volatile.** Energy Manager's email box is class
`WindowsForms10.EDIT.app.0.1a0e24_r7_ad1`. The tail encodes assembly version
and instance and changes when the app is rebuilt. Never use the literal
string. Match the stable head instead:

```python
class_name_re=r"^WindowsForms10\.EDIT\.app\.0\..*$"
```

**WinForms `control_id` is derived from the window handle.** EM's `tsMain`
reports `control_id=2297934`, which is a different number on the next run.
It resolves uniquely and is worthless. In a *classic* dialog the same field
is a genuine, stable dialog control id. Same field name, opposite
trustworthiness, decided by the framework.

**Visible is not on screen.** EM's `frmWeb` reports `IsWindowVisible=True`
and `IsIconic=False` while its rectangle sits at `(-31950, -31803)`, because
an MDI child form can be parked outside the client area. Its controls resolve
normally and report themselves visible. `click_input()` on one of them moves
the physical mouse off-screen and clicks nothing, silently.

```python
control = spec.wait("visible enabled ready", timeout=30)
left, top, right, bottom = control.rectangle()
if right < 0 or bottom < 0:
    raise RuntimeError("control resolved off-screen; restore its form first")
control.click()        # posts a message; does not move the mouse
# control.click_input() # moves the real mouse: needs an on-screen rectangle
```

**Rule: prefer `click()` and `set_edit_text()` (messages) over
`click_input()` and `type_keys()` (real mouse and keyboard) unless the
application ignores messages.** Message-based actions do not depend on
window position, do not steal focus, and cannot leak keystrokes into another
window if focus moves mid-action.

---

## 4a. Finding a top-level window is harder than it looks

Two independent failure modes, both hit on Energy Manager, both of which make
a window that is plainly on screen invisible to pywinauto.

### `title=` depends on the app answering `WM_GETTEXT`

pywinauto matches `title` / `title_re` against `rich_text`, which it obtains
by sending `WM_GETTEXT` to the window. If the target application does not
answer, `rich_text` is `''` and no title match can ever succeed.

Energy Manager's login form did exactly this after a failed login attempt:

```
GetWindowText(hwnd)          -> 'Enter Password'
HwndElementInfo.rich_text    -> ''
```

`GetWindowText` reads the caption cached in the window structure when called
across processes, so it keeps working. `WM_GETTEXT` is a message the
application has to service, and a busy, modal or wedged UI thread will not.

**It is intermittent, which is what makes it dangerous.** On a freshly
launched instance the same window answered `WM_GETTEXT` perfectly. It only
stopped after a rejected login left its message loop wedged, and then it
stayed empty for several minutes rather than recovering. So a bot built on
`title=` will work in testing and fail in production, at exactly the moment
something has already gone wrong and you most need it to keep working.

So the robust way to find a top-level window is to enumerate with
`EnumWindows`, read captions with `GetWindowText`, filter by the owning
process, and then address the window **by handle**:

```python
def find_window(caption_substring, pids):
    hits = []
    def cb(hwnd, _):
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids and win32gui.IsWindowVisible(hwnd):
            if caption_substring.lower() in (win32gui.GetWindowText(hwnd) or "").lower():
                hits.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None

window = Desktop(backend="win32").window(handle=find_window("Enter Password", pids))
```

`handle=` needs no cross-process message at all, so it always resolves.
Filtering by process id is what stops another application's window with a
similar caption being picked up.

**Rule: find top-level windows by enumerating handles and cached captions,
then address by handle. Treat `title=` as a convenience that may silently
fail.**

---

## 4b. Never search by `auto_id` at the desktop level

On the win32 backend this raises `AccessDenied`, and the reason is worth
knowing because the error looks nothing like the cause.

```python
# WRONG. Dies with pywinauto.remote_memory_block.AccessDenied
# ('[WinError 5] Access is denied.process: %d', 20748)
Desktop(backend="win32").window(auto_id="LoginForm")
```

Reading a WinForms designer name means sending `WM_GETCONTROLNAME` and having
the target process write the answer into memory you allocated inside it with
`VirtualAllocEx`. At the desktop level pywinauto has to try that against
**every top-level window on the desktop** to find your match, and the first
protected or elevated process it touches refuses.

```python
# RIGHT. Find the top-level window on cheap properties, then use auto_id
# inside a process you have rights to.
login = Desktop(backend="win32").window(title="Enter Password")
login.child_window(auto_id="cmdOk")
```

**Rule: identify top-level windows by `title` / `title_re` / `class_name`.
`auto_id` is for searching inside a window you have already found.** The same
applies to `control_id` and anything else that requires a cross-process read.

---

## 4c. An application can mix control frameworks

Do not assume one addressing strategy covers a whole application. Energy
Manager's **forms** are WinForms with a designer name on everything. Its
**error dialogs are classic Win32 MessageBoxes**:

```
#32770 'Error'                    <- window class, not a WinForms form
├── Static 'Password Incorrect.'  <- the message, and the only useful anchor
├── Static ''
└── Button 'OK'
```

Zero named `automation_id` values, nothing to select on but the title and the
static text. So the same bot needs designer names for the forms and
title-plus-static-text for the dialogs.

When you do fall back to matching on a title, **scope it to the owning process
id**, or an unrelated window called "Error" elsewhere on the desktop will
satisfy your check:

```python
_thread, pid = win32process.GetWindowThreadProcessId(handle)
if pid not in our_pids:
    continue        # not our application's dialog
```

**Rule: read the error and message dialogs as carefully as the happy path.
They are frequently built differently from the rest of the application, and
they are what your bot has to recognise when something goes wrong.**

---

## 4d. Never send a message whose parameter is a pointer

This one crashed Energy Manager, twice, in about fifteen seconds. It is the
only rule in this document where getting it wrong destroys the application
you are automating rather than merely failing to drive it.

A Win32 message parameter is often a **pointer**, and the receiving window
procedure dereferences it *inside the target process*. Pass a raw integer and
the application writes to an address that means nothing to it:

```python
# CRASHED Energy Manager. PBM_GETRANGE's lParam is a pointer to a PBRANGE
# struct; 1 is not a pointer.
win32gui.SendMessage(progress_bar, 1031, 0, 1)      # PBM_GETRANGE
```

```
Faulting application: EM.exe
Faulting module:      Comctl32.dll     <- where ProgressBar lives
Exception code:       0xc0000005       <- access violation
then:                 0xc000041d       <- unhandled exception in a callback
```

The access violation happens in **Energy Manager's** process, because that is
where the dereference occurs. Nothing in the automating script raises; it
just watches the application die.

Messages of this shape are everywhere:

| Message | What lParam actually is |
|---|---|
| `PBM_GETRANGE` | pointer to `PBRANGE` |
| `LVM_GETITEM` | pointer to `LVITEM` |
| `TVM_GETITEM` | pointer to `TVITEM` |
| `WM_GETTEXT` | pointer to a caller-supplied buffer |
| `CB_GETLBTEXT` | pointer to a buffer |

To read one of these across processes, the buffer must be allocated **in the
target process** with `VirtualAllocEx`, written there, and read back. That is
exactly what pywinauto's `RemoteMemoryBlock` does, and why `item_texts()` on
a combo box in another process works while a hand-rolled `SendMessage` does
not.

**Rules:**

1. **Use pywinauto's accessors** (`item_texts()`, `window_text()`,
   `texts()`, `legacy_properties()`) rather than sending messages yourself.
   They handle cross-process memory correctly.
2. If you must send a raw message, check its documented signature and pass
   **0 / NULL** for any pointer parameter. `PBM_GETPOS` (`lParam = 0`) is
   safe; `PBM_GETRANGE` is not.
3. Prefer UIA for reading state. `wrapper.invoke()`, `get_value()` and the
   UIA patterns go through a supported interface and cannot corrupt the
   target's memory.
4. Watch for a *progress bar's existence*, not its value. Whether the control
   is present and visible is readable with `IsWindow` and `IsWindowVisible`,
   neither of which sends the application anything at all.

The safe way to monitor the progress bar that prompted all this:

```python
# No messages sent to the application: both calls read window state only.
while win32gui.IsWindow(progress_handle) and win32gui.IsWindowVisible(progress_handle):
    time.sleep(1)
# The control being destroyed is the completion signal.
```

---

## 4e. A UIA tree walk is not read-only

It feels like inspection. It is not: UI Automation calls into the target
application's own automation provider, which for a .NET application means
running managed code inside that process. If the provider throws, the CLR can
tear the process down, and your "read-only" survey has killed the application.

Observed on Energy Manager: a UIA tree walk begun about four seconds after
login crashed it with `0xc000041d` in `clr.dll`, an unhandled exception in a
callback. No message was sent by the automating code at all. The same walk
against the same application, once settled and idle, had succeeded many times.

**Rules:**

1. **Let an application finish starting before you enumerate it.** Wait for the
   windows and child forms you expect to be present, by polling for them. The
   window existing is not the same as the application being ready.
2. **Prefer win32 for enumeration.** It reads window structure through the
   window manager rather than calling into the application, it is far faster,
   and it cannot be broken by the target's provider throwing.
3. **Treat a crash during inspection as information, not noise.** If walking
   the tree can kill the application, then so can a selector lookup, and a bot
   does those constantly.

This is the second way discovery took down the same application in one day.
The other was sending a message whose parameter was a pointer (section 4d).
Both times the automating code raised nothing at all: it simply watched the
application die.

---

## 5. Scope selectors to the nearest stable ancestor

A designer name is unique within its own form, not across an application.
Energy Manager has three `tsMain` toolbars in different MDI child forms.

```python
# Ambiguous: 3 matches across the MDI children.
main.child_window(auto_id="tsMain")

# Unambiguous, and 4x faster because the search space is smaller.
main.child_window(auto_id="frmDataSets").child_window(auto_id="tsMain")
```

Measured on Energy Manager: searching the whole main window took ~12ms per
lookup; scoping to `frmWeb` first took ~2.5ms.

**Rule: anchor on the owning form or group, then search within it.** It
removes ambiguity and it is faster. Find the right anchor from a tree
snapshot, not by guessing.

---

## 6. Never type a password, and never type blind

The April spike set the password with `type_keys()`, character by character,
into whatever held focus. Two problems: it is slow, and if focus moves
mid-sequence the password goes into another window, possibly a chat client.

```python
# WRONG
field.type_keys("hunter2", with_spaces=True)

# RIGHT: atomic, cannot leak, does not depend on focus
field.set_edit_text("hunter2")
```

Worse was the date entry, which typed digits and then `{TAB}{TAB}{ENTER}` at
whatever had focus, with no check that the right field was active or that the
value took. If you must drive a `SysDateTimePick32`, set its value and then
**read it back** and assert.

**Rule: after any blind or keyboard-driven action, read the state back and
confirm it.** An action you have not verified has not happened.

---

## 7. Wait for conditions, never sleep

The April spike had `em_startup_sleep_sec: 30` and a `post_login_sleep_sec:
10`. Fixed sleeps are simultaneously too slow on a good day and too short on
a bad one: launching EM off the DFS share was measured at 95 to 174 seconds,
so a 30-second sleep was never going to be enough anyway.

```python
# WRONG
time.sleep(30)

# RIGHT
spec.wait("visible enabled ready", timeout=180)
```

Use `wait_until` / `wait_until_passes` for anything that is not a control
appearing. Set timeouts from measurement, generously.

---

## 8. Never match a dialog on a substring of its title

The April progress monitor decided a run had succeeded if **any** window
anywhere on the desktop had a title containing `"complete"`,
case-insensitively. That reports success on an unrelated Outlook or Explorer
window.

```python
# WRONG: scans every window on the desktop
for w in Desktop(backend="win32").windows():
    if "complete" in w.window_text().lower():
        return "SUCCESS"
```

Identify the real dialog with the snapshot tool, then match it precisely: on
its own process id, its window class, and a control inside it.

**Rule: a completion check must be scoped to the application's own process
and identify a specific control, not a word in a caption.**

---

## 8b. Not every change is a new window

An MDI application changes state without opening a dialog. Clicking Energy
Manager's "Download Profile Data" produced **no new top-level window at all**:
it opened a new MDI child form (`frmProfileCollectorList`) inside the existing
shell. A monitor watching only for dialogs would have concluded that nothing
happened.

So a progress or completion check needs to watch both:

- **New top-level windows**, for real dialogs. Use the window watcher.
- **The shell's own tree**, for child forms and controls appearing inside it.
  Snapshot before and after, and diff.

```
python -m automation_core.gui.discover.snapshot --title "MyApp" --label before
# ... perform the action ...
python -m automation_core.gui.discover.snapshot --title "MyApp" --label after
python -m automation_core.gui.discover.snapshot --diff captures/tree_before_*.json                                             captures/tree_after_*.json
```

The diff names exactly what changed, including forms that appeared inside the
shell rather than beside it.

---

## 9. Use the discovery tools before writing selectors

Two headless tools ship in `automation_core.gui.discover`. The hover
inspector is not one of them: it needs a human at the keyboard plus `pynput`
and `tkinter`, so it lives in the discovery project instead. Full detail is in
the module docstrings.

### Map a window headlessly (start here)

```
python -m automation_core.gui.discover.snapshot --title "SystemsLink" --label main-shell \
       --backend both --verify
```

Walks the entire control tree under both backends, proves a selector for
every worthwhile control by actually executing it, and reports how many
resolved uniquely. No interaction needed, so it finds controls you would
never have thought to point at. `--verify-actionable-only` keeps it quick on
a large window.

### See what a click changed

```
python -m automation_core.gui.discover.snapshot --title "MyApp" --label before
# ... click something ...
python -m automation_core.gui.discover.snapshot --title "MyApp" --label after
python -m automation_core.gui.discover.snapshot --diff captures/tree_before_*.json \
                                            captures/tree_after_*.json
```

This is how you find the dialog a progress monitor should watch for, instead
of guessing at its title.

### Inspect one ambiguous control by hand

```
python src/tools/ui_element_inspector.py    # discovery project only --title "SystemsLink"
```

Hover for a red highlight; the label shows control type, name and
`automation_id` (`~` marks a synthesised one). **F2** captures, **F3**
snapshots the whole window, **ESC** finishes. Every capture probes both
backends, proves its selectors, and emits copy-paste `pywinauto` code.

Captures land in the gitignored `captures/` folder.

**Rule: paste the tool's proven selector into your bot. Do not retype it
from what you remember seeing.** Every selector in
`automation_core/gui/apps/energy_manager.yaml` was produced this way and
carries the match count that proved it.

---

## 10. A gotcha in pywinauto itself

`HwndElementInfo.children()` is documented as returning "immediate children".
It is implemented with `EnumChildWindows`, which the Win32 API defines as
enumerating **all descendants**. For Energy Manager's main form that is 76
elements where only 4 are real children.

Recursing over it naively produces one path per *subset* of the intervening
containers. It inflated our first Energy Manager tree from 77 nodes to 1151
and made `txtPassword` appear 8 times, once for each combination of
`MdiClient`, `frmWeb` and `grpUserDetails` being included or skipped. Every
node count and percentage derived from it was wrong.

`automation_core.gui.discover.probe.child_elements()` filters on the real parent:

```python
parent = win32gui.GetParent(child_handle)
if parent == this_handle:   # keep it; otherwise it is a grandchild or deeper
```

The UIA backend does not have this problem.

**Rule: if a tree looks implausibly large, or one control appears at several
depths, suspect the descendant enumeration before you suspect the
application.**

---

## 11. Checklist for a new GUI automation project

1. Snapshot the target window, `--backend both --verify`. Read the
   addressability numbers and pick a backend.
2. Snapshot every screen state the bot will touch, and label them. Commit the
   findings to a UI map document; keep the raw JSON local.
3. Diff snapshots across each action to learn what changes, and to identify
   the dialogs that signal success and failure.
4. Harvest proven selectors into a YAML map. Record the anchor each is scoped
   to, and its stability grade.
5. Write the driver as specification chains resolved late, scoped to a stable
   ancestor, waiting on `visible enabled ready`.
6. Prefer message-based actions. Verify every action by reading state back.
7. Re-run the snapshot after any application upgrade and diff it against the
   committed map. That diff is your regression test for the UI.

---

## Provenance

Every measurement quoted here was taken on 2026-08-27 against Energy Manager
build `...app.0.1a0e24_r7_ad1` and Windows 11 26100, using the tools in this
repository. Timings are indicative, not benchmarks: they came from a
developer machine doing other work. The orders of magnitude are the point,
not the digits.
