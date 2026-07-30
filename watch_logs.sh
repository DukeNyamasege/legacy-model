#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  LEGACY-MODEL  ·  Live Worker Log Viewer
#  Usage:  bash watch_logs.sh [--lines N] [--filter PATTERN]
# ─────────────────────────────────────────────────────────────────────────────

LINES=${2:-100}
FILTER=""
CONTAINER="legacy-model-worker-1"
COMPOSE_FILE="-f docker-compose.yml -f docker-compose.vps.yml"

while [[ $# -gt 0 ]]; do
  case $1 in
    --lines|-n) LINES="$2"; shift 2 ;;
    --filter|-f) FILTER="$2"; shift 2 ;;
    --container|-c) CONTAINER="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# ── ANSI palette ──────────────────────────────────────────────────────────────
RESET='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'

RED='\033[0;31m';     BRED='\033[1;31m';    BGRED='\033[1;41m'
YELLOW='\033[0;33m';  BYELLOW='\033[1;33m'
GREEN='\033[0;32m';   BGREEN='\033[1;32m'
CYAN='\033[0;36m';    BCYAN='\033[1;36m'
BLUE='\033[0;34m';    BBLUE='\033[1;34m'
MAGENTA='\033[0;35m'; BMAGENTA='\033[1;35m'
WHITE='\033[0;37m';   BWHITE='\033[1;37m'
GRAY='\033[2;37m'

# ── Banner ────────────────────────────────────────────────────────────────────
clear
printf "${BGREEN}"
cat << 'EOF'
  ██╗     ███████╗ ██████╗  █████╗  ██████╗██╗   ██╗    ███╗   ███╗ ██████╗ ██████╗ ███████╗██╗
  ██║     ██╔════╝██╔════╝ ██╔══██╗██╔════╝╚██╗ ██╔╝    ████╗ ████║██╔═══██╗██╔══██╗██╔════╝██║
  ██║     █████╗  ██║  ███╗███████║██║      ╚████╔╝     ██╔████╔██║██║   ██║██║  ██║█████╗  ██║
  ██║     ██╔══╝  ██║   ██║██╔══██║██║       ╚██╔╝      ██║╚██╔╝██║██║   ██║██║  ██║██╔══╝  ██║
  ███████╗███████╗╚██████╔╝██║  ██║╚██████╗   ██║       ██║ ╚═╝ ██║╚██████╔╝██████╔╝███████╗███████╗
  ╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝   ╚═╝       ╚═╝     ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝╚══════╝
EOF
printf "${RESET}"

printf "${DIM}  container: ${BWHITE}${CONTAINER}${RESET}  "
printf "${DIM}tail: ${BWHITE}${LINES} lines${RESET}  "
[[ -n "$FILTER" ]] && printf "${DIM}filter: ${BYELLOW}${FILTER}${RESET}  "
printf "${DIM}started: ${BWHITE}$(date '+%Y-%m-%d %H:%M:%S')${RESET}\n"
printf "${DIM}%s${RESET}\n\n" "$(printf '─%.0s' {1..100})"

# ── Color map via awk ─────────────────────────────────────────────────────────
colorize() {
  awk '
  BEGIN {
    # ANSI codes
    RESET   = "\033[0m"
    DIM     = "\033[2m"
    BOLD    = "\033[1m"

    BGRED   = "\033[1;41m"  # CRITICAL background
    BRED    = "\033[1;31m"  # ERROR
    BYELLOW = "\033[1;33m"  # WARNING
    BGREEN  = "\033[1;32m"  # PRIMARY mode / profit / win
    BCYAN   = "\033[1;36m"  # DIGITOVER / trade placed
    BBLUE   = "\033[1;34m"  # buy / proposal / signal selected
    BMAGENTA= "\033[1;35m"  # recovery mode events
    YELLOW  = "\033[0;33m"  # skipped / minor notices
    GREEN   = "\033[0;32m"  # INFO
    GRAY    = "\033[2;37m"  # DEBUG
    WHITE   = "\033[0;37m"  # default
  }

  {
    line = $0
    upper = toupper(line)

    # ── CRITICAL / unhandled exceptions ──────────────────────────────────────
    if (upper ~ /CRITICAL|UNHANDLED|TRACEBACK|EXCEPTION|FATAL/) {
      print BGRED line RESET
    }
    # ── ERROR ─────────────────────────────────────────────────────────────────
    else if (upper ~ /\bERROR\b|FAILED|FAILURE|CRASH/) {
      print BRED line RESET
    }
    # ── WARNING ───────────────────────────────────────────────────────────────
    else if (upper ~ /WARNING|WARN\b|PARAMETER_ERROR|RECONNECTING|DEADLOCK/) {
      print BYELLOW line RESET
    }
    # ── Recovery mode entered ─────────────────────────────────────────────────
    else if (upper ~ /HYBRID_RECOVERY|PUT_RECOVERY|ENTER_RECOVERY|RECOVERY_ARMED|RECOVERY_STAKE/) {
      print BMAGENTA line RESET
    }
    # ── Recovery cleared / primary resumed ───────────────────────────────────
    else if (upper ~ /HYBRID_PRIMARY_RESUMED|RETURN_TO_PRIMARY|SETTLEMENT_WAIT_CLEARED|SETTLEMENT_SYNCED/) {
      print BGREEN BOLD line RESET
    }
    # ── Trade placed / purchased ──────────────────────────────────────────────
    else if (upper ~ /DIGITOVER|CONTRACT_PURCHASED|BUY_SENT|TRADE_PLACED|PLACED.*STAKE|WON|PROFIT/) {
      print BCYAN line RESET
    }
    # ── Proposal / signal selected ────────────────────────────────────────────
    else if (upper ~ /PROPOSAL|SIGNAL_SELECTED|ARBITRAT|DIGIT_CANDIDATE|EDGE=/) {
      print BBLUE line RESET
    }
    # ── Skipped signals ───────────────────────────────────────────────────────
    else if (upper ~ /SKIP_|SKIPPED|LOSS|SETTLEMENT/) {
      print YELLOW line RESET
    }
    # ── Normal INFO ───────────────────────────────────────────────────────────
    else if (upper ~ /\bINFO\b/) {
      print GREEN line RESET
    }
    # ── DEBUG ─────────────────────────────────────────────────────────────────
    else if (upper ~ /\bDEBUG\b/) {
      print GRAY line RESET
    }
    # ── Default ───────────────────────────────────────────────────────────────
    else {
      print WHITE line RESET
    }
  }
  '
}

# ── Stream logs ───────────────────────────────────────────────────────────────
if [[ -n "$FILTER" ]]; then
  docker logs -f --tail "$LINES" "$CONTAINER" 2>&1 | grep --line-buffered -i "$FILTER" | colorize
else
  docker logs -f --tail "$LINES" "$CONTAINER" 2>&1 | colorize
fi
