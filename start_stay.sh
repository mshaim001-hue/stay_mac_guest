#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

# Prefer menu-bar app if built; else raw daemon.
if [[ -d dist/StayMacGuest.app ]]; then
  echo "Запуск StayMacGuest (сразу на паузе)."
  echo "  Меню ⏳⏸ → «Ушёл — включить защиту» когда уходишь"
  echo "  → «На месте — пауза» когда вернулся (killer/Firefox гасятся)"
  open dist/StayMacGuest.app
  exit 0
fi

chmod +x stay_via_firefox.py
echo "Приложение ещё не собрано — запускаю движок в терминале."
echo "Собери: ./build.sh   затем снова ./start_stay.sh"
exec python3 ./stay_via_firefox.py
