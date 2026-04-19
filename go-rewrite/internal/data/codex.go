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

// ReadCodexUsage reads Codex rollout JSONL files from ~/.codex/sessions and
// aggregates by date + model. Returns {date: {model: ModelUsage}}.
func ReadCodexUsage(since string) map[string]map[string]*ModelUsage {
	result := make(map[string]map[string]*ModelUsage)

	home, err := os.UserHomeDir()
	if err != nil {
		return result
	}
	codexDir := filepath.Join(home, ".codex", "sessions")
	if _, err := os.Stat(codexDir); os.IsNotExist(err) {
		return result
	}

	localTZ := time.Now().Location()

	_ = filepath.WalkDir(codexDir, func(path string, d os.DirEntry, err error) error {
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

		var (
			model     string
			prevTotal int64
			date      string
			skipFile  bool
		)

		for scanner.Scan() {
			if skipFile {
				break
			}
			line := strings.TrimSpace(scanner.Text())
			if line == "" {
				continue
			}

			var obj struct {
				Type      string          `json:"type"`
				Timestamp string          `json:"timestamp"`
				Payload   json.RawMessage `json:"payload"`
			}
			if err := json.Unmarshal([]byte(line), &obj); err != nil {
				continue
			}

			switch obj.Type {
			case "session_meta":
				var payload struct {
					Timestamp string `json:"timestamp"`
				}
				if err := json.Unmarshal(obj.Payload, &payload); err != nil {
					continue
				}
				ts := payload.Timestamp
				if ts == "" {
					ts = obj.Timestamp
				}
				date = parseLocalDate(ts, localTZ)
				if date < since {
					skipFile = true
				}

			case "turn_context":
				var payload struct {
					Model string `json:"model"`
				}
				if err := json.Unmarshal(obj.Payload, &payload); err != nil {
					continue
				}
				if payload.Model != "" {
					model = payload.Model
				}

			case "event_msg":
				var payload struct {
					Type string `json:"type"`
					Info *struct {
						TotalTokenUsage struct {
							TotalTokens int64 `json:"total_tokens"`
						} `json:"total_token_usage"`
						LastTokenUsage struct {
							InputTokens       int64 `json:"input_tokens"`
							OutputTokens      int64 `json:"output_tokens"`
							CachedInputTokens int64 `json:"cached_input_tokens"`
						} `json:"last_token_usage"`
					} `json:"info"`
				}
				if err := json.Unmarshal(obj.Payload, &payload); err != nil {
					continue
				}
				if payload.Type != "token_count" || payload.Info == nil {
					continue
				}

				// If we don't have a date yet, try to get it from the event timestamp
				if date == "" {
					ts := obj.Timestamp
					if ts != "" {
						date = parseLocalDate(ts, localTZ)
						if date < since {
							skipFile = true
							continue
						}
					}
				}

				// Use total_token_usage delta to avoid double-counting
				newTotal := payload.Info.TotalTokenUsage.TotalTokens
				if newTotal <= prevTotal {
					continue // duplicate event
				}
				prevTotal = newTotal

				inp := payload.Info.LastTokenUsage.InputTokens
				out := payload.Info.LastTokenUsage.OutputTokens
				cached := payload.Info.LastTokenUsage.CachedInputTokens
				tokens := int(inp + out)
				// Cost: input billed = input_tokens - cached_input_tokens
				cost := ComputeCost(model, inp-cached, out, 0, 0)

				if model == "" || date == "" {
					continue
				}

				day, ok := result[date]
				if !ok {
					day = make(map[string]*ModelUsage)
					result[date] = day
				}
				if existing, ok := day[model]; ok {
					existing.Tokens += tokens
					existing.Cost += cost
				} else {
					day[model] = &ModelUsage{Name: model, Tokens: tokens, Cost: cost}
				}
			}
		}

		if err := scanner.Err(); err != nil {
			log.Printf("Error scanning %s: %v", path, err)
		}
		return nil
	})

	return result
}
