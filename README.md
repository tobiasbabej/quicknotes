# quicknotes

A lightweight, single-instance overlay notepad for Linux desktops. Press a global shortcut to show or hide it; type in any of multiple tabs; auto-saves to plain text files. The window remembers its last position so it always reappears where you left it.

## Features

- **Multi-tab** notes — each tab is its own `.txt` file on disk
- **Auto-save** ~400 ms after you stop typing
- **Per-tab close button** (×) plus an `Alt+D` shortcut
- **Double-click a tab** to rename it (the underlying file is renamed too)
- **Esc** hides the overlay; relaunch shows it again at the same screen position
- **Single-instance daemon** with a Unix socket — invoking the command a second time toggles the existing window instead of opening a new one
- **Dark theme**, monospaced editor, top-most window

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Alt+N` | New tab |
| `Alt+D` | Close current tab |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Cycle tabs |
| `Esc` | Hide overlay |
| Double-click tab | Rename |

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
└── ...
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
