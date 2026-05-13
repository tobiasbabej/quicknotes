# quicknotes

A lightweight, single-instance overlay notepad for Linux desktops. Press a global shortcut to show or hide it; type in any of multiple tabs; auto-saves to plain text files. The window remembers its last position so it always reappears where you left it.

## Features

- **Multi-tab** notes — each tab is its own `.txt` file on disk
- **Auto-save** ~400 ms after you stop typing
- **Per-tab close button** (×) plus an `Alt+P` shortcut
- **Archive** the current tab to a side folder with `Alt+S` — pair with `Alt+P` to clear it from the overlay while keeping a timestamped copy on disk
- **Double-click a tab** to rename it (the underlying file is renamed too)
- **Esc** hides the overlay; relaunch shows it again at the same screen position
- **Single-instance daemon** with a Unix socket — invoking the command a second time toggles the existing window instead of opening a new one
- **Dark-green CRT-ish theme**, monospaced editor, top-most window

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Alt+N` | New tab |
| `Alt+P` | Close current tab |
| `Alt+S` | Archive current tab to `~/.local/share/quicknotes/archive/` |
| `Alt+F` | Open search bar (substring search across all tabs) |
| `Alt+.` / `Alt+,` | Next / previous tab |
| `Esc` | Close search bar if open, otherwise hide overlay |
| Double-click tab | Rename |

All `Alt+…` shortcuts above are user-rebindable — click the `[?]` button in the top-right of the overlay, then click any key cell in the popup to capture a new combination. Bindings persist in `~/.local/share/quicknotes/shortcuts.json`. Use **[reset defaults]** at the bottom of the popup to revert.

### Archive

`Alt+S` (or the side-panel **↳ archive** button) flushes the current tab and copies its contents to `~/.local/share/quicknotes/archive/<tab-name>_<YYYYMMDD-HHMMSS>.txt`. The tab stays open; each press creates a new timestamped snapshot. Typical workflow: `Alt+S` to stash a note you want to keep, then `Alt+P` to drop the tab from the overlay.

### Search

`Alt+F` opens a search bar at the top of the window. Type to scan every tab's content; results show up as `<tab> · L<line> — <preview>`. Use `Down` / `Enter` to move from the entry into the results, click any result (or press `Enter`) to jump to that tab with the match highlighted. `Esc` closes the bar and clears highlights. Matches are capped at 50 results.

## Requirements

- Linux with a graphical session (X11 or Wayland)
- `python3` (3.8+) with `tkinter`
  - Fedora: `sudo dnf install python3-tkinter`
  - Debian / Ubuntu: `sudo apt install python3-tk`
  - Arch: `sudo pacman -S tk`

No third-party Python packages required — only the standard library.

## Tested environment

- **Distribution:** Fedora Linux 41 (Workstation Edition)
- **Kernel:** Linux 6.17.10-100.fc41.x86_64
- **Desktop:** GNOME on Wayland
- **Python:** 3.13.9

It should work on any modern Linux distribution (Ubuntu, Debian, Arch, Mint, openSUSE, etc.) with the same dependencies. The kernel version is irrelevant — `tkinter` is a userspace library. The only desktop-specific part is how you bind the global shortcut (see below).

## Usage

```sh
python3 quicknotes.py            # toggle the overlay (start if not running)
python3 quicknotes.py show       # explicitly show
python3 quicknotes.py hide       # explicitly hide
python3 quicknotes.py quit       # shut down the daemon
```

Notes are stored under `~/.local/share/quicknotes/`:

```
~/.local/share/quicknotes/
├── tabs.json        # tab order + saved window geometry
├── Note_1.txt
├── Note_2.txt
├── ...
└── archive/         # snapshots written by Alt+S; not loaded back into the overlay
    └── Note_1_20260513-142210.txt
```

## Binding a global shortcut

### GNOME (Wayland or X11)

Settings → **Keyboard** → **View and Customize Shortcuts** → **Custom Shortcuts** → **+**

- Name: `Toggle Notes`
- Command: `python3 /absolute/path/to/quicknotes.py`
- Shortcut: e.g. `Super+N`

### KDE Plasma

System Settings → **Shortcuts** → **Custom Shortcuts** → Edit → New → Global Shortcut → Command/URL.

### XFCE

Settings → **Keyboard** → **Application Shortcuts** → Add.

### i3 / sway

Add to your config: `bindsym $mod+n exec python3 /absolute/path/to/quicknotes.py`

## How it works

The first invocation starts a Tk application and listens on `/tmp/quicknotes-<uid>.sock`. Subsequent invocations connect to that socket and send `toggle` / `show` / `hide` / `quit` instead of starting a second process. Window geometry is captured in `hide()` and rewritten in `show()` so the overlay reappears at the same coordinates.
