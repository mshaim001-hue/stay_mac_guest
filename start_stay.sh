#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

# Prefer menu-bar app if built; else raw daemon.
if [[ -d dist/StayMacGuest.app ]]; then
  echo "Запуск StayMacGuest (один раз с утра → весь день Guest)."
  open dist/StayMacGuest.app
  exit 0
fi

chmod +x stay_via_firefox.py
echo "Приложение ещё не собрано — запускаю движок в терминале."
echo "Собери: ./build.sh   затем снова ./start_stay.sh"
exec python3 ./stay_via_firefox.py
