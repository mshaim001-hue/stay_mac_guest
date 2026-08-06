# StayMacGuest — один запуск с утра на весь день Guest

## Задача школы

Два независимых таймера разлогина:

1. **Mosyle** `AutoLogOutDelay = 1800` (~30 мин). Считает `HIDIdleTime` — jiggle мыши через Firefox DE его сбрасывает.
2. **Tomorrow School** `ai.tomorrowschool.idlelogout` → `/usr/local/school/idle-logout.sh`  
   `THRESHOLD=3600` (60 мин). Замер через `osascript` + `CGEventSourceSecondsSinceLastEventType`.  
   Если замер не вернул число → скрипт делает `exit 0` и **не** разлогинивает.  
   Stay глушит этот `osascript` (и диалог «Я здесь») из-под Guest каждые 50 мс — admin не нужен.

Своё Accessibility без пароля админа не включить.  
Jiggle/blocker идут через **Firefox Developer Edition** (AX в Mosyle). Обычный `Firefox.app` не подходит.

## Как пользоваться

```bash
cd ~/Desktop/stay_mac_guest
./build.sh
./start_stay.sh          # или: open dist/StayMacGuest.app
```

**С утра один раз** → в меню-баре ⏳ → работаешь / уходишь на лекции / возвращаешься.  
Пока сам не сделаешь Quit / не выйдешь из Guest — сессия жива.

### Логика

| Состояние | Поведение |
|-----------|-----------|
| Ты за маком (idle &lt; 5 мин) | Ничего не делает |
| Простой ≥ 5 мин (лекция и т.п.) | Тихий jiggle через Firefox DE, сброс idle |
| Политика logout | ~30 мин → порог 5 мин с запасом |

Пароль админа **не нужен**. Отдельный **Firefox Developer Edition** (профиль в `~/Library/Application Support/StayMacGuest/`) сворачивается; его лучше не закрывать вручную — при необходимости движок поднимет снова.

### Меню ⏳

- статус (watching / armed / jiggle)
- Restart engine
- Open log → `~/Library/Logs/StayMacGuest-Firefox.log`
- Quit — когда уходишь из Guest сам

### Проверка

```bash
python3 ./stay_via_firefox.py --once
# … idle X → ~0s OK
```

## Почему не «просто своё приложение с мышью»

StayMacGuest без admin не получает Accessibility.  
ChatGPT/Firefox уже в whitelist Mosyle — jiggle идёт **из процесса Firefox**.  
Меню-бар только оркестрирует: следит за idle и держит движок весь день.
