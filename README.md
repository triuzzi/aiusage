# aiusage

AI coding usage dashboard — tracks your personal spend across Claude Code and OpenAI Codex.

## Install

```bash
go install github.com/triuzzi/aiusage/go-rewrite/cmd/aiusage@latest
```

Or build from source:

```bash
git clone https://github.com/triuzzi/aiusage.git
cd aiusage/go-rewrite
go build -o ~/.local/bin/aiusage ./cmd/aiusage
```

Single binary, zero runtime dependencies.

## Claude Code statusline

A rich statusline for Claude Code showing git, costs, context, model, and a moon phase.

```
 ~/unemployer | master↑1 | +3617 -1386 | $77.01 | today:$21796.88 | 36% 1M | Opus 4.8 | 🌒 | 206h33m
```

**Segments:** cwd, git branch (yellow if dirty, green if clean, ahead/behind arrows), lines changed, session cost, daily cost (cached 5min), context %, model, moon phase, session duration.

### Setup (3 steps)

**1. Install aiusage:**

```bash
go install github.com/triuzzi/aiusage/go-rewrite/cmd/aiusage@latest
```

**2. Download the statusline script:**

```bash
curl -fsSL https://raw.githubusercontent.com/triuzzi/aiusage/master/statusline/statusline.sh \
  -o ~/.claude/statusline.sh
```

**3. Add to `~/.claude/settings.json`:**

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline.sh"
  }
}
```

Restart Claude Code. Done.

### Requirements

- `jq` (for JSON parsing in the statusline script)
- `aiusage` binary in PATH (for the daily cost cache)

## TUI dashboard

```bash
aiusage
```

A terminal dashboard with:
- Per-model breakdown with provider subtotals (Claude / Codex / Total)
- Period-over-period deltas (today vs yesterday, 7d vs prev 7d, 30d vs prev 30d)
- 30-day sparkline chart
- Tamagotchi cat that reacts to your daily spend

## CLI

```bash
aiusage today              # Today's total cost (plain number)
aiusage daily --since 7    # Last 7 days, JSON
aiusage models --since 30  # Model/provider breakdown, JSON
```

## How it works

Reads usage data directly from local files — no API keys, no external services, no account setup:
- **Claude Code**: `~/.claude/projects/**/*.jsonl`
- **Codex**: `~/.codex/sessions/**/*.jsonl`
- **Pricing**: auto-fetched from [LiteLLM](https://github.com/BerriAI/litellm) (2000+ models), with offline fallback
