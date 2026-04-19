package data

import (
	"bufio"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ReadClaudeUsage reads Claude Code JSONL files from ~/.claude/projects and
// aggregates by date + model. Deduplicates by message.id / requestId.
// Groups by local-timezone date. Returns {date: {model: ModelUsage}}.
func ReadClaudeUsage(since string) map[string]map[string]*ModelUsage {
	result := make(map[string]map[string]*ModelUsage)

	home, err := os.UserHomeDir()
	if err != nil {
		return result
	}
	claudeDir := filepath.Join(home, ".claude", "projects")
	if _, err := os.Stat(claudeDir); os.IsNotExist(err) {
		return result
	}

	seenIDs := make(map[string]struct{})
	localTZ := time.Now().Location()

	_ = filepath.WalkDir(claudeDir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".jsonl") {
			return nil
		}

		f, err := os.Open(path)
		if err != nil {
			log.Printf("Error opening %s: %v", path, err)
			return nil
		}
		defer f.Close()

		scanner := bufio.NewScanner(f)
		scanner.Buffer(make([]byte, 0, 64*1024), 50*1024*1024) // 50MB buffer

		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}

			var obj struct {
				Timestamp string `json:"timestamp"`
				RequestID string `json:"requestId"`
				Message   struct {
					Model string `json:"model"`
					ID    string `json:"id"`
					Usage struct {
						InputTokens               int64 `json:"input_tokens"`
						OutputTokens              int64 `json:"output_tokens"`
						CacheCreationInputTokens  int64 `json:"cache_creation_input_tokens"`
						CacheReadInputTokens      int64 `json:"cache_read_input_tokens"`
					} `json:"usage"`
				} `json:"message"`
			}
			if err := json.Unmarshal([]byte(line), &obj); err != nil {
				continue
			}

			ts := obj.Timestamp
			msg := obj.Message
			if ts == "" || msg.Model == "" || msg.Model == "<synthetic>" {
				continue
			}
			// Check usage has any fields (skip if all zero and no usage block)
			usage := msg.Usage
			if usage.InputTokens == 0 && usage.OutputTokens == 0 &&
				usage.CacheCreationInputTokens == 0 && usage.CacheReadInputTokens == 0 {
				continue
			}

			// Deduplicate by message.id or requestId
			msgID := msg.ID
			if msgID == "" {
				msgID = obj.RequestID
			}
			if msgID != "" {
				if _, seen := seenIDs[msgID]; seen {
					continue
				}
				seenIDs[msgID] = struct{}{}
			}

			// Convert UTC timestamp to local date
			date := parseLocalDate(ts, localTZ)
			if date < since {
				continue
			}

			inp := usage.InputTokens
			out := usage.OutputTokens
			cw := usage.CacheCreationInputTokens
			cr := usage.CacheReadInputTokens
			tokens := int(inp + out + cw + cr)
			cost := ComputeCost(msg.Model, inp, out, cw, cr)

			day, ok := result[date]
			if !ok {
				day = make(map[string]*ModelUsage)
				result[date] = day
			}
			if existing, ok := day[msg.Model]; ok {
				existing.Tokens += tokens
				existing.Cost += cost
			} else {
				day[msg.Model] = &ModelUsage{Name: msg.Model, Tokens: tokens, Cost: cost}
			}
		}

		if err := scanner.Err(); err != nil {
			log.Printf("Error scanning %s: %v", path, err)
		}
		return nil
	})

	return result
}

// parseLocalDate converts an ISO timestamp string to a YYYY-MM-DD date in the
// given local timezone.
func parseLocalDate(ts string, loc *time.Location) string {
	// Try RFC3339 / ISO8601 formats
	for _, layout := range []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02T15:04:05Z",
		"2006-01-02T15:04:05.000Z",
	} {
		if t, err := time.Parse(layout, ts); err == nil {
			return t.In(loc).Format("2006-01-02")
		}
	}
	// Replace Z with +00:00 for Go parsing
	normalized := strings.Replace(ts, "Z", "+00:00", 1)
	if t, err := time.Parse("2006-01-02T15:04:05-07:00", normalized); err == nil {
		return t.In(loc).Format("2006-01-02")
	}
	if t, err := time.Parse("2006-01-02T15:04:05.999999999-07:00", normalized); err == nil {
		return t.In(loc).Format("2006-01-02")
	}
	// Fallback: first 10 chars
	if len(ts) >= 10 {
		return ts[:10]
	}
	return ts
}
