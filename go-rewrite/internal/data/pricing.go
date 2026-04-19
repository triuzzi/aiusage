package data

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

const litellmPricingURL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

type rates struct {
	Input      float64
	Output     float64
	CacheWrite float64
	CacheRead  float64
}

// fallbackPricing is used when LiteLLM is unreachable (keyed by substring match).
var fallbackPricing = map[string]rates{
	"opus-4-7":   {Input: 5e-6, Output: 25e-6, CacheWrite: 6.25e-6, CacheRead: 0.5e-6},
	"opus-4-6":   {Input: 5e-6, Output: 25e-6, CacheWrite: 6.25e-6, CacheRead: 0.5e-6},
	"opus-4-5":   {Input: 5e-6, Output: 25e-6, CacheWrite: 6.25e-6, CacheRead: 0.5e-6},
	"opus-4-1":   {Input: 15e-6, Output: 75e-6, CacheWrite: 18.75e-6, CacheRead: 1.5e-6},
	"sonnet-4-6": {Input: 3e-6, Output: 15e-6, CacheWrite: 3.75e-6, CacheRead: 0.3e-6},
	"sonnet-4-5": {Input: 3e-6, Output: 15e-6, CacheWrite: 3.75e-6, CacheRead: 0.3e-6},
	"haiku-4-5":  {Input: 1e-6, Output: 5e-6, CacheWrite: 1.25e-6, CacheRead: 0.1e-6},
	"sonnet-3-5": {Input: 3e-6, Output: 15e-6, CacheWrite: 3.75e-6, CacheRead: 0.3e-6},
	"haiku-3-5":  {Input: 0.8e-6, Output: 4e-6, CacheWrite: 1e-6, CacheRead: 0.08e-6},
}

var (
	pricingOnce sync.Once
	pricing     map[string]rates
)

func loadPricing() {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(litellmPricingURL)
	if err != nil {
		log.Printf("Could not fetch LiteLLM pricing — using fallback: %v", err)
		pricing = copyFallback()
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("Could not read LiteLLM pricing response — using fallback: %v", err)
		pricing = copyFallback()
		return
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		log.Printf("Could not parse LiteLLM pricing JSON — using fallback: %v", err)
		pricing = copyFallback()
		return
	}

	result := make(map[string]rates, len(raw))
	for key, val := range raw {
		var entry struct {
			InputCostPerToken                *float64 `json:"input_cost_per_token"`
			OutputCostPerToken               *float64 `json:"output_cost_per_token"`
			CacheCreationInputTokenCost      *float64 `json:"cache_creation_input_token_cost"`
			CacheReadInputTokenCost          *float64 `json:"cache_read_input_token_cost"`
		}
		if err := json.Unmarshal(val, &entry); err != nil {
			continue
		}
		if entry.InputCostPerToken == nil || entry.OutputCostPerToken == nil {
			continue
		}
		r := rates{
			Input:  *entry.InputCostPerToken,
			Output: *entry.OutputCostPerToken,
		}
		if entry.CacheCreationInputTokenCost != nil {
			r.CacheWrite = *entry.CacheCreationInputTokenCost
		}
		if entry.CacheReadInputTokenCost != nil {
			r.CacheRead = *entry.CacheReadInputTokenCost
		}
		result[key] = r
	}

	if len(result) > 0 {
		pricing = result
		log.Printf("Loaded pricing for %d models from LiteLLM", len(pricing))
	} else {
		pricing = copyFallback()
	}
}

func copyFallback() map[string]rates {
	m := make(map[string]rates, len(fallbackPricing))
	for k, v := range fallbackPricing {
		m[k] = v
	}
	return m
}

// ComputeCost calculates the USD cost for a single request given token counts.
func ComputeCost(model string, inputTokens, outputTokens, cacheWriteTokens, cacheReadTokens int64) float64 {
	pricingOnce.Do(loadPricing)

	// Exact match first (LiteLLM keys like "claude-opus-4-6")
	r, ok := pricing[model]
	if !ok {
		// Substring match (fallback keys like "opus-4-6")
		for pattern, pr := range pricing {
			if strings.Contains(model, pattern) {
				r = pr
				ok = true
				break
			}
		}
	}
	if !ok {
		return 0.0
	}
	return float64(inputTokens)*r.Input +
		float64(outputTokens)*r.Output +
		float64(cacheWriteTokens)*r.CacheWrite +
		float64(cacheReadTokens)*r.CacheRead
}
