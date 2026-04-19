#!/usr/bin/env bash
input=$(cat)

eval "$(echo "$input" | jq -r '
  @sh "cwd=\(.workspace.current_dir // .cwd // "")",
  @sh "model=\(.model.display_name // "" | gsub(" \\(.*\\)$"; ""))",
  @sh "style=\(.output_style.name // "")",
  @sh "ctx_pct=\(.context_window.used_percentage // 0)",
  @sh "ctx_size=\(.context_window.context_window_size // 200000)",
  @sh "cost=\(.cost.total_cost_usd // 0)",
  @sh "lines_add=\(.cost.total_lines_added // 0)",
  @sh "lines_rm=\(.cost.total_lines_removed // 0)",
  @sh "duration_ms=\(.cost.total_duration_ms // 0)",
  @sh "session=\(.session_name // "")"
')"

cwd="${cwd/#$HOME/\~}"

RST='\033[0m'; DIM='\033[2m'; BOLD='\033[1m'
RED='\033[31m'; GRN='\033[32m'; YLW='\033[33m'; CYN='\033[36m'
SEP="${DIM} | ${RST}"

parts=""

[ -n "$session" ] && parts="${CYN}${session}${RST} "
parts="${parts}${BOLD}${cwd}${RST}"

if git rev-parse --git-dir > /dev/null 2>&1; then
  branch=$(git branch --show-current 2>/dev/null)
  if [ -n "$branch" ]; then
    dirty=""
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
      dirty="*"
    fi

    arrows=""
    if git rev-parse --verify --quiet '@{upstream}' >/dev/null 2>&1; then
      ahead=$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null)
      behind=$(git rev-list --count 'HEAD..@{upstream}' 2>/dev/null)
      [ "${ahead:-0}" -gt 0 ] && arrows="${arrows}↑${ahead}"
      [ "${behind:-0}" -gt 0 ] && arrows="${arrows}↓${behind}"
    fi

    if [ -n "$dirty" ]; then
      parts="${parts}${SEP}${YLW}${branch}${dirty}${RST}"
    else
      parts="${parts}${SEP}${GRN}${branch}${RST}"
    fi
    [ -n "$arrows" ] && parts="${parts}${DIM}${arrows}${RST}"
  fi
fi

if [ "${lines_add:-0}" -gt 0 ] || [ "${lines_rm:-0}" -gt 0 ]; then
  parts="${parts}${SEP}${GRN}+${lines_add}${RST} ${RED}-${lines_rm}${RST}"
fi

if [ -n "$cost" ] && [ "$cost" != "0" ] && [ "$cost" != "null" ]; then
  cost_fmt=$(printf '$%.2f' "$cost")
  parts="${parts}${SEP}${BOLD}${cost_fmt}${RST}"
fi

cache_file="/tmp/aiusage-daily-cost"
cache_max_age=300
daily_cost=""
if [ -f "$cache_file" ]; then
  cache_age=$(( $(date +%s) - $(stat -f%m "$cache_file") ))
  daily_cost=$(cat "$cache_file")
  if [ "$cache_age" -ge "$cache_max_age" ]; then
    (aiusage today > "$cache_file" 2>/dev/null &)
  fi
else
  (aiusage today > "$cache_file" 2>/dev/null &)
fi
if [ -n "$daily_cost" ] && [ "$daily_cost" != "0" ]; then
  daily_fmt=$(printf '$%.2f' "$daily_cost")
  parts="${parts}${SEP}${DIM}today:${RST}${daily_fmt}"
fi

ctx_int=$(printf '%.0f' "${ctx_pct:-0}" 2>/dev/null || echo "0")
if [ "${ctx_int:-0}" -ge 85 ] 2>/dev/null; then
  ctx_color="$RED"
elif [ "${ctx_int:-0}" -ge 60 ] 2>/dev/null; then
  ctx_color="$YLW"
else
  ctx_color="$GRN"
fi

ctx_label="200k"
[ "${ctx_size:-0}" -ge 1000000 ] && ctx_label="1M"

parts="${parts}${SEP}${ctx_color}${ctx_int}%${RST} ${DIM}${ctx_label}${RST}"

[ -n "$model" ] && parts="${parts}${SEP}${DIM}${model}${RST}"

[ -n "$style" ] && [ "$style" != "default" ] && parts="${parts}${SEP}${DIM}${style}${RST}"

diff_days=$(( ($(date +%s) - 947116800) / 86400 ))
phase_idx=$(( (diff_days * 100 % 2953) * 8 / 2953 ))
moon_phases=(🌑 🌒 🌓 🌔 🌕 🌖 🌗 🌘)
parts="${parts}${SEP}${moon_phases[$phase_idx]}"

if [ "${duration_ms:-0}" -gt 0 ] 2>/dev/null; then
  ds=$((duration_ms / 1000))
  if [ "$ds" -ge 3600 ]; then
    parts="${parts}${SEP}${DIM}$((ds/3600))h$(( (ds%3600)/60 ))m${RST}"
  elif [ "$ds" -ge 60 ]; then
    parts="${parts}${SEP}${DIM}$((ds/60))m${RST}"
  else
    parts="${parts}${SEP}${DIM}${ds}s${RST}"
  fi
fi

printf '%b' "$parts"
