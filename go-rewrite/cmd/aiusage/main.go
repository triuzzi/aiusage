package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/triuzzi/aiusage/internal/data"
	"github.com/triuzzi/aiusage/internal/tui"
)

func init() {
	dir := filepath.Join(os.Getenv("HOME"), ".aiusage")
	os.MkdirAll(dir, 0o755)
	f, err := os.OpenFile(filepath.Join(dir, "aiusage.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err == nil {
		log.SetOutput(f)
	}
}

const usageText = `Usage: aiusage [command]

Commands:
  (none)              Launch the TUI dashboard
  today               Today's cost (plain number)
  daily [--since N]   Daily breakdown as JSON (default: 30 days)
  models [--since N]  Model breakdown as JSON with provider subtotals
  help, -h            Show this help text`

func main() {
	args := os.Args[1:]

	if len(args) == 0 {
		launchTUI()
		return
	}

	switch args[0] {
	case "today":
		cmdToday()
	case "daily":
		cmdDaily(parseSince(args[1:], 30))
	case "models":
		cmdModels(parseSince(args[1:], 30))
	case "help", "-h", "--help":
		fmt.Println(usageText)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n", args[0])
		fmt.Fprintln(os.Stderr, usageText)
		os.Exit(1)
	}
}

func launchTUI() {
	m := tui.NewModel()
	p := tea.NewProgram(m, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "Error running TUI: %v\n", err)
		os.Exit(1)
	}
}

func parseSince(args []string, defaultVal int) int {
	for i, a := range args {
		if a == "--since" && i+1 < len(args) {
			n, err := strconv.Atoi(args[i+1])
			if err == nil {
				return n
			}
		}
	}
	return defaultVal
}

func cmdToday() {
	days := data.FetchUsage(1)
	today := time.Now().Format("2006-01-02")
	_, cost, _ := data.AggregateUsage(days, today)
	fmt.Printf("%.2f\n", cost)
}

func cmdDaily(sinceDays int) {
	days := data.FetchUsage(sinceDays)
	out := make([]map[string]interface{}, 0, len(days))
	for _, d := range days {
		models := make(map[string]interface{})
		for name, mu := range d.Models {
			models[name] = map[string]interface{}{
				"tokens": mu.Tokens,
				"cost":   roundTo(mu.Cost, 2),
			}
		}
		out = append(out, map[string]interface{}{
			"date":         d.Date,
			"total_tokens": d.TotalTokens,
			"total_cost":   roundTo(d.TotalCost, 2),
			"models":       models,
		})
	}
	b, _ := json.MarshalIndent(out, "", "  ")
	fmt.Println(string(b))
}

func cmdModels(sinceDays int) {
	days := data.FetchUsage(sinceDays)
	since := time.Now().AddDate(0, 0, -sinceDays).Format("2006-01-02")
	agg, totalCost, totalTokens := data.AggregateUsage(days, since)

	models := make(map[string]interface{})
	for _, name := range data.AllModelNames(days) {
		mu, ok := agg[name]
		if !ok {
			continue
		}
		models[name] = map[string]interface{}{
			"provider": data.ModelProvider(name),
			"tokens":   mu.Tokens,
			"cost":     roundTo(mu.Cost, 2),
		}
	}

	providers := make(map[string]map[string]interface{})
	for name, info := range models {
		infoMap := info.(map[string]interface{})
		p := infoMap["provider"].(string)
		if _, ok := providers[p]; !ok {
			providers[p] = map[string]interface{}{"tokens": 0, "cost": 0.0}
		}
		providers[p]["tokens"] = providers[p]["tokens"].(int) + agg[name].Tokens
		providers[p]["cost"] = roundTo(providers[p]["cost"].(float64)+agg[name].Cost, 2)
	}

	result := map[string]interface{}{
		"since":        since,
		"total_tokens": totalTokens,
		"total_cost":   roundTo(totalCost, 2),
		"providers":    providers,
		"models":       models,
	}
	b, _ := json.MarshalIndent(result, "", "  ")
	fmt.Println(string(b))
}

func roundTo(val float64, places int) float64 {
	pow := 1.0
	for i := 0; i < places; i++ {
		pow *= 10
	}
	return float64(int(val*pow+0.5)) / pow
}
