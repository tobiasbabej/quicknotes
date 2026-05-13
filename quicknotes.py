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
from datetime import datetime
from pathlib import Path
from tkinter import simpledialog, ttk

NOTES_DIR = Path.home() / ".local/share/quicknotes"
ARCHIVE_DIR = NOTES_DIR / "archive"
META_FILE = NOTES_DIR / "tabs.json"
SOCKET_PATH = f"/tmp/quicknotes-{os.getuid()}.sock"
AUTOSAVE_MS = 400

# Dark-green CRT-ish palette. All UI colors flow from here.
BG          = "#0a1f14"  # window / frames
BG_ALT      = "#13301f"  # buttons, idle tabs
BG_HOVER    = "#1f4a30"  # active button, selected tab
EDITOR_BG   = "#0d2419"  # text widget interior
FG          = "#9be09b"  # primary text
FG_DIM      = "#6aa66a"  # idle tab label, helper labels
FG_FAINT    = "#4a7a4a"  # status footer
FG_BRIGHT   = "#d6ffd6"  # selected tab text / cursor
SEL_BG      = "#1f5a3a"  # text selection background
CLOSE_X     = "#6aa66a"  # tab × idle
CLOSE_X_HOT = "#ff7070"  # tab × hover (red kept for "destructive" affordance)
HIT_BG      = "#2a5a1a"  # search-match highlight bg
HIT_FG      = "#ffffff"

EDITOR_FONT = ("Monospace", 11)
UI_FONT     = ("Monospace", 9)


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
        # Ask the WM for a borderless window instead of overrideredirect —
        # the latter breaks keyboard focus under GNOME Wayland/XWayland.
        try:
            self.root.attributes("-type", "splash")
        except tk.TclError:
            pass
        self.root.configure(bg=BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TButton", padding=4, background=BG_ALT,
                        foreground=FG, borderwidth=0, font=UI_FONT)
        style.map("TButton", background=[("active", BG_HOVER)],
                  foreground=[("active", FG_BRIGHT)])
        style.configure("TSeparator", background=BG_HOVER)

        self._setup_closeable_notebook(style)

        self._build_drag_bar()
        self.container = ttk.Frame(self.root)
        self.container.pack(fill="both", expand=True)
        self._build_search_bar()

        self.nb = ttk.Notebook(self.container, style="Closeable.TNotebook")
        self.nb.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self._pressed_close_idx = None
        self.nb.bind("<ButtonPress-1>", self._on_tab_press, add=True)
        self.nb.bind("<ButtonRelease-1>", self._on_tab_release, add=True)

        side = ttk.Frame(self.container)
        side.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(side, text="+ new", width=10, command=self.new_tab).pack(pady=2)
        ttk.Button(side, text="rename", width=10, command=self.rename_current_tab).pack(pady=2)
        ttk.Button(side, text="× close", width=10, command=self.close_current_tab).pack(pady=2)
        ttk.Separator(side, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(side, text="↳ archive", width=10, command=self.archive_current_tab).pack(pady=2)
        ttk.Button(side, text="hide (Esc)", width=10, command=self.hide).pack(pady=2)
        self.status = ttk.Label(side, text="", background=BG,
                                foreground=FG_FAINT, font=UI_FONT)
        self.status.pack(side="bottom", pady=4)

        self.tabs = []
        self.load_tabs()
        if not self.tabs:
            self.new_tab(name="Note 1")

        self._build_resize_grip()

        self.root.bind_all("<Escape>", self._on_escape)
        self.root.bind_all("<Alt-n>", lambda e: (self.new_tab(), "break")[1])
        self.root.bind_all("<Alt-p>", lambda e: (self.close_current_tab(), "break")[1])
        self.root.bind_all("<Alt-s>", lambda e: (self.archive_current_tab(), "break")[1])
        self.root.bind_all("<Alt-f>", lambda e: (self._open_search(), "break")[1])
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

        self._img_close = make_x(CLOSE_X)
        self._img_close_hover = make_x(CLOSE_X_HOT)

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
        style.configure("Closeable.TNotebook", background=BG, borderwidth=0)
        style.configure("Closeable.TNotebook.Tab", padding=[10, 4],
                        background=BG_ALT, foreground=FG_DIM, font=UI_FONT)
        style.map("Closeable.TNotebook.Tab",
                  background=[("selected", BG_HOVER)],
                  foreground=[("selected", FG_BRIGHT)])

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
        text = tk.Text(frame, wrap="word", bg=EDITOR_BG, fg=FG,
                       insertbackground=FG_BRIGHT, relief="flat",
                       font=EDITOR_FONT, undo=True, padx=10, pady=10,
                       selectbackground=SEL_BG)
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

    def archive_current_tab(self):
        idx = self._current_index()
        if idx is None:
            return
        tab = self.tabs[idx]
        self._save_tab(tab)
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = ARCHIVE_DIR / f"{safe_filename(tab['name'])}_{ts}.txt"
            target.write_text(tab["text"].get("1.0", "end-1c"))
            self._flash_status(f"archived → {target.name}")
        except OSError as e:
            self._flash_status(f"archive failed: {e}")

    def _flash_status(self, msg):
        self.status.configure(text=msg)
        self.root.after(1500, lambda: self.status.configure(text=""))

    def _focus_current_text(self):
        idx = self._current_index()
        if idx is not None and 0 <= idx < len(self.tabs):
            self.tabs[idx]["text"].focus_set()

    # ---------- Drag bar + resize grip (replace WM chrome) ----------
    def _build_drag_bar(self):
        self.drag_bar = tk.Frame(self.root, bg=BG_HOVER, height=6, cursor="fleur")
        self.drag_bar.pack(side="top", fill="x")
        self.drag_bar.bind("<ButtonPress-1>", self._on_drag_start)
        self.drag_bar.bind("<B1-Motion>", self._on_drag_motion)
        self.drag_bar.bind("<Enter>", lambda e: self.drag_bar.configure(bg=FG_DIM))
        self.drag_bar.bind("<Leave>", lambda e: self.drag_bar.configure(bg=BG_HOVER))

    def _on_drag_start(self, event):
        self._drag_dx = event.x_root - self.root.winfo_x()
        self._drag_dy = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event):
        x = event.x_root - self._drag_dx
        y = event.y_root - self._drag_dy
        self.root.geometry(f"+{x}+{y}")

    def _build_resize_grip(self):
        self.resize_grip = tk.Frame(self.root, bg=FG_DIM, width=12, height=12,
                                    cursor="bottom_right_corner")
        self.resize_grip.place(relx=1.0, rely=1.0, anchor="se")
        self.resize_grip.bind("<ButtonPress-1>", self._on_resize_start)
        self.resize_grip.bind("<B1-Motion>", self._on_resize_motion)
        self.resize_grip.bind("<Enter>", lambda e: self.resize_grip.configure(bg=FG_BRIGHT))
        self.resize_grip.bind("<Leave>", lambda e: self.resize_grip.configure(bg=FG_DIM))
        self.resize_grip.lift()

    def _on_resize_start(self, event):
        self._resize_anchor = (event.x_root, event.y_root,
                               self.root.winfo_width(), self.root.winfo_height())

    def _on_resize_motion(self, event):
        x0, y0, w0, h0 = self._resize_anchor
        min_w, min_h = 420, 240
        new_w = max(min_w, w0 + (event.x_root - x0))
        new_h = max(min_h, h0 + (event.y_root - y0))
        self.root.geometry(f"{new_w}x{new_h}")

    # ---------- Search ----------
    def _build_search_bar(self):
        self.search_frame = ttk.Frame(self.root)
        self.search_visible = False
        self._search_hits = []
        self._search_after_id = None

        row = ttk.Frame(self.search_frame)
        row.pack(side="top", fill="x", padx=6, pady=(6, 2))
        ttk.Label(row, text="search:", background=BG,
                  foreground=FG_DIM, font=UI_FONT).pack(side="left", padx=(0, 6))
        self.search_entry = tk.Entry(row, bg=EDITOR_BG, fg=FG,
                                     insertbackground=FG_BRIGHT, relief="flat",
                                     font=EDITOR_FONT)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="×", width=3, command=self._close_search).pack(side="left", padx=(6, 0))

        self.search_results = tk.Listbox(
            self.search_frame, height=6, bg=EDITOR_BG, fg=FG,
            selectbackground=SEL_BG, relief="flat",
            font=("Monospace", 10), activestyle="none", borderwidth=0,
            highlightthickness=0,
        )
        self.search_results.pack(side="top", fill="x", padx=6, pady=(0, 6))

        self.search_entry.bind("<KeyRelease>", self._on_search_type)
        self.search_entry.bind("<Return>", lambda e: self._focus_results())
        self.search_entry.bind("<Down>", lambda e: self._focus_results())
        self.search_entry.bind("<Escape>", lambda e: (self._close_search(), "break")[1])
        self.search_results.bind("<<ListboxSelect>>", self._on_result_select)
        self.search_results.bind("<Return>", lambda e: self._on_result_select())
        self.search_results.bind("<Double-Button-1>", self._on_result_select)
        self.search_results.bind("<Escape>", lambda e: (self._close_search(), "break")[1])

    def _open_search(self):
        if not self.search_visible:
            self.search_frame.pack(before=self.container, fill="x")
            self.search_visible = True
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _close_search(self):
        if not self.search_visible:
            return
        self.search_frame.pack_forget()
        self.search_visible = False
        for tab in self.tabs:
            try:
                tab["text"].tag_remove("search_hit", "1.0", "end")
            except tk.TclError:
                pass
        self._focus_current_text()

    def _on_search_type(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        if self._search_after_id:
            try:
                self.root.after_cancel(self._search_after_id)
            except tk.TclError:
                pass
        q = self.search_entry.get()
        self._search_after_id = self.root.after(120, lambda: self._do_search(q))

    def _do_search(self, query):
        self._search_hits = []
        if not query:
            self._render_results()
            return
        q_lower = query.lower()
        cap = 50
        for tab_idx, tab in enumerate(self.tabs):
            content = tab["text"].get("1.0", "end-1c")
            for line_no, line_text in enumerate(content.split("\n"), start=1):
                line_lower = line_text.lower()
                start = 0
                while True:
                    pos = line_lower.find(q_lower, start)
                    if pos < 0:
                        break
                    preview = line_text
                    if len(preview) > 60:
                        s = max(0, pos - 20)
                        preview = ("…" if s > 0 else "") + line_text[s:s + 60] + \
                                  ("…" if s + 60 < len(line_text) else "")
                    self._search_hits.append({
                        "tab_idx": tab_idx,
                        "line": line_no,
                        "col": pos,
                        "length": len(query),
                        "label": f"{tab['name']} · L{line_no} — {preview.strip()}",
                    })
                    if len(self._search_hits) >= cap:
                        break
                    start = pos + max(1, len(query))
                if len(self._search_hits) >= cap:
                    break
            if len(self._search_hits) >= cap:
                break
        self._render_results()

    def _render_results(self):
        self.search_results.delete(0, "end")
        for hit in self._search_hits:
            self.search_results.insert("end", hit["label"])
        if self._search_hits:
            self.search_results.selection_clear(0, "end")

    def _focus_results(self):
        if self.search_results.size() == 0:
            return "break"
        self.search_results.focus_set()
        self.search_results.selection_clear(0, "end")
        self.search_results.selection_set(0)
        self.search_results.activate(0)
        self._on_result_select()
        return "break"

    def _on_result_select(self, event=None):
        sel = self.search_results.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(self._search_hits)):
            return
        hit = self._search_hits[idx]
        self.nb.select(hit["tab_idx"])
        text = self.tabs[hit["tab_idx"]]["text"]
        for tab in self.tabs:
            try:
                tab["text"].tag_remove("search_hit", "1.0", "end")
            except tk.TclError:
                pass
        start = f"{hit['line']}.{hit['col']}"
        end = f"{hit['line']}.{hit['col'] + hit['length']}"
        text.tag_add("search_hit", start, end)
        text.tag_configure("search_hit", background=HIT_BG, foreground=HIT_FG)
        text.mark_set("insert", start)
        text.see(start)

    def _on_escape(self, event=None):
        if self.search_visible:
            self._close_search()
            return "break"
        self.hide()
        return "break"

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
