# StayMacGuest — один запуск с утра на весь день Guest

## Задача школы

Mosyle: `AutoLogOutDelay = 1800` (30 мин HID-idle).  
Своё Accessibility без пароля админа не включить.  
У **Firefox** AX уже выдан профилем — через него и работаем.

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
| Ты за маком (idle &lt; 10 мин) | Ничего не делает |
| Простой ≥ 10 мин (лекция и т.п.) | Тихий jiggle через Firefox, сброс idle |
| Политика logout | ~30 мин → порог 10 мин с запасом |

Пароль админа **не нужен**. Отдельный Firefox (профиль в `~/Library/Application Support/StayMacGuest/`) сворачивается; его лучше не закрывать вручную — при необходимости движок поднимет снова.

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
