package data

import "regexp"

var claudeFamily = map[string]string{
	"opus": "op", "sonnet": "sn", "haiku": "hk",
}

var (
	reGPT = regexp.MustCompile(`gpt-(\d+(?:\.\d+)?)-?(.*)`)
)

// ShortModel auto-generates short model names.
func ShortModel(name string) string {
	for family, prefix := range claudeFamily {
		re := regexp.MustCompile(family + `-(\d+)-(\d+)`)
		if m := re.FindStringSubmatch(name); m != nil {
			return prefix + m[1] + "." + m[2]
		}
	}
	if m := reGPT.FindStringSubmatch(name); m != nil {
		ver := m[1]
		suffix := m[2]
		tag := ""
		if contains(suffix, "mini") {
			tag = "m"
		} else if contains(suffix, "codex") {
			tag = "cx"
		}
		return ver + tag
	}
	if len(name) > 6 {
		return name[:6]
	}
	return name
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(s) > len(sub) && findSubstring(s, sub))
}

func findSubstring(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
