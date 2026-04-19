package data

import (
	"fmt"
	"math"
	"sort"
	"time"
)

// FetchUsage reads usage from both Claude Code and Codex, merged by date.
func FetchUsage(sinceDays int) []DailyUsage {
	since := time.Now().AddDate(0, 0, -sinceDays).Format("2006-01-02")

	claude := ReadClaudeUsage(since)
	codex := ReadCodexUsage(since)

	// Merge into unified dict
	allDates := make(map[string]map[string]*ModelUsage)
	for _, source := range []map[string]map[string]*ModelUsage{claude, codex} {
		for date, models := range source {
			day, ok := allDates[date]
			if !ok {
				day = make(map[string]*ModelUsage)
				allDates[date] = day
			}
			for name, mu := range models {
				if existing, ok := day[name]; ok {
					existing.Tokens += mu.Tokens
					existing.Cost += mu.Cost
				} else {
					day[name] = &ModelUsage{Name: name, Tokens: mu.Tokens, Cost: mu.Cost}
				}
			}
		}
	}

	// Convert to sorted list
	dates := make([]string, 0, len(allDates))
	for d := range allDates {
		dates = append(dates, d)
	}
	sort.Strings(dates)

	days := make([]DailyUsage, 0, len(dates))
	for _, date := range dates {
		models := allDates[date]
		totalTokens := 0
		totalCost := 0.0
		// Convert *ModelUsage to ModelUsage for the DailyUsage struct
		modelsVal := make(map[string]ModelUsage, len(models))
		for name, m := range models {
			totalTokens += m.Tokens
			totalCost += m.Cost
			modelsVal[name] = ModelUsage{Name: m.Name, Tokens: m.Tokens, Cost: m.Cost}
		}
		days = append(days, DailyUsage{
			Date:        date,
			TotalTokens: totalTokens,
			TotalCost:   totalCost,
			Models:      modelsVal,
		})
	}

	return days
}

// AggregateUsage aggregates usage across days, filtering by date >= since.
// Returns (models_dict, total_cost, total_tokens).
func AggregateUsage(days []DailyUsage, since string) (map[string]ModelUsage, float64, int) {
	merged := make(map[string]ModelUsage)
	totalCost := 0.0
	totalTokens := 0
	for _, d := range days {
		if since != "" && d.Date < since {
			continue
		}
		totalCost += d.TotalCost
		totalTokens += d.TotalTokens
		for name, m := range d.Models {
			if existing, ok := merged[name]; ok {
				merged[name] = ModelUsage{
					Name:   name,
					Tokens: existing.Tokens + m.Tokens,
					Cost:   existing.Cost + m.Cost,
				}
			} else {
				merged[name] = ModelUsage{Name: name, Tokens: m.Tokens, Cost: m.Cost}
			}
		}
	}
	return merged, totalCost, totalTokens
}

// AllModelNames returns sorted model names by total cost (descending).
func AllModelNames(days []DailyUsage) []string {
	totals := make(map[string]float64)
	for _, d := range days {
		for name, m := range d.Models {
			totals[name] += m.Cost
		}
	}
	// Sort by cost descending
	type kv struct {
		name string
		cost float64
	}
	var sorted []kv
	for name, cost := range totals {
		sorted = append(sorted, kv{name, cost})
	}
	for i := 0; i < len(sorted); i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j].cost > sorted[i].cost {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}
	result := make([]string, len(sorted))
	for i, kv := range sorted {
		result[i] = kv.name
	}
	return result
}

// FmtTokens formats a token count with K/M/B suffix.
func FmtTokens(n int) string {
	if n >= 1_000_000_000 {
		return fmt.Sprintf("%.1fB", float64(n)/1e9)
	}
	if n >= 1_000_000 {
		return fmt.Sprintf("%.1fM", float64(n)/1e6)
	}
	if n >= 1_000 {
		return fmt.Sprintf("%.1fK", float64(n)/1e3)
	}
	return fmt.Sprintf("%d", n)
}

// FmtCost formats a cost as $N (rounded).
func FmtCost(n float64) string {
	return fmt.Sprintf("$%d", int(math.Round(n)))
}

// ModelProvider classifies a model name as "claude" or "codex".
func ModelProvider(name string) string {
	if len(name) >= 6 && name[:6] == "claude" {
		return "claude"
	}
	return "codex"
}
