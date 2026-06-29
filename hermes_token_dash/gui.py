"""Hermes Token Dashboard — Modern Desktop GUI.

Uses real native widgets: ttk.Progressbar for hit-rate, 
custom scrollable table with embedded progress bars.
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# ── Windows DPI awareness (fixes blurry text on HiDPI displays) ──
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PerMonitorV2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from hermes_token_dash.parser_claude import (
    aggregate_by_model_date,
    get_available_models,
    parse_jsonl,
    scan_claude_jsonls,
)
from hermes_token_dash.parser_codex import parse_codex_jsonl, scan_codex_jsonls
from hermes_token_dash.parser_hermes import parse_hermes_sessions

TIME_FILTERS = ["all", "today", "7d", "30d"]
TIME_LABELS = {"all": "All Time", "today": "Today", "7d": "Last 7 Days", "30d": "Last 30 Days"}


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


# ── theme ────────────────────────────────────────────────────────

C = {
    "bg":        "#0d1117",
    "surface":   "#161b22",
    "card":      "#1c2333",
    "border":    "#30363d",
    "accent":    "#58a6ff",
    "green":     "#3fb950",
    "yellow":    "#d29922",
    "red":       "#f85149",
    "text":      "#c9d1d9",
    "white":     "#f0f6fc",
    "muted":     "#6e7681",
    "hilight":   "#1f2a3c",
    "summary":   "#1a3a5c",
}


COL_SPECS = [
    ("model",       "Model",        300),
    ("date",        "Date",         160),
    ("input",       "Input",        140),
    ("output",      "Output",       140),
    ("cache_read",  "Cache Read",   170),
    ("cache_create","Cache Create", 170),
    ("requests",    "Reqs",         100),
    ("hit_rate",    "Hit Rate",     260),
]


class TokenDashApp:
    """Modern desktop GUI."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Hermes Token Dashboard")
        self.root.geometry("1700x950")
        self.root.minsize(1200, 600)
        self.root.configure(bg=C["bg"])

        self._all_usages: list = []
        self._models: list[str] = []
        self._time_filter = tk.StringVar(value="all")
        self._auto_refresh = tk.BooleanVar(value=True)
        self._model_vars: dict[str, tk.BooleanVar] = {}
        self._sort_col: str | None = None
        self._sort_rev = False
        self._refresh_job: str | None = None
        self._table_rows: list[dict] = []  # {frame, labels, progressbar}
        self._model_filter_val: str | None = None

        self._setup_theme()
        self._build_ui()
        self._load_and_render()
        self._start_auto_refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_theme(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=C["bg"], foreground=C["text"],
                        font=("Segoe UI", 18))
        style.configure("TFrame", background=C["bg"])
        style.configure("TLabel", background=C["bg"], foreground=C["text"])
        style.configure("TButton", background=C["surface"],
                        foreground=C["white"], borderwidth=0,
                        padding=(14, 6), font=("Segoe UI", 20))
        style.map("TButton", background=[("active", C["accent"])])
        style.configure("TCombobox", background=C["card"],
                        foreground=C["text"], fieldbackground=C["card"])
        style.map("TCombobox", fieldbackground=[("readonly", C["card"])])
        style.configure("TCheckbutton", background=C["bg"],
                        foreground=C["text"])
        style.configure("TScrollbar", background=C["surface"],
                        troughcolor=C["bg"])

        # Progressbar styles
        for name, color in [("green", C["green"]),
                            ("yellow", C["yellow"]),
                            ("red", C["red"])]:
            style.configure(f"{name}.Horizontal.TProgressbar",
                            background=color, troughcolor=C["border"],
                            bordercolor=C["border"])

        self.root.option_add("*TCombobox*Listbox.background", C["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["hilight"])

    # ── build ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Title bar
        tb = tk.Frame(self.root, bg=C["surface"], height=56)
        tb.pack(fill="x")
        tb.pack_propagate(False)
        tk.Label(tb, text="  ◈  Hermes Token Dashboard",
                 bg=C["surface"], fg=C["white"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", pady=10)
        self._badge = tk.Label(tb, text="", bg=C["surface"],
                               fg=C["muted"], font=("Segoe UI", 18))
        self._badge.pack(side="right", padx=16, pady=12)

        # Toolbar
        bar = tk.Frame(self.root, bg=C["surface"], height=52)
        bar.pack(fill="x", pady=(1, 0))
        bar.pack_propagate(False)

        ttk.Label(bar, text="Time Range  ", background=C["surface"],
                  foreground=C["text"]).pack(side="left", padx=(16, 4))
        cb = ttk.Combobox(bar, textvariable=self._time_filter,
                          values=list(TIME_LABELS.keys()),
                          state="readonly", width=14)
        cb.pack(side="left", padx=(0, 16))
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_filter_change())

        ttk.Button(bar, text="  ↻  Refresh",
                   command=self._on_refresh).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(bar, text="Auto-refresh (5s)",
                        variable=self._auto_refresh,
                        command=self._on_auto_toggle).pack(side="left")

        self._refresh_label = tk.Label(bar, text="", bg=C["surface"],
                                       fg=C["muted"], font=("Segoe UI", 16))
        self._refresh_label.pack(side="right", padx=16)

        # Body
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        # -- sidebar --
        sidebar = tk.Frame(body, bg=C["surface"], width=320,
                           highlightthickness=0)
        sidebar.pack(side="left", fill="y", padx=(0, 10))
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="MODELS", bg=C["surface"], fg=C["accent"],
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=16, pady=(14, 4))

        sf = tk.Frame(sidebar, bg=C["surface"])
        sf.pack(fill="x", padx=12)
        self._model_canvas = tk.Canvas(sf, bg=C["surface"], highlightthickness=0,
                                       height=240, width=200)
        scrollbar = ttk.Scrollbar(sf, orient="vertical",
                                  command=self._model_canvas.yview)
        self._model_inner = tk.Frame(self._model_canvas, bg=C["surface"])
        self._model_inner.bind("<Configure>",
                               lambda e: self._model_canvas.configure(
                                   scrollregion=self._model_canvas.bbox("all")))
        self._model_canvas.create_window((0, 0), window=self._model_inner, anchor="nw")
        self._model_canvas.configure(yscrollcommand=scrollbar.set)
        self._model_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        tk.Label(sidebar, text="SUMMARY", bg=C["surface"], fg=C["accent"],
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=16, pady=(16, 4))

        self._summ_frame = tk.Frame(sidebar, bg=C["card"])
        self._summ_frame.pack(fill="x", padx=12, ipady=4)
        self._summ_w: dict[str, tk.Label] = {}
        for key, lbl in [("input","Input"),("output","Output"),
                         ("requests","Requests"),("cost","Est. Cost"),
                         ("hit","Hit Rate")]:
            row = tk.Frame(self._summ_frame, bg=C["card"])
            row.pack(fill="x", padx=14, pady=3)
            tk.Label(row, text=lbl, bg=C["card"], fg=C["muted"],
                     font=("Segoe UI", 18), width=10, anchor="w").pack(side="left")
            val = tk.Label(row, text="—", bg=C["card"], fg=C["white"],
                           font=("Segoe UI", 22, "bold"))
            val.pack(side="right")
            self._summ_w[key] = val

        # -- right: table --
        right = tk.Frame(body, bg=C["surface"])
        right.pack(side="left", fill="both", expand=True)

        # Header
        hdr = tk.Frame(right, bg=C["card"], height=36)
        hdr.pack(fill="x", padx=8, pady=(8, 0))
        hdr.pack_propagate(False)
        for col_id, heading, width in COL_SPECS:
            lbl = tk.Label(hdr, text=heading, bg=C["card"], fg=C["accent"],
                           font=("Segoe UI", 18, "bold"),
                           width=width // 9, anchor="w" if col_id == "model" else "center")
            lbl.pack(side="left", padx=1, pady=6)
            lbl.bind("<Button-1>",
                     lambda e, c=col_id: self._on_sort(c))

        # Scrollable body
        self._table_canvas = tk.Canvas(right, bg=C["surface"],
                                       highlightthickness=0)
        t_scroll = ttk.Scrollbar(right, orient="vertical",
                                 command=self._table_canvas.yview)
        self._table_inner = tk.Frame(self._table_canvas, bg=C["surface"])
        self._table_inner.bind("<Configure>",
                               lambda e: self._table_canvas.configure(
                                   scrollregion=self._table_canvas.bbox("all")))
        self._table_canvas.create_window((0, 0), window=self._table_inner,
                                         anchor="nw", tags="inner")
        self._table_canvas.configure(yscrollcommand=t_scroll.set)

        self._table_canvas.pack(side="left", fill="both", expand=True, padx=8)
        t_scroll.pack(side="right", fill="y", pady=(0, 8))

        # Bind canvas resize to adjust inner frame width
        self._table_canvas.bind("<Configure>",
            lambda e: self._table_canvas.itemconfigure(
                "inner", width=e.width))

        # Mousewheel
        def _mw(event):
            self._table_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._table_canvas.bind("<Enter>",
            lambda e: self._table_canvas.bind_all("<MouseWheel>", _mw))
        self._table_canvas.bind("<Leave>",
            lambda e: self._table_canvas.unbind_all("<MouseWheel>"))

        # Status bar
        sb = tk.Frame(self.root, bg=C["surface"], height=24,
                      highlightthickness=0)
        sb.pack(fill="x", side="bottom")
        sb.pack_propagate(False)
        self._status_text = tk.Label(sb, text="Ready", bg=C["surface"],
                                     fg=C["muted"], font=("Segoe UI", 16))
        self._status_text.pack(side="left", padx=12)

    # ── data ────────────────────────────────────────────────────

    def _load_data(self) -> None:
        self._status_text.configure(text="Scanning...")
        self.root.update_idletasks()
        records = []
        files = scan_claude_jsonls()
        for f in files:
            records.extend(parse_jsonl(f))
        records.extend(parse_hermes_sessions())
        codex_files = scan_codex_jsonls()
        for f in codex_files:
            records.extend(parse_codex_jsonl(f))
        self._all_usages = records
        self._models = get_available_models(records)
        now = datetime.now().strftime("%H:%M:%S")
        self._refresh_label.configure(
            text=f"Updated {now}  |  {len(files)} Claude + Hermes + {len(codex_files)} Codex"
        )
        self._status_text.configure(text="Ready")

    def _load_and_render(self) -> None:
        self._load_data()
        self._render_all()

    def _get_stats(self) -> list:
        stats = aggregate_by_model_date(self._all_usages,
                                        self._time_filter.get())
        active = {m for m, v in self._model_vars.items()
                  if v.get() and m != "__all__"}
        if active and "__all__" not in active:
            stats = [s for s in stats if s.model in active]
        elif not self._model_vars.get("__all__",
                                      tk.BooleanVar(value=True)).get():
            stats = []

        col_map = {
            "model": lambda s: (s.date, s.model),
            "date": lambda s: s.date,
            "input": lambda s: s.total_input,
            "output": lambda s: s.total_output,
            "cache_read": lambda s: s.total_cache_read,
            "cache_create": lambda s: s.total_cache_creation,
            "requests": lambda s: s.request_count,
            "hit_rate": lambda s: s.cache_hit_rate,
        }
        key_fn = col_map.get(self._sort_col, col_map["model"])
        stats.sort(key=key_fn, reverse=self._sort_rev)
        return stats

    # ── render ──────────────────────────────────────────────────

    def _render_all(self) -> None:
        self._render_models()
        self._render_table()
        self._render_summary()
        stats = self._get_stats()
        tc = sum(s.estimated_cost for s in stats)
        tr = sum(s.request_count for s in stats)
        tf = TIME_LABELS.get(self._time_filter.get(), "All")
        self._badge.configure(
            text=f"{tf}  ·  {len(stats)} groups  ·  {tr} requests  ·  ${tc:.2f}")

    def _render_models(self) -> None:
        for w in self._model_inner.winfo_children():
            w.destroy()
        self._model_vars.clear()

        # Determine active model
        all_on = True
        for m, v in self._model_vars.items():
            if m != "__all__" and v.get():
                all_on = False
        if all_on:
            self._model_filter_val = None

        self._add_model_check("ALL", None, len(self._all_usages),
                              self._model_filter_val is None)
        for m in self._models:
            count = sum(1 for u in self._all_usages if u.model == m)
            self._add_model_check(m, m, count,
                                  self._model_filter_val == m)

    def _add_model_check(self, label: str, model: str | None,
                         count: int, selected: bool) -> None:
        frame = tk.Frame(self._model_inner, bg=C["surface"], cursor="hand2")
        frame.pack(fill="x", pady=1)

        var = tk.BooleanVar(value=selected)
        cb = ttk.Checkbutton(frame, text=f"{label}  ({count})", variable=var)
        cb.pack(side="left")
        cb.configure(command=lambda m=model, v=var:
                     self._on_model_select(m))

        if model is None:
            self._model_vars["__all__"] = var
        else:
            self._model_vars[model] = var

    def _on_model_select(self, model: str | None) -> None:
        self._model_filter_val = model
        # Sync checkboxes
        for m, var in self._model_vars.items():
            expect = (m == "__all__" and model is None) or (m == model)
            var.set(expect)
        self._render_table()
        self._render_summary()

    # ── table with real Progressbars ─────────────────────────────

    def _render_table(self) -> None:
        for w in self._table_inner.winfo_children():
            w.destroy()

        stats = self._get_stats()
        if not stats:
            tk.Label(self._table_inner, text="No data for this selection",
                     bg=C["surface"], fg=C["muted"],
                     font=("Segoe UI", 24)).pack(pady=40)
            return

        # Summary row
        ti = sum(s.total_input for s in stats)
        to_ = sum(s.total_output for s in stats)
        tcr = sum(s.total_cache_read for s in stats)
        tcc = sum(s.total_cache_creation for s in stats)
        tr = sum(s.request_count for s in stats)
        trc = sum(s.requests_with_cache for s in stats)
        hit = trc / tr * 100 if tr > 0 else 0

        self._add_row([
            "◆ TOTAL", f"{len(stats)} groups",
            _fmt(ti), _fmt(to_), _fmt(tcr), _fmt(tcc), str(tr),
        ], hit, bg=C["summary"], fg=C["white"], bold=True)

        # Separator
        sep = tk.Frame(self._table_inner, height=1, bg=C["border"])
        sep.pack(fill="x", pady=2)

        # Data rows
        for s in stats:
            self._add_row([
                s.model, s.date,
                _fmt(s.total_input), _fmt(s.total_output),
                _fmt(s.total_cache_read), _fmt(s.total_cache_creation),
                str(s.request_count),
            ], s.cache_hit_rate)

    def _add_row(self, values: list[str], hit_rate: float,
                 bg: str = "", fg: str = C["text"],
                 bold: bool = False) -> None:
        frame = tk.Frame(self._table_inner, bg=bg or C["surface"], height=34)
        frame.pack(fill="x", pady=1)
        frame.pack_propagate(False)

        font = ("Segoe UI", 10, "bold") if bold else ("Segoe UI", 10)
        col_widths = [300, 160, 140, 140, 170, 170, 100]
        anchors = ["w", "center", "center", "center", "center", "center", "center"]

        for i, (val, w, anchor) in enumerate(zip(values, col_widths, anchors)):
            kw = {"text": val, "bg": bg or C["surface"], "fg": fg,
                  "font": font, "width": w // 9, "anchor": anchor}
            lbl = tk.Label(frame, **kw)
            lbl.pack(side="left", padx=0)

        # Progressbar
        pb_frame = tk.Frame(frame, bg=bg or C["surface"], width=260, height=34)
        pb_frame.pack(side="left", fill="x")
        pb_frame.pack_propagate(False)

        if hit_rate >= 80:
            pstyle = "green.Horizontal.TProgressbar"
            pcolor = C["green"]
        elif hit_rate >= 40:
            pstyle = "yellow.Horizontal.TProgressbar"
            pcolor = C["yellow"]
        else:
            pstyle = "red.Horizontal.TProgressbar"
            pcolor = C["red"]

        pb = ttk.Progressbar(pb_frame, style=pstyle, length=160,
                             mode="determinate", value=hit_rate)
        pb.pack(side="left", pady=8)

        tk.Label(pb_frame, text=f"  {hit_rate:.1f}%",
                 bg=bg or C["surface"], fg=pcolor,
                 font=("Segoe UI", 16, "bold"), width=6
                 ).pack(side="left")

        # Hover + double-click
        if not bold:
            def _enter(e, f=frame):
                f.configure(bg=C["hilight"])
                for c in f.winfo_children():
                    try:
                        c.configure(bg=C["hilight"])
                    except Exception:
                        pass
                    if isinstance(c, tk.Frame):
                        for gc in c.winfo_children():
                            try:
                                gc.configure(bg=C["hilight"])
                            except Exception:
                                pass

            def _leave(e, f=frame):
                f.configure(bg=C["surface"])
                for c in f.winfo_children():
                    try:
                        c.configure(bg=C["surface"])
                    except Exception:
                        pass

            frame.bind("<Enter>", _enter)
            frame.bind("<Leave>", _leave)
            for child in frame.winfo_children():
                child.bind("<Double-Button-1>",
                           lambda e, v=values: self._on_row_dbl_click(v))
                child.bind("<Enter>", _enter)
                child.bind("<Leave>", _leave)

    def _on_row_dbl_click(self, values: list[str]) -> None:
        model = values[0]
        if model and model != "◆ TOTAL":
            self._on_model_select(model)

    # ── summary ──────────────────────────────────────────────────

    def _render_summary(self) -> None:
        stats = self._get_stats()
        if not stats:
            for v in self._summ_w.values():
                v.configure(text="—", fg=C["muted"])
            return

        ti = sum(s.total_input for s in stats)
        to_ = sum(s.total_output for s in stats)
        tc = sum(s.estimated_cost for s in stats)
        tr = sum(s.request_count for s in stats)
        trc = sum(s.requests_with_cache for s in stats)
        hit = trc / tr * 100 if tr > 0 else 0

        hc = C["green"] if hit >= 80 else (C["yellow"] if hit >= 40 else C["red"])
        data = [
            ("input", _fmt(ti), C["white"]),
            ("output", _fmt(to_), C["white"]),
            ("requests", str(tr), C["white"]),
            ("cost", f"${tc:.2f}", C["green"]),
            ("hit", f"{hit:.1f}%", hc),
        ]
        for key, val, color in data:
            self._summ_w[key].configure(text=val, fg=color)

    # ── events ──────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        self._load_and_render()

    def _on_filter_change(self) -> None:
        self._render_table()
        self._render_summary()

    def _on_sort(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._render_table()

    def _on_auto_toggle(self) -> None:
        if self._auto_refresh.get():
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()

    def _start_auto_refresh(self) -> None:
        self._stop_auto_refresh()
        self._do_auto()

    def _stop_auto_refresh(self) -> None:
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None

    def _do_auto(self) -> None:
        if self._auto_refresh.get():
            self._load_and_render()
            self._refresh_job = self.root.after(5000, self._do_auto)

    def _on_close(self) -> None:
        self._stop_auto_refresh()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    TokenDashApp().run()


if __name__ == "__main__":
    main()
