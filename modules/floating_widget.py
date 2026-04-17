"""
Noting Bot — Floating Desktop Widget (v2.0)
Always-on-top pill button that stays visible while working in e-Office/GeM.
Redesigned with Premium Dark aesthetics and improved reliability.
"""

import threading
import webbrowser
import tkinter as tk
from tkinter import font as tkfont
from modules.utils import CONFIG, logger

# ── Theme (Premium Dark / GitHub inspired) ────────────────────────────────────
THEME = {
    "bg":          "#000001",        # Magic transparency color
    "pill_bg":     "#161b22",        # dark-gray
    "pill_border": "#30363d",        # muted-border
    "pill_accent": "#3b82f6",        # vibrant-blue
    "pill_text":   "#f0f6fc",
    "menu_bg":     "#0d1117",        # deep-dark
    "menu_border": "#3b82f6",        # blue-glow
    "item_hover":  "#1f2937",        # slate-800
    "item_text":   "#e6edf3",
    "item_sub":    "#8b949e",
    "item_icon":   "#3b82f6",
    "sep":         "#30363d",
}

PORT = CONFIG.get("dashboard", {}).get("port", 5000)
BASE = f"http://127.0.0.1:{PORT}"

# ── Dynamic Menu Items ─────────────────────────────────────────────────────────
MENU_ITEMS = [
    ("🏠", "Dashboard",             "Home view",             f"{BASE}/#dashboard"),
    ("📝", "e-Office Noting",       "AI draft noting",       f"{BASE}/#noting"),
    ("📁", "PDF Tool",              "Merge & Compress",      f"{BASE}/#documents"),
    ("📥", "Bid Downloader",        "Bulk document DL",      f"{BASE}/#bid"),
    ("📖", "Know How (Q&A)",        "GSI manual Q&A",        f"{BASE}/#knowhow"),
    ("✅", "TEC Evaluation",         "Auto-TEC reporting",    f"{BASE}/#tec"),
    (None, None, None, None), 
    ("🧠", "Knowledge Base",        "Knowledge Management",  f"{BASE}/#kb"),
    ("⚙️", "AI Settings",           "Model configuration",   f"{BASE}/#ai"),
    (None, None, None, None),
    ("🏛️", "e-Office Portal",       "External Link",         "https://eoffice.gov.in"),
    ("💎", "GeM Portal",            "External Link",         "https://gem.gov.in"),
]


class FloatingWidget:
    """Compact always-on-top assistant with fly-out menu."""

    PILL_W  = 160
    PILL_H  = 40
    MENU_W  = 260

    def __init__(self):
        self.root = tk.Tk()
        self._menu_open = False
        self._menu_win  = None
        self._drag_x = 0
        self._drag_y = 0
        self._drag_moved = False
        self._setup_pill()

    def _setup_pill(self):
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.98)
        r.configure(bg=THEME["bg"])
        r.wm_attributes("-transparentcolor", THEME["bg"])

        # Initial Position: Bottom Right
        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        x  = sw - self.PILL_W - 30
        y  = sh - self.PILL_H - 120
        r.geometry(f"{self.PILL_W}x{self.PILL_H}+{x}+{y}")

        # Canvas for the "Modern Pill"
        c = tk.Canvas(r, width=self.PILL_W, height=self.PILL_H,
                      bg=THEME["bg"], highlightthickness=0)
        c.pack()
        self._canvas = c

        self._draw_pill()

        # Bindings
        c.bind("<Button-1>",     self._on_click)
        c.bind("<Enter>",        self._on_enter)
        c.bind("<Leave>",        self._on_leave)
        c.bind("<B1-Motion>",    self._on_drag)
        c.bind("<ButtonPress-1>",self._drag_start)

    def _draw_pill(self, hover=False):
        c = self._canvas
        c.delete("all")
        
        bg = THEME["pill_accent"] if hover else THEME["pill_bg"]
        border = THEME["pill_accent"] if hover else THEME["pill_border"]
        
        # Draw rounded rectangle (manual approximation)
        rad = 18
        c.create_rectangle(rad, 2, self.PILL_W - rad, self.PILL_H - 2, fill=bg, outline=border)
        c.create_oval(2, 2, rad*2, self.PILL_H - 2, fill=bg, outline=border)
        c.create_oval(self.PILL_W - rad*2, 2, self.PILL_W - 2, self.PILL_H - 2, fill=bg, outline=border)
        
        # Icon & Text
        c.create_text(25, self.PILL_H // 2, text="✨", fill=THEME["pill_text"], font=("Segoe UI Emoji", 12))
        c.create_text(self.PILL_W // 2, self.PILL_H // 2, 
                      text="Assistant AI ▾", 
                      fill=THEME["pill_text"], 
                      font=("Segoe UI", 9, "bold"))
        
        # Close 'X'
        c.create_text(self.PILL_W - 20, self.PILL_H // 2, text="✕", fill=THEME["pill_text"], font=("Segoe UI", 8))

    def _on_enter(self, _):
        self._draw_pill(hover=True)
        self.root.attributes("-alpha", 1.0)

    def _on_leave(self, _):
        if not self._menu_open:
            self._draw_pill(hover=False)
            self.root.attributes("-alpha", 0.98)

    def _drag_start(self, e):
        self._drag_x = e.x_root - self.root.winfo_x()
        self._drag_y = e.y_root - self.root.winfo_y()
        self._drag_moved = False

    def _on_drag(self, e):
        nx, ny = e.x_root - self._drag_x, e.y_root - self._drag_y
        self.root.geometry(f"+{nx}+{ny}")
        if self._menu_open:
            self._reposition_menu()
        self._drag_moved = True

    def _on_click(self, e):
        if self._drag_moved: return
        
        # Check if click was on the close button area
        if e.x > self.PILL_W - 35:
            self.root.destroy()
            return

        if self._menu_open:
            self._close_menu()
        else:
            self._open_menu()

    def _open_menu(self):
        self._menu_open = True
        m = tk.Toplevel(self.root)
        m.overrideredirect(True)
        m.attributes("-topmost", True)
        m.configure(bg=THEME["menu_bg"], highlightbackground=THEME["menu_border"], highlightthickness=1)
        self._menu_win = m

        row = 0
        for icon, label, sub, action in MENU_ITEMS:
            if icon is None:
                tk.Frame(m, bg=THEME["sep"], height=1).grid(row=row, column=0, sticky="ew", padx=10, pady=5)
                row += 1
                continue

            f = tk.Frame(m, bg=THEME["menu_bg"], cursor="hand2")
            f.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            m.columnconfigure(0, weight=1)

            tk.Label(f, text=icon, bg=THEME["menu_bg"], fg=THEME["item_icon"], font=("Segoe UI Emoji", 13), width=3).pack(side="left", padx=5)
            
            tf = tk.Frame(f, bg=THEME["menu_bg"])
            tf.pack(side="left", fill="both", expand=True, pady=4)
            
            tk.Label(tf, text=label, bg=THEME["menu_bg"], fg=THEME["item_text"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(tf, text=sub, bg=THEME["menu_bg"], fg=THEME["item_sub"], font=("Segoe UI", 7)).pack(anchor="w")

            # Events
            for w in [f, tf] + list(f.children.values()) + list(tf.children.values()):
                w.bind("<Enter>", lambda e, frame=f: self._menu_hover(frame, True))
                w.bind("<Leave>", lambda e, frame=f: self._menu_hover(frame, False))
                w.bind("<Button-1>", lambda e, a=action: self._item_click(a))
            
            row += 1

        m.update_idletasks()
        self._reposition_menu()
        m.bind("<FocusOut>", lambda e: self.root.after(200, self._close_if_unfocused))
        m.focus_set()

    def _menu_hover(self, frame, enter):
        color = THEME["item_hover"] if enter else THEME["menu_bg"]
        frame.configure(bg=color)
        for child in frame.winfo_children():
            child.configure(bg=color)
            if isinstance(child, tk.Frame):
                for gc in child.winfo_children():
                    gc.configure(bg=color)

    def _reposition_menu(self):
        m = self._menu_win
        if not m: return
        mh = m.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        
        # Reposition above pill
        mx = px + (self.PILL_W // 2) - (self.MENU_W // 2)
        my = py - mh - 8
        
        # Screen bounds check
        if my < 0: my = py + self.PILL_H + 8 # show below if no space above
        
        m.geometry(f"{self.MENU_W}x{mh}+{mx}+{my}")

    def _item_click(self, action):
        self._close_menu()
        if action: webbrowser.open(action)

    def _close_if_unfocused(self):
        if self._menu_win and not self._menu_win.focus_get():
            self._close_menu()

    def _close_menu(self):
        if self._menu_win:
            self._menu_win.destroy()
            self._menu_win = None
        self._menu_open = False
        self._draw_pill(hover=False)

    def run(self):
        self.root.mainloop()


def start_floating_widget():
    """Entry point for the widget thread."""
    try:
        FloatingWidget().run()
    except Exception as e:
        logger.warning(f"Widget Error: {e}")
