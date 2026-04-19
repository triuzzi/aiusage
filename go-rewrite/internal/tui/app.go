package tui

import (
	"fmt"
	"math"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/key"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/mattn/go-runewidth"

	"github.com/triuzzi/aiusage/internal/data"
)

// ---------------------------------------------------------------------------
// Tamagotchi cat animation frames
// ---------------------------------------------------------------------------

var catFrames = map[string][]string{
	"sleeping": {
		"~(\u02d8\u02d8 )  z    ", "~(\u02d8\u02d8 )  zZ   ", "~(\u02d8\u02d8 )  zzZ  ",
		"~(\u02d8\u02d8 ) zzZZZ ", "~(\u02d8\u02d8 )  zzZ  ", "~(\u02d8\u02d8 )  zZ   ",
		"~(\u02d8\u02d8 )  z    ", "~(\u02d8\u02d8 )       ",
	},
	"waking": {
		"~(\u02d8\u02d8 )       ", " (\u1d54_\u1d54)      ", " (\u1d54o\u1d54)      ",
		" (\u1d54O\u1d54)  ~   ", " (\u1d54O\u1d54) ~~   ", " (\u1d54o\u1d54)  ~   ",
		" (\u1d54_\u1d54)/     ", " (\u1d54\u1d55\u1d54)      ",
	},
	"playful": {
		" (\u1d54\u1d55\u1d54)  o   ", " (\u1d54\u1d55\u1d54)/ o   ", " (\u1d54\u1d55\u1d54)/o    ",
		"  o(\u1d54\u1d55\u1d54)    ", " o (\u1d54\u1d55\u1d54)    ", " (\u1d54\u1d55\u1d54)o     ",
		" (\u1d54\u1d55\u1d54) o    ", " (\u1d54\u1d55\u1d54)  o   ",
	},
	"satisfied": {
		" (=^.^=)     ", " (=^.^=)  ~  ", " (=^.^=) \u2661   ", " (=^.^=)  \u2661  ",
		" (=^.^=)   \u2661 ", " (=^.^=)     ", " (=^.^=) \u2661 \u2661 ", " (=^.^=)  ~  ",
	},
	"nervous": {
		" (\u1d54_\u1d54;)  $  ", " (\u1d54_\u1d54;) $   ", " (\u1d54_\u1d54;)$    ",
		" (\u1d54_\u1d54;)     ", " (\u1d54_\u1d54;) ..  ", " (\u1d54_\u1d54;)...  ",
		" (\u1d54_\u1d54;) ..  ", " (\u1d54_\u1d54;)  .  ",
	},
	"alarm": {
		" (>_<) !!!   ", " (>_<)  *!*  ", " (>_<) *!!!* ", " (>_<)  *!*  ",
		" (>_<) !!!   ", "  (>_<) !!   ", " (>_<) *!!!* ", " (>_<)  !!!  ",
	},
	"nuclear": {
		" (X_X) ~*!*~ ", "*(X_X)*!$!$!*", " ~(x_x)~ $$$ ", "*!(@_@)*!*! ",
		"~$(X_X)$~!!! ", "*!(@_@;)$$$!*", "~*~(X_X)~*~! ", "*$*!(@_@)!*$*",
	},
}

// catTiers maps spend thresholds to animation tiers (sorted ascending).
var catTiers = []struct {
	threshold float64
	name      string
}{
	{0, "sleeping"},
	{50, "waking"},
	{100, "playful"},
	{200, "satisfied"},
	{350, "nervous"},
	{500, "alarm"},
	{700, "nuclear"},
}

func pickCatTier(dailyCost float64) string {
	tier := "sleeping"
	for _, t := range catTiers {
		if dailyCost >= t.threshold {
			tier = t.name
		}
	}
	return tier
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

var (
	headerStyle = lipgloss.NewStyle().
			Background(lipgloss.Color("#00a4d6")).
			Foreground(lipgloss.Color("#ffffff")).
			PaddingLeft(1).
			PaddingRight(1)

	panelBg = lipgloss.NewStyle().
		Background(lipgloss.Color("#ffffff")).
		Foreground(lipgloss.Color("#1a1a1a")).
		PaddingLeft(1).
		PaddingRight(1)

	helpBoxStyle = lipgloss.NewStyle().
			Background(lipgloss.Color("#ffffff")).
			Foreground(lipgloss.Color("#212121")).
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#1a1a1a")).
			Padding(1, 2).
			Width(60)

	cyanBold = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#00a4d6")).
			Bold(true)

	orangeBold = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#e65100")).
			Bold(true)

	greenBold = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#1b7a1b")).
			Bold(true)

	dimStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#999999"))

	boldStyle = lipgloss.NewStyle().
			Bold(true)

	chartStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("#1b5e20"))
)

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

type tickMsg time.Time
type catTickMsg time.Time

type usageDataMsg struct {
	days []data.DailyUsage
}

// ---------------------------------------------------------------------------
// Key bindings
// ---------------------------------------------------------------------------

type keyMap struct {
	Quit    key.Binding
	Refresh key.Binding
	Help    key.Binding
}

var keys = keyMap{
	Quit: key.NewBinding(
		key.WithKeys("q", "ctrl+c"),
		key.WithHelp("q", "quit"),
	),
	Refresh: key.NewBinding(
		key.WithKeys("r"),
		key.WithHelp("r", "refresh"),
	),
	Help: key.NewBinding(
		key.WithKeys("?"),
		key.WithHelp("?", "help"),
	),
}

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

// Model is the main Bubble Tea model for the TUI.
type Model struct {
	days      []data.DailyUsage
	width     int
	height    int
	frameIdx  int
	dailyCost float64
	cost30d   float64
	loading   bool
	showHelp  bool
	hostname  string
}

// NewModel creates a new TUI model.
func NewModel() Model {
	host, _ := os.Hostname()
	// Strip domain suffix (like Python's platform.node().split(".")[0])
	if idx := strings.Index(host, "."); idx != -1 {
		host = host[:idx]
	}
	return Model{
		hostname: host,
		loading:  true,
	}
}

// Init starts the initial data fetch and tick timers.
func (m Model) Init() tea.Cmd {
	return tea.Batch(
		fetchUsageCmd(),
		tickEvery(time.Second, func(t time.Time) tea.Msg { return catTickMsg(t) }),
		tickEvery(60*time.Second, func(t time.Time) tea.Msg { return tickMsg(t) }),
	)
}

func tickEvery(d time.Duration, fn func(time.Time) tea.Msg) tea.Cmd {
	return tea.Every(d, fn)
}

func fetchUsageCmd() tea.Cmd {
	return func() tea.Msg {
		days := data.FetchUsage(60)
		return usageDataMsg{days: days}
	}
}

// Update handles messages.
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case tea.KeyMsg:
		if m.showHelp {
			// Any key dismisses help
			m.showHelp = false
			return m, nil
		}
		switch {
		case key.Matches(msg, keys.Quit):
			return m, tea.Quit
		case key.Matches(msg, keys.Refresh):
			if !m.loading {
				m.loading = true
				return m, fetchUsageCmd()
			}
		case key.Matches(msg, keys.Help):
			m.showHelp = true
		}
		return m, nil

	case catTickMsg:
		m.frameIdx++
		return m, tickEvery(time.Second, func(t time.Time) tea.Msg { return catTickMsg(t) })

	case tickMsg:
		if !m.loading {
			m.loading = true
			return m, tea.Batch(
				fetchUsageCmd(),
				tickEvery(60*time.Second, func(t time.Time) tea.Msg { return tickMsg(t) }),
			)
		}
		return m, tickEvery(60*time.Second, func(t time.Time) tea.Msg { return tickMsg(t) })

	case usageDataMsg:
		m.days = msg.days
		m.loading = false
		m.updateCosts()
		return m, nil
	}

	return m, nil
}

func (m *Model) updateCosts() {
	today := time.Now().Format("2006-01-02")
	monthAgo := time.Now().AddDate(0, 0, -30).Format("2006-01-02")
	_, m.dailyCost, _ = data.AggregateUsage(m.days, today)
	_, m.cost30d, _ = data.AggregateUsage(m.days, monthAgo)
}

// View renders the full TUI.
func (m Model) View() string {
	if m.width == 0 {
		return ""
	}

	var sections []string
	sections = append(sections, m.renderHeader())
	sections = append(sections, m.renderUsagePanel())

	view := strings.Join(sections, "\n")

	if m.showHelp {
		view = m.renderHelpOverlay(view)
	}

	return view
}

// ---------------------------------------------------------------------------
// Header bar
// ---------------------------------------------------------------------------

func (m Model) renderHeader() string {
	tier := pickCatTier(m.dailyCost)
	frames := catFrames[tier]
	cat := frames[m.frameIdx%len(frames)]

	now := time.Now().Format("15:04:05")

	// Pad cat frame to fixed width so the rest of the header doesn't shift
	catW := runewidth.StringWidth(cat)
	const catColW = 14 // widest frame is 13-14 cells
	if catW < catColW {
		cat = cat + strings.Repeat(" ", catColW-catW)
	}

	left := fmt.Sprintf("%s  AI Usage  $%d today \u00b7 $%d/30d",
		cat, int(m.dailyCost+0.5), int(m.cost30d+0.5))
	right := fmt.Sprintf("%s  %s", m.hostname, now)

	leftW := runewidth.StringWidth(left)
	rightW := runewidth.StringWidth(right)
	// Account for 2 chars of horizontal padding (1 left + 1 right)
	innerWidth := m.width - 2
	pad := innerWidth - leftW - rightW
	if pad < 1 {
		pad = 1
	}

	line := left + strings.Repeat(" ", pad) + right
	return headerStyle.Width(m.width).Render(line)
}

// ---------------------------------------------------------------------------
// Usage panel
// ---------------------------------------------------------------------------

func (m Model) renderUsagePanel() string {
	if len(m.days) == 0 {
		loading := dimStyle.Render("Loading personal usage...")
		return panelBg.Width(m.width).Render(loading)
	}

	today := time.Now().Format("2006-01-02")
	yesterday := time.Now().AddDate(0, 0, -1).Format("2006-01-02")
	weekAgo := time.Now().AddDate(0, 0, -7).Format("2006-01-02")
	monthAgo := time.Now().AddDate(0, 0, -30).Format("2006-01-02")
	prevWeek := time.Now().AddDate(0, 0, -14).Format("2006-01-02")

	models := data.AllModelNames(m.days)

	// Determine peak cost for sparkline scaling
	start30 := len(m.days) - 30
	if start30 < 0 {
		start30 = 0
	}
	lastDays := m.days[start30:]
	peak := 0.0
	for _, d := range lastDays {
		if d.TotalCost > peak {
			peak = d.TotalCost
		}
	}

	// Split models by provider
	var claudeModels, codexModels []string
	for _, mn := range models {
		if data.ModelProvider(mn) == "claude" {
			claudeModels = append(claudeModels, mn)
		} else {
			codexModels = append(codexModels, mn)
		}
	}
	hasCodex := len(codexModels) > 0

	colW := 13
	subW := 8

	// Header row
	hdr := "         " // 9 spaces for label column
	for _, mn := range models {
		short := data.ShortModel(mn)
		hdr += cyanBold.Render(padLeft(short, colW)) + "  "
	}
	hdr += dimStyle.Render("\u2502") + " " + orangeBold.Render(padLeft("Claude", subW))
	if hasCodex {
		hdr += "  " + greenBold.Render(padLeft("Codex", subW))
	}
	hdr += "  " + cyanBold.Render(padLeft("TOTAL", subW))
	hdr += "        " + dimStyle.Render("\u2502") + " " + boldStyle.Render("Daily $") + " " + dimStyle.Render("\u2191"+data.FmtCost(peak))

	// Build 3-row sparkline
	costs30 := make([]float64, len(lastDays))
	for i, d := range lastDays {
		costs30[i] = d.TotalCost
	}
	peakS := peak
	if peakS == 0 {
		peakS = 1
	}
	heights := make([]float64, len(costs30))
	for i, c := range costs30 {
		heights[i] = c / peakS * 3
	}
	chartRows := [3]string{
		chartRowStr(heights, 2),
		chartRowStr(heights, 1),
		chartRowStr(heights, 0),
	}

	// Data rows
	type rowCfg struct {
		label     string
		since     string
		prevSince string
	}
	rowsCfg := []rowCfg{
		{"Today", today, ""},
		{"7d", weekAgo, prevWeek},
		{"30d", monthAgo, ""},
	}

	lines := []string{hdr}
	for idx, rc := range rowsCfg {
		aggModels, totalCost, _ := data.AggregateUsage(m.days, rc.since)

		// Delta calculation
		deltaStr := "      "
		var pct *int
		if rc.prevSince != "" {
			// Filter days before since
			var filtered []data.DailyUsage
			for _, d := range m.days {
				if d.Date < rc.since {
					filtered = append(filtered, d)
				}
			}
			_, prevCost, _ := data.AggregateUsage(filtered, rc.prevSince)
			if prevCost > 0 {
				p := int(math.Round((totalCost - prevCost) / prevCost * 100))
				pct = &p
			}
		} else if rc.label == "Today" {
			_, ydCost, _ := data.AggregateUsage(m.days, yesterday)
			_, tdCost, _ := data.AggregateUsage(m.days, today)
			ydOnly := ydCost - tdCost
			if ydOnly > 0 {
				p := int(math.Round((tdCost - ydOnly) / ydOnly * 100))
				pct = &p
			}
		} else if rc.label == "30d" {
			prev30d := time.Now().AddDate(0, 0, -60).Format("2006-01-02")
			var filtered []data.DailyUsage
			for _, d := range m.days {
				if d.Date < monthAgo {
					filtered = append(filtered, d)
				}
			}
			_, prevCost, _ := data.AggregateUsage(filtered, prev30d)
			if prevCost > 0 {
				p := int(math.Round((totalCost - prevCost) / prevCost * 100))
				pct = &p
			}
		}
		if pct != nil && *pct != 0 {
			arrow := "\u25b2" // ▲
			if *pct < 0 {
				arrow = "\u25bc" // ▼
			}
			abs := *pct
			if abs < 0 {
				abs = -abs
			}
			deltaStr = dimStyle.Render(fmt.Sprintf("%s%4d%%", arrow, abs))
		}

		// Build row: label + model columns
		row := "  " + cyanBold.Render(padRight(rc.label, 7))
		for _, mn := range models {
			mu, ok := aggModels[mn]
			if ok && mu.Tokens > 0 {
				row += dimStyle.Render(padLeft(data.FmtTokens(mu.Tokens), 7)) + " " + boldStyle.Render(padLeft(data.FmtCost(mu.Cost), 5)) + "  "
			} else {
				row += dimStyle.Render(padLeft("\u2014", colW)) + "  "
			}
		}

		// Provider subtotals
		claudeCost := 0.0
		for _, mn := range claudeModels {
			if mu, ok := aggModels[mn]; ok {
				claudeCost += mu.Cost
			}
		}
		codexCost := 0.0
		for _, mn := range codexModels {
			if mu, ok := aggModels[mn]; ok {
				codexCost += mu.Cost
			}
		}

		row += dimStyle.Render("\u2502") + " " + orangeBold.Render(padLeft(data.FmtCost(claudeCost), subW))
		if hasCodex {
			row += "  " + greenBold.Render(padLeft(data.FmtCost(codexCost), subW))
		}
		row += "  " + boldStyle.Render(padLeft(data.FmtCost(totalCost), subW))
		row += deltaStr

		// Sparkline chart
		chart := ""
		if idx < len(chartRows) {
			chart = chartRows[idx]
		}
		row += "  " + dimStyle.Render("\u2502") + " " + chartStyle.Render(chart)

		lines = append(lines, row)
	}

	content := strings.Join(lines, "\n")
	return panelBg.Width(m.width).Render(content)
}

// chartRowStr builds one row of the 3-tier block sparkline.
func chartRowStr(heights []float64, rowBase int) string {
	blocks := []rune{' ', '\u2581', '\u2582', '\u2583', '\u2584', '\u2585', '\u2586', '\u2587', '\u2588'}
	var sb strings.Builder
	for _, h := range heights {
		portion := h - float64(rowBase)
		switch {
		case portion >= 1:
			sb.WriteRune(blocks[8]) // █
		case portion >= 0.875:
			sb.WriteRune(blocks[7]) // ▇
		case portion >= 0.75:
			sb.WriteRune(blocks[6]) // ▆
		case portion >= 0.625:
			sb.WriteRune(blocks[5]) // ▅
		case portion >= 0.5:
			sb.WriteRune(blocks[4]) // ▄
		case portion >= 0.375:
			sb.WriteRune(blocks[3]) // ▃
		case portion >= 0.25:
			sb.WriteRune(blocks[2]) // ▂
		case portion >= 0.125:
			sb.WriteRune(blocks[1]) // ▁
		default:
			sb.WriteRune(blocks[0]) // space
		}
	}
	return sb.String()
}

// ---------------------------------------------------------------------------
// Help modal
// ---------------------------------------------------------------------------

func (m Model) renderHelpOverlay(base string) string {
	help := strings.Join([]string{
		boldStyle.Render("Keyboard Shortcuts"),
		"",
		boldStyle.Render("r") + "      Refresh all data",
		boldStyle.Render("?") + "      This help screen",
		boldStyle.Render("q") + "      Quit",
		"",
		lipgloss.NewStyle().Foreground(lipgloss.Color("#007a9e")).Bold(true).Render("Data Sources:"),
		"  Personal: ~/.claude/projects + ~/.codex/sessions",
		"  Pricing: LiteLLM (live fetch, offline fallback)",
		"",
		lipgloss.NewStyle().Foreground(lipgloss.Color("#007a9e")).Bold(true).Render("Tamagotchi Cat:"),
		"  The cat reacts to your daily spend.",
		"  Keep it sleeping. \U0001f63a",
	}, "\n")

	box := helpBoxStyle.Render(help)

	// Center the box on screen
	boxW := lipgloss.Width(box)
	boxH := lipgloss.Height(box)

	// Build base lines and overlay
	baseLines := strings.Split(base, "\n")
	// Ensure we have enough lines
	for len(baseLines) < m.height {
		baseLines = append(baseLines, "")
	}

	startY := (m.height - boxH) / 2
	startX := (m.width - boxW) / 2
	if startY < 0 {
		startY = 0
	}
	if startX < 0 {
		startX = 0
	}

	boxLines := strings.Split(box, "\n")
	for i, bl := range boxLines {
		y := startY + i
		if y >= len(baseLines) {
			break
		}
		// Replace the line region with the box line
		line := baseLines[y]
		lineW := runewidth.StringWidth(stripAnsi(line))
		// Pad line if needed
		if lineW < startX+lipgloss.Width(bl) {
			line += strings.Repeat(" ", startX+lipgloss.Width(bl)-lineW)
		}
		baseLines[y] = padToWidth(startX) + bl
	}

	return strings.Join(baseLines[:m.height], "\n")
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

// padLeft right-aligns s within a field of width w.
func padLeft(s string, w int) string {
	sw := runewidth.StringWidth(s)
	if sw >= w {
		return s
	}
	return strings.Repeat(" ", w-sw) + s
}

// padRight left-aligns s within a field of width w.
func padRight(s string, w int) string {
	sw := runewidth.StringWidth(s)
	if sw >= w {
		return s
	}
	return s + strings.Repeat(" ", w-sw)
}

// padToWidth returns spaces of the given width.
func padToWidth(w int) string {
	if w <= 0 {
		return ""
	}
	return strings.Repeat(" ", w)
}

// stripAnsi removes ANSI escape sequences for width calculation.
func stripAnsi(s string) string {
	var sb strings.Builder
	inEsc := false
	for _, r := range s {
		if r == '\x1b' {
			inEsc = true
			continue
		}
		if inEsc {
			if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
				inEsc = false
			}
			continue
		}
		sb.WriteRune(r)
	}
	return sb.String()
}
