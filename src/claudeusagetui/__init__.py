from __future__ import annotations

import logging
import platform
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Footer, Label, Static

from .data import (
    DailyUsage,
    aggregate_usage,
    all_model_names,
    fetch_usage,
    fmt_cost,
    fmt_tokens,
    model_provider,
    short_model,
    sparkline,
)

log = logging.getLogger("claudeusagetui")

# ---------------------------------------------------------------------------
# Tamagotchi cat animation frames (subset of the bash script tiers)
# ---------------------------------------------------------------------------

CAT_FRAMES: dict[str, list[str]] = {
    "sleeping": [
        "~(\u02d8\u02d8 )  z    ", "~(\u02d8\u02d8 )  zZ   ", "~(\u02d8\u02d8 )  zzZ  ",
        "~(\u02d8\u02d8 ) zzZZZ ", "~(\u02d8\u02d8 )  zzZ  ", "~(\u02d8\u02d8 )  zZ   ",
        "~(\u02d8\u02d8 )  z    ", "~(\u02d8\u02d8 )       ",
    ],
    "waking": [
        "~(\u02d8\u02d8 )       ", " (\u1d54_\u1d54)      ", " (\u1d54o\u1d54)      ",
        " (\u1d54O\u1d54)  ~   ", " (\u1d54O\u1d54) ~~   ", " (\u1d54o\u1d54)  ~   ",
        " (\u1d54_\u1d54)/     ", " (\u1d54\u1d25\u1d54)      ",
    ],
    "playful": [
        " (\u1d54\u1d25\u1d54)  o   ", " (\u1d54\u1d25\u1d54)/ o   ", " (\u1d54\u1d25\u1d54)/o    ",
        "  o(\u1d54\u1d25\u1d54)    ", " o (\u1d54\u1d25\u1d54)    ", " (\u1d54\u1d25\u1d54)o     ",
        " (\u1d54\u1d25\u1d54) o    ", " (\u1d54\u1d25\u1d54)  o   ",
    ],
    "satisfied": [
        " (=^.^=)     ", " (=^.^=)  ~  ", " (=^.^=) \u2661   ", " (=^.^=)  \u2661  ",
        " (=^.^=)   \u2661 ", " (=^.^=)     ", " (=^.^=) \u2661 \u2661 ", " (=^.^=)  ~  ",
    ],
    "nervous": [
        " (\u1d54_\u1d54;)  $  ", " (\u1d54_\u1d54;) $   ", " (\u1d54_\u1d54;)$    ",
        " (\u1d54_\u1d54;)     ", " (\u1d54_\u1d54;) ..  ", " (\u1d54_\u1d54;)...  ",
        " (\u1d54_\u1d54;) ..  ", " (\u1d54_\u1d54;)  .  ",
    ],
    "alarm": [
        " (>_<) !!!   ", " (>_<)  *!*  ", " (>_<) *!!!* ", " (>_<)  *!*  ",
        " (>_<) !!!   ", "  (>_<) !!   ", " (>_<) *!!!* ", " (>_<)  !!!  ",
    ],
    "nuclear": [
        " (X_X) ~*!*~ ", "*(X_X)*!$!$!*", " ~(x_x)~ $$$ ", "*!*(@_@)*!*! ",
        "~$(X_X)$~!!! ", "*!(@_@;)$$$!*", "~*~(X_X)~*~! ", "*$*!(@_@)!*$*",
    ],
}

# Spend thresholds -> animation tier
CAT_TIERS: list[tuple[float, str]] = [
    (0, "sleeping"),
    (50, "waking"),
    (100, "playful"),
    (200, "satisfied"),
    (350, "nervous"),
    (500, "alarm"),
    (700, "nuclear"),
]


def pick_cat_tier(daily_cost: float) -> str:
    tier = "sleeping"
    for threshold, name in CAT_TIERS:
        if daily_cost >= threshold:
            tier = name
    return tier


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

USAGE_THEME = Theme(
    name="claudeusage",
    primary="#00a4d6",
    secondary="#007a9e",
    accent="#00a4d6",
    warning="#e65100",
    error="#b71c1c",
    success="#1b5e20",
    background="#ffffff",
    surface="#ffffff",
    panel="#f5f5f5",
    foreground="#1a1a1a",
    dark=False,
)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class HeaderBar(Static):
    DEFAULT_CSS = """
    HeaderBar { dock: top; height: 1; background: #00a4d6; color: #ffffff; padding: 0 1; }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._frame_idx = 0
        self._daily_cost = 0.0
        self._cost_30d = 0.0

    def set_costs(self, daily: float, cost_30d: float) -> None:
        self._daily_cost = daily
        self._cost_30d = cost_30d

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        if self.size.width < 10:
            return
        tier = pick_cat_tier(self._daily_cost)
        frames = CAT_FRAMES[tier]
        cat = frames[self._frame_idx % len(frames)]
        self._frame_idx += 1

        host = platform.node().split(".")[0]
        now = datetime.now().strftime("%H:%M:%S")

        left = f"[bold]{cat}[/]  Claude Usage  ${round(self._daily_cost)} today \u00b7 ${round(self._cost_30d)}/30d"
        left_plain = f"{cat}  Claude Usage  ${round(self._daily_cost)} today \u00b7 ${round(self._cost_30d)}/30d"
        right = f"{host}  {now}"
        pad = max(1, self.size.width - len(left_plain) - len(right) - 2)
        self.update(f"{left}{' ' * pad}{right}")


class UsagePanel(Static):
    """Personal usage table with model breakdown and sparkline."""

    DEFAULT_CSS = """
    UsagePanel {
        height: auto; min-height: 5; max-height: 8;
        background: #ffffff; color: #1a1a1a;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("[dim]Loading personal usage...[/]")
        self._days: list[DailyUsage] = []

    def set_data(self, days: list[DailyUsage]) -> None:
        self._days = days
        self._rebuild()

    def _rebuild(self) -> None:
        if not self._days:
            self.update("[dim]Loading personal usage...[/]")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        prev_week = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

        models = all_model_names(self._days)
        peak = max((d.total_cost for d in self._days[-30:]), default=0)

        # Split models by provider
        claude_models = [m for m in models if model_provider(m) == "claude"]
        codex_models = [m for m in models if model_provider(m) == "codex"]
        has_codex = bool(codex_models)

        # Header row: models... | Claude | Codex | TOTAL
        col_w = 13
        sub_w = 8  # subtotal column width
        header = "         "
        for m in models:
            header += f"[bold #00a4d6]{short_model(m):>{col_w}}[/]  "
        header += f"[dim]\u2502[/] [bold #e65100]{'Claude':>{sub_w}}[/]"
        if has_codex:
            header += f"  [bold #1b7a1b]{'Codex':>{sub_w}}[/]"
        header += f"  [bold #00a4d6]{'TOTAL':>{sub_w}}[/]"
        header += f"        [dim]\u2502[/] [bold]Daily $[/] [dim]\u2191{fmt_cost(peak)}[/]"

        # Data rows
        rows_cfg = [
            ("Today", today, None),
            ("7d", week_ago, prev_week),
            ("30d", month_ago, None),
        ]

        lines = [header]
        # Build 3-row sparkline (top/mid/bot) from the last 30 days
        costs_30 = [d.total_cost for d in self._days[-30:]]
        peak_s = max(costs_30) if costs_30 else 1
        if peak_s == 0:
            peak_s = 1
        heights = [c / peak_s * 3 for c in costs_30]

        def chart_row_str(row_base: int) -> str:
            chars = []
            for h in heights:
                portion = h - row_base
                if portion >= 1:
                    chars.append("\u2588")
                elif portion >= 0.875:
                    chars.append("\u2587")
                elif portion >= 0.75:
                    chars.append("\u2586")
                elif portion >= 0.625:
                    chars.append("\u2585")
                elif portion >= 0.5:
                    chars.append("\u2584")
                elif portion >= 0.375:
                    chars.append("\u2583")
                elif portion >= 0.25:
                    chars.append("\u2582")
                elif portion >= 0.125:
                    chars.append("\u2581")
                else:
                    chars.append(" ")
            return "".join(chars)

        chart_rows = [chart_row_str(2), chart_row_str(1), chart_row_str(0)]

        for idx, (label, since, prev_since) in enumerate(rows_cfg):
            agg_models, total_cost, total_tokens = aggregate_usage(self._days, since)

            # Delta vs previous period (fixed 6-char plain width: arrow + 4-digit pct + %)
            delta_str = "      "
            pct = None
            if prev_since:
                _, prev_cost, _ = aggregate_usage(
                    [d for d in self._days if d.date < since],
                    prev_since,
                )
                if prev_cost > 0:
                    pct = round((total_cost - prev_cost) / prev_cost * 100)
            else:
                if label == "Today":
                    _, yd_cost, _ = aggregate_usage(self._days, yesterday)
                    _, td_cost, _ = aggregate_usage(self._days, today)
                    yd_only = yd_cost - td_cost
                    if yd_only > 0:
                        pct = round((td_cost - yd_only) / yd_only * 100)
                elif label == "30d":
                    prev_30d = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
                    _, prev_cost, _ = aggregate_usage(
                        [d for d in self._days if d.date < month_ago],
                        prev_30d,
                    )
                    if prev_cost > 0:
                        pct = round((total_cost - prev_cost) / prev_cost * 100)
            if pct and pct != 0:
                arrow = "\u25b2" if pct > 0 else "\u25bc"
                delta_str = f"[dim]{arrow}{abs(pct):>4}%[/]"

            # Individual model columns
            row = f"  [bold #00a4d6]{label:<7}[/]"
            for m in models:
                mu = agg_models.get(m)
                if mu:
                    row += f"[dim]{fmt_tokens(mu.tokens):>7}[/] [bold]{fmt_cost(mu.cost):>5}[/]  "
                else:
                    row += f"[dim]{'\u2014':>13}[/]  "

            # Provider subtotals
            claude_cost = sum(agg_models[m].cost for m in claude_models if m in agg_models)
            codex_cost = sum(agg_models[m].cost for m in codex_models if m in agg_models)

            row += f"[dim]\u2502[/] [bold #e65100]{fmt_cost(claude_cost):>{sub_w}}[/]"
            if has_codex:
                row += f"  [bold #1b7a1b]{fmt_cost(codex_cost):>{sub_w}}[/]"
            row += f"  [bold]{fmt_cost(total_cost):>{sub_w}}[/]"
            row += delta_str
            # Chart
            chart = chart_rows[idx] if idx < len(chart_rows) else ""
            row += f"  [dim]\u2502[/] [#1b5e20]{chart}[/]"

            lines.append(row)

        self.update("\n".join(lines))


class HelpModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("question_mark", "dismiss", "Close")]
    DEFAULT_CSS = """
    HelpModal { align: center middle; }
    #help-box {
        width: 60; height: auto; max-height: 80%;
        background: white; border: round #1a1a1a;
        padding: 1 2; color: #212121;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label("[bold]Keyboard Shortcuts[/]\n")
            yield Label(
                "[bold]r[/]      Refresh all data\n"
                "[bold]?[/]      This help screen\n"
                "[bold]q[/]      Quit\n"
                "\n[bold #007a9e]Data Sources:[/]\n"
                "  Claude Code: ~/.claude/projects/**/*.jsonl\n"
                "  Codex: ~/.codex/sessions/**/*.jsonl\n"
                "  Pricing: LiteLLM (live fetch, offline fallback)\n"
                "\n[bold #007a9e]Tamagotchi Cat:[/]\n"
                "  The cat reacts to your daily spend.\n"
                "  Keep it sleeping. \U0001f63a"
            )


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class ClaudeUsageApp(App):
    TITLE = "claudeusagetui"

    CSS = """
    Screen { background: #ffffff; color: #1a1a1a; }
    ModalScreen { background: rgba(0, 0, 0, 0.4); }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("question_mark", "help", "?:Help"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(USAGE_THEME)
        self.theme = "claudeusage"
        self._days: list[DailyUsage] = []
        self._loading = False

    def compose(self) -> ComposeResult:
        yield HeaderBar()
        yield UsagePanel()
        yield Footer()

    def on_mount(self) -> None:
        self._trigger_refresh()
        self.set_interval(60.0, self._trigger_refresh)

    def _trigger_refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        Thread(target=self._fetch_all, daemon=True).start()

    def _fetch_all(self) -> None:
        try:
            days = fetch_usage(since_days=60)
            if days:
                self._days = days
            self.app.call_from_thread(self._update_personal)
        except Exception:
            log.exception("Fetch error")
        finally:
            self._loading = False

    def _update_personal(self) -> None:
        panel = self.query_one(UsagePanel)
        panel.set_data(self._days)

        header = self.query_one(HeaderBar)
        today = datetime.now().strftime("%Y-%m-%d")
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        _, today_cost, _ = aggregate_usage(self._days, today)
        _, cost_30d, _ = aggregate_usage(self._days, month_ago)
        header.set_costs(today_cost, cost_30d)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_refresh(self) -> None:
        self._trigger_refresh()
        self.notify("Refreshing...", timeout=2)


def main() -> None:
    log_dir = Path.home() / ".claudeusagetui"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "claudeusagetui.log"),
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    ClaudeUsageApp().run()


if __name__ == "__main__":
    main()
