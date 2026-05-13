#!/usr/bin/env python3
"""QuickNotes — single-instance overlay notes with tabs.

Usage:
  quicknotes.py            # toggle (start if not running)
  quicknotes.py toggle     # same
  quicknotes.py show
  quicknotes.py hide
  quicknotes.py quit
"""
import json
import os
import re
import socket
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk

NOTES_DIR = Path.home() / ".local/share/quicknotes"
META_FILE = NOTES_DIR / "tabs.json"
SOCKET_PATH = f"/tmp/quicknotes-{os.getuid()}.sock"
AUTOSAVE_MS = 400


def safe_filename(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
    return s or "untitled"


def send_command(cmd: str) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(SOCKET_PATH)
        s.sendall(cmd.encode() + b"\n")
        s.close()
        return True
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
        return False


class NotesApp:
    def __init__(self):
        NOTES_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title("QuickNotes")
        self.root.geometry("760x540")
        self.root.minsize(420, 240)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1e1e1e")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TButton", padding=5, background="#2d2d2d",
                        foreground="#e6e6e6", borderwidth=0)
        style.map("TButton", background=[("active", "#3a3a3a")])
        style.configure("TSeparator", background="#3a3a3a")

        self._setup_closeable_notebook(style)

        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self.nb = ttk.Notebook(container, style="Closeable.TNotebook")
        self.nb.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self._pressed_close_idx = None
        self.nb.bind("<ButtonPress-1>", self._on_tab_press, add=True)
        self.nb.bind("<ButtonRelease-1>", self._on_tab_release, add=True)

        side = ttk.Frame(container)
        side.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(side, text="+ new", width=10, command=self.new_tab).pack(pady=2)
        ttk.Button(side, text="rename", width=10, command=self.rename_current_tab).pack(pady=2)
        ttk.Button(side, text="× close", width=10, command=self.close_current_tab).pack(pady=2)
        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(side, text="hide (Esc)", width=10, command=self.hide).pack(pady=2)
        self.status = ttk.Label(side, text="", background="#1e1e1e",
                                foreground="#6a6a6a", font=("Sans", 8))
        self.status.pack(side="bottom", pady=4)

        self.tabs = []
        self.load_tabs()
        if not self.tabs:
            self.new_tab(name="Note 1")

        self.root.bind_all("<Escape>", lambda e: self.hide())
        self.root.bind_all("<Alt-n>", lambda e: (self.new_tab(), "break")[1])
        self.root.bind_all("<Alt-d>", lambda e: (self.close_current_tab(), "break")[1])
        self.root.bind_all("<Control-Tab>", lambda e: self._cycle_tab(1))
        self.root.bind_all("<Control-Shift-ISO_Left_Tab>", lambda e: self._cycle_tab(-1))
        self.nb.bind("<Double-Button-1>", self.on_tab_double_click)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self.visible = True
        self.start_socket_server()
        self._focus_current_text()

    # ---------- Closeable tab styling ----------
    def _setup_closeable_notebook(self, style):
        # Build 12x12 PhotoImages for the × button (idle + hover).
        def make_x(color):
            img = tk.PhotoImage(width=12, height=12)
            for i in range(2, 10):
                img.put(color, (i, i))
                img.put(color, (i + 1, i))
                img.put(color, (11 - i, i))
                img.put(color, (10 - i, i))
            return img

        self._img_close = make_x("#9a9a9a")
        self._img_close_hover = make_x("#ff6b6b")

        # Re-creating an element with the same name throws — guard it.
        try:
            style.element_create(
                "close", "image", self._img_close,
                ("active", "!disabled", self._img_close_hover),
                ("pressed", "!disabled", self._img_close_hover),
                border=4, sticky="",
            )
        except tk.TclError:
            pass

        style.layout("Closeable.TNotebook", [
            ("Closeable.TNotebook.client", {"sticky": "nswe"}),
        ])
        style.layout("Closeable.TNotebook.Tab", [
            ("Closeable.TNotebook.tab", {"sticky": "nswe", "children": [
                ("Closeable.TNotebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Closeable.TNotebook.focus", {"side": "top", "sticky": "nswe", "children": [
                        ("Closeable.TNotebook.label", {"side": "left", "sticky": ""}),
                        ("Closeable.TNotebook.close", {"side": "left", "sticky": "", "border": 4}),
                    ]}),
                ]}),
            ]}),
        ])
        style.configure("Closeable.TNotebook", background="#1e1e1e", borderwidth=0)
        style.configure("Closeable.TNotebook.Tab", padding=[12, 6],
                        background="#2d2d2d", foreground="#bdbdbd")
        style.map("Closeable.TNotebook.Tab",
                  background=[("selected", "#3a3a3a")],
                  foreground=[("selected", "#ffffff")])

    def _on_tab_press(self, event):
        elem = self.nb.identify(event.x, event.y)
        if "close" in elem:
            try:
                idx = self.nb.index(f"@{event.x},{event.y}")
            except tk.TclError:
                return
            self._pressed_close_idx = idx
            return "break"

    def _on_tab_release(self, event):
        if self._pressed_close_idx is None:
            return
        elem = self.nb.identify(event.x, event.y)
        try:
            idx = self.nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            idx = -1
        pressed = self._pressed_close_idx
        self._pressed_close_idx = None
        if "close" in elem and idx == pressed:
            self._close_tab_at(idx)
            return "break"

    def _close_tab_at(self, idx):
        if idx < 0 or idx >= len(self.tabs):
            return
        tab = self.tabs[idx]
        self._save_tab(tab)
        self.nb.forget(idx)
        self.tabs.pop(idx)
        try:
            (NOTES_DIR / tab["file"]).unlink(missing_ok=True)
        except OSError:
            pass
        if not self.tabs:
            self.new_tab(name="Note 1")
        self.save_meta()

    # ---------- Tab persistence ----------
    def load_tabs(self):
        self._saved_geometry = None
        if not META_FILE.exists():
            return
        try:
            data = json.loads(META_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return
        geom = data.get("geometry")
        if isinstance(geom, str) and re.match(r"^\d+x\d+[+-]-?\d+[+-]-?\d+$", geom):
            self._saved_geometry = geom
            try:
                self.root.geometry(geom)
            except tk.TclError:
                pass
        for entry in data.get("tabs", []):
            name = entry.get("name", "untitled")
            fname = entry.get("file") or safe_filename(name) + ".txt"
            path = NOTES_DIR / fname
            content = ""
            if path.exists():
                try:
                    content = path.read_text()
                except OSError:
                    content = ""
            self._add_tab(name, content, fname)

    def save_meta(self):
        data = {
            "tabs": [{"name": t["name"], "file": t["file"]} for t in self.tabs],
            "geometry": self._current_geometry(),
        }
        try:
            META_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    def _current_geometry(self):
        try:
            if self.root.state() == "withdrawn":
                return self._saved_geometry
            g = self.root.geometry()
            self._saved_geometry = g
            return g
        except tk.TclError:
            return self._saved_geometry

    # ---------- Tabs ----------
    def _add_tab(self, name, content="", fname=None):
        frame = ttk.Frame(self.nb)
        text = tk.Text(frame, wrap="word", bg="#252526", fg="#e6e6e6",
                       insertbackground="#e6e6e6", relief="flat",
                       font=("Monospace", 11), undo=True, padx=10, pady=10,
                       selectbackground="#264f78")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        if content:
            text.insert("1.0", content)
        text.edit_modified(False)

        tab = {
            "frame": frame,
            "text": text,
            "name": name,
            "file": fname or (safe_filename(name) + ".txt"),
            "after_id": None,
        }
        self.tabs.append(tab)
        self.nb.add(frame, text=name)
        text.bind("<<Modified>>", lambda e, t=tab: self._on_modified(t))
        return tab

    def new_tab(self, name=None):
        if name is None:
            n = 1
            used = {t["name"] for t in self.tabs}
            while f"Note {n}" in used:
                n += 1
            name = f"Note {n}"
        base = safe_filename(name)
        fname = base + ".txt"
        i = 1
        existing_files = {t["file"] for t in self.tabs}
        while fname in existing_files or (NOTES_DIR / fname).exists():
            fname = f"{base}_{i}.txt"
            i += 1
        tab = self._add_tab(name, "", fname)
        try:
            (NOTES_DIR / fname).touch()
        except OSError:
            pass
        self.nb.select(tab["frame"])
        tab["text"].focus_set()
        self.save_meta()
        self._flash_status(f"+ {name}")

    def close_current_tab(self):
        idx = self._current_index()
        if idx is None:
            return
        self._close_tab_at(idx)

    def rename_current_tab(self):
        idx = self._current_index()
        if idx is None:
            return
        self._prompt_rename(idx)

    def on_tab_double_click(self, event):
        try:
            idx = self.nb.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self._prompt_rename(idx)

    def _prompt_rename(self, idx):
        tab = self.tabs[idx]
        new_name = simpledialog.askstring(
            "Rename tab", "New name:", initialvalue=tab["name"], parent=self.root)
        if not new_name or new_name == tab["name"]:
            return
        base = safe_filename(new_name)
        new_file = base + ".txt"
        i = 1
        used = {t["file"] for j, t in enumerate(self.tabs) if j != idx}
        while new_file in used or (
                (NOTES_DIR / new_file).exists() and new_file != tab["file"]):
            new_file = f"{base}_{i}.txt"
            i += 1
        try:
            old_path = NOTES_DIR / tab["file"]
            new_path = NOTES_DIR / new_file
            if old_path.exists():
                old_path.rename(new_path)
            else:
                new_path.touch()
        except OSError:
            return
        tab["name"] = new_name
        tab["file"] = new_file
        self.nb.tab(idx, text=new_name)
        self.save_meta()

    def _cycle_tab(self, direction):
        if len(self.tabs) < 2:
            return "break"
        idx = self._current_index() or 0
        new_idx = (idx + direction) % len(self.tabs)
        self.nb.select(new_idx)
        self.tabs[new_idx]["text"].focus_set()
        return "break"

    def _current_index(self):
        try:
            return self.nb.index("current")
        except tk.TclError:
            return None

    # ---------- Autosave ----------
    def _on_modified(self, tab):
        if not tab["text"].edit_modified():
            return
        tab["text"].edit_modified(False)
        if tab["after_id"]:
            try:
                self.root.after_cancel(tab["after_id"])
            except tk.TclError:
                pass
        tab["after_id"] = self.root.after(AUTOSAVE_MS, lambda: self._save_tab(tab))

    def _save_tab(self, tab):
        try:
            content = tab["text"].get("1.0", "end-1c")
            (NOTES_DIR / tab["file"]).write_text(content)
            self._flash_status(f"saved {tab['name']}")
        except OSError as e:
            self._flash_status(f"save failed: {e}")
        tab["after_id"] = None

    def save_all(self):
        for tab in self.tabs:
            self._save_tab(tab)

    def _flash_status(self, msg):
        self.status.configure(text=msg)
        self.root.after(1500, lambda: self.status.configure(text=""))

    def _focus_current_text(self):
        idx = self._current_index()
        if idx is not None and 0 <= idx < len(self.tabs):
            self.tabs[idx]["text"].focus_set()

    # ---------- Visibility ----------
    def show(self):
        self.root.deiconify()
        if self._saved_geometry:
            try:
                self.root.geometry(self._saved_geometry)
            except tk.TclError:
                pass
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self._focus_current_text()
        self.visible = True

    def hide(self):
        # Capture geometry BEFORE withdraw — after withdraw it returns 1x1+0+0.
        try:
            self._saved_geometry = self.root.geometry()
        except tk.TclError:
            pass
        self.save_all()
        self.save_meta()
        self.root.withdraw()
        self.visible = False

    def toggle(self):
        if self.visible and self.root.state() != "withdrawn":
            self.hide()
        else:
            self.show()

    # ---------- Socket ----------
    def start_socket_server(self):
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except OSError:
                pass
        try:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(SOCKET_PATH)
            os.chmod(SOCKET_PATH, 0o600)
            srv.listen(4)
        except OSError as e:
            print(f"socket bind failed: {e}", file=sys.stderr)
            return

        def loop():
            while True:
                try:
                    conn, _ = srv.accept()
                    data = conn.recv(64).strip()
                    conn.close()
                except OSError:
                    return
                if data == b"toggle":
                    self.root.after(0, self.toggle)
                elif data == b"show":
                    self.root.after(0, self.show)
                elif data == b"hide":
                    self.root.after(0, self.hide)
                elif data == b"quit":
                    self.root.after(0, self._quit)
                    return

        threading.Thread(target=loop, daemon=True).start()

    def _quit(self):
        try:
            if self.root.state() != "withdrawn":
                self._saved_geometry = self.root.geometry()
        except tk.TclError:
            pass
        self.save_all()
        self.save_meta()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"

    if cmd in ("toggle", "show", "hide", "quit"):
        if send_command(cmd):
            return
        if cmd in ("hide", "quit"):
            return  # daemon not running, nothing to do
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
        return
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)

    NotesApp().run()


if __name__ == "__main__":
    main()
