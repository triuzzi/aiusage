package data

// ModelUsage holds per-model token and cost data.
type ModelUsage struct {
	Name   string
	Tokens int
	Cost   float64
}

// DailyUsage holds aggregated usage for a single date.
type DailyUsage struct {
	Date        string
	TotalTokens int
	TotalCost   float64
	Models      map[string]ModelUsage
}
