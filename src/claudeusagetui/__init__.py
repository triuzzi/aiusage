from __future__ import annotations

import logging
import platform
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Footer, Label, Rule, Static

from .data import (
    DailyUsage,
    PlatformClient,
    TeamMember,
    aggregate_usage,
    all_model_names,
    fetch_ccusage,
    fmt_cost,
    fmt_tokens,
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

# Spend thresholds → animation tier
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
        self._status = ""

    def set_costs(self, daily: float, cost_30d: float, status: str = "") -> None:
        self._daily_cost = daily
        self._cost_30d = cost_30d
        self._status = status

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
        status = f"  [{self._status}]" if self._status else ""

        left = f"[bold]{cat}[/]  Claude Usage  ${round(self._daily_cost)} today \u00b7 ${round(self._cost_30d)}/30d{status}"
        left_plain = f"{cat}  Claude Usage  ${round(self._daily_cost)} today \u00b7 ${round(self._cost_30d)}/30d{status}"
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
            self.update("[dim]Loading personal usage from ccusage...[/]")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        prev_week = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

        models = all_model_names(self._days)
        spark = sparkline(self._days, 30)
        peak = max((d.total_cost for d in self._days[-30:]), default=0)

        # Header row
        col_w = 13
        header = "         "
        for m in models:
            header += f"[bold #00a4d6]{short_model(m):>{col_w}}[/]  "
        header += f"[bold #00a4d6]{'TOTAL':>{col_w}}[/]"
        header += f"         [dim]\u2502[/] [bold]Daily $[/] [dim]\u2191{fmt_cost(peak)}[/]"

        # Data rows
        rows_cfg = [
            ("Today", today, None),
            ("7d", week_ago, prev_week),
            ("30d", month_ago, None),
        ]

        lines = [header]
        chart_row = 0
        # Build 3-row sparkline (top/mid/bot) from the last 30 days
        costs_30 = [d.total_cost for d in self._days[-30:]]
        peak_s = max(costs_30) if costs_30 else 1
        if peak_s == 0:
            peak_s = 1
        heights = [c / peak_s * 3 for c in costs_30]
        chart_blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"

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

            # Delta vs previous period
            delta_str = "       "
            if prev_since:
                _, prev_cost, _ = aggregate_usage(
                    [d for d in self._days if d.date < since],
                    prev_since,
                )
                if prev_cost > 0:
                    pct = round((total_cost - prev_cost) / prev_cost * 100)
                    if pct != 0:
                        arrow = "\u25b2" if pct > 0 else "\u25bc"
                        delta_str = f"[dim] {arrow}{abs(pct):>4}%[/]"
            else:
                # Today vs yesterday
                if label == "Today":
                    _, yd_cost, _ = aggregate_usage(self._days, yesterday)
                    # yd_cost includes today, we need just yesterday
                    _, td_cost, _ = aggregate_usage(self._days, today)
                    yd_only = yd_cost - td_cost
                    if yd_only > 0:
                        pct = round((td_cost - yd_only) / yd_only * 100)
                        if pct != 0:
                            arrow = "\u25b2" if pct > 0 else "\u25bc"
                            delta_str = f"[dim] {arrow}{abs(pct):>4}%[/]"
                elif label == "30d":
                    # 30d: compare with previous 30d
                    prev_30d = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
                    _, prev_cost, _ = aggregate_usage(
                        [d for d in self._days if d.date < month_ago],
                        prev_30d,
                    )
                    if prev_cost > 0:
                        pct = round((total_cost - prev_cost) / prev_cost * 100)
                        if pct != 0:
                            arrow = "\u25b2" if pct > 0 else "\u25bc"
                            delta_str = f"[dim] {arrow}{abs(pct):>4}%[/]"

            row = f"  [bold #00a4d6]{label:<7}[/]"
            for m in models:
                mu = agg_models.get(m)
                if mu:
                    row += f"[dim]{fmt_tokens(mu.tokens):>7}[/] [bold]{fmt_cost(mu.cost):>5}[/]  "
                else:
                    row += f"[dim]{'\u2014':>13}[/]  "
            # Total column
            row += f"[dim]{fmt_tokens(total_tokens):>7}[/] [bold]{fmt_cost(total_cost):>5}[/]"
            row += delta_str
            # Chart
            chart = chart_rows[idx] if idx < len(chart_rows) else ""
            row += f"  [dim]\u2502[/] [#1b5e20]{chart}[/]"

            lines.append(row)

        self.update("\n".join(lines))


class RaceTrack(Static):
    """Team racing scoreboard."""

    DEFAULT_CSS = """
    RaceTrack {
        height: 1fr; min-height: 8;
        background: #ffffff; color: #1a1a1a;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("[dim]Loading team scoreboard...[/]")
        self._members: list[TeamMember] = []
        self._error: str | None = None
        self._sort = "spend"  # spend or lines
        self._period_label = ""

    def set_data(self, members: list[TeamMember], error: str | None = None, period: str = "") -> None:
        self._members = members
        self._error = error
        self._period_label = period
        self._rebuild()

    def set_sort(self, sort: str) -> None:
        self._sort = sort
        self._rebuild()

    def _rebuild(self) -> None:
        if self._error and not self._members:
            self.update(
                f"\n[dim]\u2500\u2500 TEAM RACING \u2500\u2500[/]\n\n"
                f"  [#b71c1c]{self._error}[/]\n\n"
                f"  [dim]Open platform.claude.com in Brave to enable the team scoreboard.[/]"
            )
            return

        if not self._members:
            self.update("\n[dim]Loading team data from platform.claude.com...[/]")
            return

        # Sort
        if self._sort == "lines":
            members = sorted(self._members, key=lambda m: -m.lines_accepted)
        else:
            members = sorted(self._members, key=lambda m: -m.spend)

        max_val = members[0].spend if members else 1
        if max_val <= 0:
            max_val = 1

        # Available width for the track bar
        avail = self.size.width - 2  # padding
        name_w = min(22, max(len(m.email.split("@")[0]) for m in members[:20]) + 1) if members else 15
        rank_w = 4
        cost_w = 10
        car_w = 2
        track_w = max(10, avail - name_w - rank_w - cost_w - car_w - 6)

        # Medal and color config
        medals = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze
        bar_colors = ["#FF8C00", "#888888", "#B8860B"]
        name_colors = ["bold #FF8C00", "#666666", "#8B6914"]

        now = datetime.now().strftime("%B %Y")
        period = self._period_label or now
        sort_label = "spend" if self._sort == "spend" else "lines"

        lines: list[str] = []
        finish = "\u2503"
        lines.append("")
        title = f"\U0001f3c1 TEAM RACING \u2014 {period} (by {sort_label})"
        pad = max(0, avail - len(title) - 12)
        lines.append(f"[bold]{title}[/]{' ' * pad}[dim]FINISH {finish}[/]")
        lines.append("")

        # Show up to terminal height rows
        max_rows = max(3, self.size.height - 5) if self.size.height > 0 else 20
        visible = members[:max_rows]

        for i, m in enumerate(visible):
            # Rank
            if i < 3:
                rank = f" {medals[i]}"
                bar_color = bar_colors[i]
                name_style = name_colors[i]
            else:
                rank = f" {i + 1:>2}"
                bar_color = "#cccccc"
                name_style = "#999999"

            # Name (before @)
            name = m.email.split("@")[0]
            if len(name) > name_w:
                name = name[: name_w - 1] + "\u2026"

            # Bar width
            val = m.spend if self._sort == "spend" else m.lines_accepted
            bar_len = max(1, int(val / max_val * track_w)) if max_val > 0 else 1
            bar = "\u2588" * bar_len

            # Cost label
            if self._sort == "spend":
                cost_label = f"${m.spend:,.0f}"
            else:
                cost_label = f"{m.lines_accepted:,}L"

            # Build line with car emoji
            car = "\U0001f3ce"  # racing car
            line = (
                f"[{name_style}]{rank} {name:<{name_w}}[/]"
                f"[{bar_color}]{bar}[/]{car}"
                f"  [{name_style}]{cost_label:>{cost_w}}[/]"
            )
            lines.append(line)

        if len(members) > max_rows:
            lines.append(f"  [dim]... and {len(members) - max_rows} more[/]")

        lines.append("")
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
                "[bold]s[/]      Cycle sort (spend / lines)\n"
                "[bold]p[/]      Cycle period (month / 7d / 30d)\n"
                "[bold]?[/]      This help screen\n"
                "[bold]q[/]      Quit\n"
                "\n[bold #007a9e]Data Sources:[/]\n"
                "  Personal usage: ccusage CLI (local JSONL)\n"
                "  Team racing: platform.claude.com API\n"
                "  (auth via Brave sessionKey cookie)\n"
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
    Rule { color: #e0e0e0; margin: 0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("question_mark", "help", "?:Help"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("p", "cycle_period", "Period"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(USAGE_THEME)
        self.theme = "claudeusage"
        self._days: list[DailyUsage] = []
        self._team: list[TeamMember] = []
        self._platform = PlatformClient()
        self._loading = False
        self._sort_mode = "spend"
        self._period_mode = "month"  # month, 7d, 30d
        self._team_error: str | None = None

    def compose(self) -> ComposeResult:
        yield HeaderBar()
        yield UsagePanel()
        yield Rule()
        yield RaceTrack()
        yield Footer()

    def on_mount(self) -> None:
        self._trigger_refresh()
        self.set_interval(60.0, self._trigger_refresh)

    def _trigger_refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        Thread(target=self._fetch_all, daemon=True).start()

    def _period_dates(self) -> tuple[str, str, str]:
        """Returns (start_date, end_date, label) for current period mode."""
        today = datetime.now()
        end = today.strftime("%Y-%m-%d")
        if self._period_mode == "7d":
            start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            label = f"Last 7 days"
        elif self._period_mode == "30d":
            start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            label = f"Last 30 days"
        else:
            start = today.replace(day=1).strftime("%Y-%m-%d")
            label = today.strftime("%B %Y")
        return start, end, label

    def _fetch_all(self) -> None:
        try:
            # Personal usage
            days = fetch_ccusage(since_days=60)
            if days:
                self._days = days

            # Update personal panel
            self.app.call_from_thread(self._update_personal)

            # Team usage
            if self._platform.init():
                start, end, label = self._period_dates()
                team = self._platform.fetch_team(start, end)
                if team:
                    self._team = team
                    self._team_error = None
                else:
                    self._team_error = self._platform.error
            else:
                self._team_error = self._platform.error

            self.app.call_from_thread(self._update_team)
        except Exception:
            log.exception("Fetch error")
        finally:
            self._loading = False

    def _update_personal(self) -> None:
        panel = self.query_one(UsagePanel)
        panel.set_data(self._days)

        # Update header costs
        header = self.query_one(HeaderBar)
        today = datetime.now().strftime("%Y-%m-%d")
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        _, today_cost, _ = aggregate_usage(self._days, today)
        _, cost_30d, _ = aggregate_usage(self._days, month_ago)
        status = "connected" if self._platform.connected else ""
        header.set_costs(today_cost, cost_30d, status)

    def _update_team(self) -> None:
        track = self.query_one(RaceTrack)
        _, _, label = self._period_dates()
        track.set_data(self._team, self._team_error, label)

    def on_resize(self) -> None:
        self._update_team()

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_refresh(self) -> None:
        self._platform._initialized = False  # force re-auth
        self._trigger_refresh()
        self.notify("Refreshing...", timeout=2)

    def action_cycle_sort(self) -> None:
        modes = ["spend", "lines"]
        self._sort_mode = modes[(modes.index(self._sort_mode) + 1) % len(modes)]
        track = self.query_one(RaceTrack)
        track.set_sort(self._sort_mode)
        self.notify(f"Sort: {self._sort_mode}", timeout=2)

    def action_cycle_period(self) -> None:
        modes = ["month", "7d", "30d"]
        self._period_mode = modes[(modes.index(self._period_mode) + 1) % len(modes)]
        self.notify(f"Period: {self._period_mode}", timeout=2)
        self._trigger_refresh()


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
