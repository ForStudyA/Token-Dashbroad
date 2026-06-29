"""Hermes Token Dashboard — Full-featured professional TUI dashboard.

Layout
  ┌─────────────────────────────────────────────────────────┐
  │  Header (title + clock)                                 │
  ├──────────┬──────────────────────────────────────────────┤
  │ SIDEBAR  │  MAIN DataTable                              │
  │ 25 %     │  — progress bars ████░░ 82.6 %               │
  │          │  — colour-coded hit rates                     │
  │          │  — zebra stripes + summary row                │
  ├──────────┴──────────────────────────────────────────────┤
  └─ Footer (key bindings) ─────────────────────────────────┘
"""

from __future__ import annotations

import logging

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from hermes_token_dash.parser_claude import (
    aggregate_by_model_date,
    get_available_models,
    parse_jsonl,
    scan_claude_jsonls,
)
from hermes_token_dash.parser_codex import parse_codex_jsonl, scan_codex_jsonls
from hermes_token_dash.parser_hermes import parse_hermes_sessions
from hermes_token_dash.widgets import (
    ModelsBox,
    PulseDot,
    SummaryBox,
    cache_bar,
    fmt_cost,
    fmt_tokens,
    hit_color,
)

logger = logging.getLogger(__name__)

TIME_FILTERS = ["all_time", "today", "7d", "30d"]
SORT_MODES = ["date", "input", "output", "cost"]


class TokenDashApp(App):
    """Professional TUI dashboard for AI token consumption."""

    CSS = """
    /* ── Root ─────────────────────────────────────── */
    Screen {
        layout: vertical;
    }

    /* ── Header / Footer ──────────────────────────── */
    Header {
        dock: top;
    }
    Footer {
        dock: bottom;
    }

    /* ── Main split area ──────────────────────────── */
    #main-container {
        height: 1fr;
        layout: horizontal;
    }

    /* ── Sidebar ──────────────────────────────────── */
    #sidebar {
        width: 25%;
        min-width: 34;
        height: 100%;
        layout: vertical;
        padding: 0 1 0 0;
    }

    /* ── Bordered boxes in sidebar ────────────────── */
    #models-box, #summary-box {
        border: solid $secondary;
        margin: 0 0 1 0;
        height: auto;
    }

    .box-title {
        background: $primary 25%;
        text-style: bold;
        padding: 0 1;
        color: $text;
    }

    #summary-box {
        height: auto;
    }

    #models-container {
        height: auto;
    }

    /* ── Status line (pulse dot + label) ──────────── */
    #status-line {
        height: 3;
        align: left middle;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    /* ── Model items (clickable) ──────────────────── */
    ModelItem {
        padding: 0 1;
    }
    ModelItem:hover {
        background: $accent 20%;
    }

    /* ── DataTable ────────────────────────────────── */
    DataTable {
        width: 1fr;
        height: 100%;
        border: solid $secondary;
    }
    DataTable > .datatable--header {
        background: $primary 30%;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("r", "refresh_data", "Refresh"),
        Binding("t", "cycle_time", "Time:ALL"),
        Binding("m", "cycle_model", "Model:ALL"),
        Binding("c", "toggle_cost", "Cost"),
        Binding("s", "cycle_sort", "Sort:date"),
        Binding("a", "toggle_auto", "Auto:OFF"),
        Binding("q", "quit", "Quit"),
    ]

    TITLE = "🔱 Hermes Token Dashboard"

    def __init__(self) -> None:
        super().__init__()
        self._all_usages: list = []
        self._time_filter: str = "all_time"
        self._model_filter: str = "__all__"
        self._show_cost: bool = True
        self._sort_mode: str = "date"
        self._auto_refresh_on: bool = False
        self._auto_refresh_handle = None
        self._available_models: list[str] = []

    # ── Composition ────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                with Horizontal(id="status-line"):
                    yield PulseDot()
                    yield Static("  Auto-refresh", id="status-label")
                yield ModelsBox()
                yield SummaryBox()
            yield DataTable()
        yield Footer()

    def on_mount(self) -> None:
        """Initial data load and render."""
        self._load_data()
        self._rebuild_table()
        self._update_footer()

    # ── Data loading ──────────────────────────────────

    def _load_data(self) -> None:
        all_records = []
        for path in scan_claude_jsonls():
            all_records.extend(parse_jsonl(path))
        all_records.extend(parse_hermes_sessions())
        for path in scan_codex_jsonls():
            all_records.extend(parse_codex_jsonl(path))
        self._all_usages = all_records
        self._available_models = get_available_models(all_records)

    def _get_filtered_stats(self) -> list:
        tf_map = {"all_time": "all", "today": "today",
                  "7d": "7d", "30d": "30d"}
        stats = aggregate_by_model_date(self._all_usages, tf_map[self._time_filter])
        if self._model_filter != "__all__":
            stats = [s for s in stats if s.model == self._model_filter]
        return stats

    # ── Table ─────────────────────────────────────────

    def _rebuild_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)

        cols = [
            "Model", "Date", "Input", "Output",
            "Cache Read", "Cache Crt", "Reqs",
            "Hit Rate",
        ]
        if self._show_cost:
            cols.append("Cost")

        table.add_columns(*cols)
        table.zebra_stripes = True

        stats = self._get_filtered_stats()

        # Sort
        if self._sort_mode == "input":
            stats.sort(key=lambda s: s.total_input, reverse=True)
        elif self._sort_mode == "output":
            stats.sort(key=lambda s: s.total_output, reverse=True)
        elif self._sort_mode == "cost":
            stats.sort(key=lambda s: s.estimated_cost, reverse=True)
        else:
            stats.sort(key=lambda s: (s.date, s.model))

        # ── Empty state ──────────────────────────────────
        if not stats:
            table.add_row(*["—"] * len(cols))
            self._update_sidebar(stats)
            self._update_footer()
            return

        # ── Summary row (bold, bordered) ─────────────────
        total_in = sum(s.total_input for s in stats)
        total_out = sum(s.total_output for s in stats)
        total_cr = sum(s.total_cache_read for s in stats)
        total_cc = sum(s.total_cache_creation for s in stats)
        total_req = sum(s.request_count for s in stats)
        total_rc = sum(s.requests_with_cache for s in stats)
        hit_pct = (total_rc / total_req * 100) if total_req else 0.0
        total_cost = sum(s.estimated_cost for s in stats)

        hc = hit_color(hit_pct)
        summary_row = [
            f"[bold]TOTAL[/]",
            f"[bold]{len(stats)} grps[/]",
            f"[bold]{fmt_tokens(total_in)}[/]",
            f"[bold]{fmt_tokens(total_out)}[/]",
            f"[bold]{fmt_tokens(total_cr)}[/]",
            f"[bold]{fmt_tokens(total_cc)}[/]",
            f"[bold]{total_req}[/]",
            f"[bold {hc}]{cache_bar(hit_pct)}[/]",
        ]
        if self._show_cost:
            summary_row.append(f"[bold]${total_cost:.2f}[/]")
        table.add_row(*summary_row)

        # Separator
        sep = ["─" * 12] * len(cols)
        table.add_row(*sep)

        # ── Data rows ────────────────────────────────────
        for s in stats:
            row = [
                s.model,
                s.date,
                fmt_tokens(s.total_input),
                fmt_tokens(s.total_output),
                fmt_tokens(s.total_cache_read),
                fmt_tokens(s.total_cache_creation),
                str(s.request_count),
                f"[{hit_color(s.cache_hit_rate)}]"
                f"{cache_bar(s.cache_hit_rate)}[/]",
            ]
            if self._show_cost:
                row.append(fmt_cost(s.estimated_cost))
            table.add_row(*row)

        self._update_sidebar(stats)
        self._update_footer()

    # ── Sidebar ───────────────────────────────────────

    def _update_sidebar(self, stats: list | None = None) -> None:
        if stats is None:
            stats = self._get_filtered_stats()

        # Model counts for sidebar list
        mc: dict[str, int] = {}
        for s in stats:
            mc[s.model] = mc.get(s.model, 0) + s.request_count
        sorted_models = sorted(mc.items(), key=lambda x: -x[1])

        models_box = self.query_one(ModelsBox)
        models_box.set_models(sorted_models, self._model_filter)

        summary_box = self.query_one(SummaryBox)
        summary_box.refresh(stats)

    # ── Footer helpers ────────────────────────────────

    def _make_bindings(self) -> list[Binding]:
        ms = self._model_filter if self._model_filter != "__all__" else "ALL"
        return [
            Binding("r", "refresh_data", "Refresh"),
            Binding("t", "cycle_time", f"Time:{self._time_filter.upper()}"),
            Binding("m", "cycle_model", f"Model:{ms}"),
            Binding("c", "toggle_cost", f"Cost:{'ON' if self._show_cost else 'OFF'}"),
            Binding("s", "cycle_sort", f"Sort:{self._sort_mode}"),
            Binding("a", "toggle_auto", f"Auto:{'ON' if self._auto_refresh_on else 'OFF'}"),
            Binding("q", "quit", "Quit"),
        ]

    def _rebind(self) -> None:
        self.BINDINGS = self._make_bindings()
        self.refresh_bindings()

    def _update_footer(self) -> None:
        self._rebind()

    # ── Actions ───────────────────────────────────────

    def action_refresh_data(self) -> None:
        self._load_data()
        self._rebuild_table()
        self.notify("Data refreshed", timeout=2)

    def action_cycle_time(self) -> None:
        idx = TIME_FILTERS.index(self._time_filter)
        self._time_filter = TIME_FILTERS[(idx + 1) % len(TIME_FILTERS)]
        self._rebuild_table()
        self.notify(f"Time range: {self._time_filter.upper()}", timeout=2)

    def action_cycle_model(self) -> None:
        models = self._available_models
        if not models:
            return
        if self._model_filter == "__all__":
            self._model_filter = models[0]
        else:
            idx = models.index(self._model_filter)
            self._model_filter = models[(idx + 1) % len(models)] if idx + 1 < len(models) else "__all__"
        self._rebuild_table()
        lbl = self._model_filter if self._model_filter != "__all__" else "ALL"
        self.notify(f"Model filter: {lbl}", timeout=2)

    def action_toggle_cost(self) -> None:
        self._show_cost = not self._show_cost
        self._rebuild_table()
        self.notify(f"Cost column: {'ON' if self._show_cost else 'OFF'}", timeout=2)

    def action_cycle_sort(self) -> None:
        idx = SORT_MODES.index(self._sort_mode)
        self._sort_mode = SORT_MODES[(idx + 1) % len(SORT_MODES)]
        self._rebuild_table()
        self.notify(f"Sort: {self._sort_mode}", timeout=2)

    def action_toggle_auto(self) -> None:
        if self._auto_refresh_on:
            if self._auto_refresh_handle is not None:
                try:
                    self._auto_refresh_handle.stop()
                except Exception:
                    pass
            self._auto_refresh_handle = None
            self._auto_refresh_on = False
        else:
            self._auto_refresh_handle = self.set_interval(5, self._auto_tick)
            self._auto_refresh_on = True
        self._update_pulse()
        self._update_footer()
        state = "ON" if self._auto_refresh_on else "OFF"
        self.notify(f"Auto-refresh: {state}", timeout=2)

    def _auto_tick(self) -> None:
        self._load_data()
        self._rebuild_table()

    def _update_pulse(self) -> None:
        self.query_one(PulseDot).set_active(self._auto_refresh_on)

    # ── Sidebar model-selection handler ────────────────

    def on_models_box_model_selected(
        self, event: ModelsBox.ModelSelected,
    ) -> None:
        """Handle model selection from the sidebar clickable list."""
        event.stop()
        self._model_filter = event.model_name
        self._rebuild_table()
        lbl = self._model_filter if self._model_filter != "__all__" else "ALL"
        self.notify(f"Model filter: {lbl}", timeout=2)


def main() -> None:
    app = TokenDashApp()
    app.run()


if __name__ == "__main__":
    main()
