#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

compose=(docker compose -f docker-compose.yml -f docker-compose.vps.yml)

printf '\033[2J\033[H'
printf '\033[1;32m'
printf '======================================================================\n'
printf '  LEGACY MODEL // TEN-MARKET EXECUTION STREAM // LIVE\n'
printf '======================================================================\n'
printf '\033[0;36m  Cyan/blue/magenta: ticks   Yellow: signals   Green: execution\n'
printf '\033[0;36m  Bright green: wins         Bright red: losses/errors\n'
printf '\033[0m\n'

"${compose[@]}" logs -f --tail=0 worker 2>&1 \
  | stdbuf -o0 tr '\r' '\n' \
  | grep --line-buffered -E \
      'LIVE |RF_SIGNAL|RF_DECISION|PURCHASE|CONTRACT|RECOVERY|ACCOUNT_SKIPPED|ACCOUNT_EXCLUDED|RECONNECT|ERROR|FAILED|CRITICAL|TRACEBACK|WIN|LOSS' \
  | while IFS= read -r line; do
      printf -v now '%(%H:%M:%S)T' -1

      case "$line" in
        *CRITICAL*|*TRACEBACK*|*ERROR*|*FAILED*)
          printf '\033[1;97;41m[%s] [ALERT]\033[0m \033[1;31m%s\033[0m\n' "$now" "$line"
          ;;
        *CONTRACT_SETTLED*result=WIN*|*' WIN '*|*'WIN profit='*)
          printf '\033[2;37m[%s]\033[0m \033[1;30;102m[WIN]\033[0m \033[1;92m%s\033[0m\n' "$now" "$line"
          ;;
        *CONTRACT_SETTLED*result=LOSS*|*' LOSS '*|*'LOSS profit='*)
          printf '\033[2;37m[%s]\033[0m \033[1;97;101m[LOSS]\033[0m \033[1;91m%s\033[0m\n' "$now" "$line"
          ;;
        *RF_ONE_SHOT_RECOVERY*)
          printf '\033[2;37m[%s]\033[0m \033[1;97;45m[RECOVERY]\033[0m \033[1;95m%s\033[0m\n' "$now" "$line"
          ;;
        *PURCHASE_REQUESTED*|*PURCHASE_CONFIRMED*|*CONTRACT_REGISTERED*)
          printf '\033[2;37m[%s]\033[0m \033[1;30;42m[EXECUTE]\033[0m \033[1;92m%s\033[0m\n' "$now" "$line"
          ;;
        *RF_SIGNAL_QUALIFIED*)
          printf '\033[2;37m[%s]\033[0m \033[1;30;103m[SIGNAL]\033[0m \033[1;93m%s\033[0m\n' "$now" "$line"
          ;;
        *RF_DECISION*)
          printf '\033[2;37m[%s]\033[0m \033[1;33m[DECISION]\033[0m \033[0;93m%s\033[0m\n' "$now" "$line"
          ;;
        *ACCOUNT_SKIPPED*|*ACCOUNT_EXCLUDED*)
          printf '\033[2;37m[%s]\033[0m \033[1;30;43m[ISOLATED]\033[0m \033[0;33m%s\033[0m\n' "$now" "$line"
          ;;
        *RECONNECT*)
          printf '\033[2;37m[%s]\033[0m \033[1;30;46m[NETWORK]\033[0m \033[1;96m%s\033[0m\n' "$now" "$line"
          ;;
        *'LIVE 1HZ100V '*) tick_color=96 ;;
        *'LIVE 1HZ10V '*)  tick_color=92 ;;
        *'LIVE 1HZ25V '*)  tick_color=93 ;;
        *'LIVE 1HZ50V '*)  tick_color=95 ;;
        *'LIVE 1HZ75V '*)  tick_color=94 ;;
        *'LIVE R_10 '*)     tick_color=36 ;;
        *'LIVE R_25 '*)     tick_color=32 ;;
        *'LIVE R_50 '*)     tick_color=33 ;;
        *'LIVE R_75 '*)     tick_color=35 ;;
        *'LIVE R_100 '*)    tick_color=91 ;;
        *)
          printf '\033[2;37m[%s] [SYSTEM]\033[0m %s\n' "$now" "$line"
          continue
          ;;
      esac

      if [[ ${tick_color:-} ]]; then
        printf '\033[2;37m[%s]\033[0m \033[1;%sm[STREAM]\033[0m \033[%sm%s\033[0m\n' \
          "$now" "$tick_color" "$tick_color" "$line"
        unset tick_color
      fi
    done
