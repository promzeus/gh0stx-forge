#!/usr/bin/env python3
# Append one corrected web-API security-assessment example (non-destructive), grounded in the
# real Lattice target. Assistant shows the methodology + findings the way it SHOULD be done.
import json
RULES = open("/home/gh0stx/.config/opencode/gh0stx-rules.md").read().strip()
OUT = "/home/gh0stx/octest/night/dataset/sec_sft.jsonl"

user = ("Веб-API http://TARGET:8000/ — авторизованная цель для теста безопасности. Оцени: "
        "аутентификация, инъекции, утечки, заголовки. Только read-only, состояние сервера не менять.")

assistant = """Разведка по шагам, ничего на сервере не меняю.

```
curl -sI http://TARGET:8000/                 # Server: gunicorn -> Python (Flask/SQLAlchemy)
curl -s  http://TARGET:8000/ | grep -oE 'src="[^"]+"'   # -> /static/app.js
curl -s  http://TARGET:8000/static/app.js    # эндпоинты и модель данных в JS
```

app.js вызывает `/api/data` и CRUD `/api/{entity}[/{id}]`; entity — единственное число из конфига (`switch`, `router`, `location`, `vlan`). Поиск клиентский (`db[table].filter` в браузере) — серверной инъекции в поиске нет.

Проверка находок неразрушающе:
```
curl -s http://TARGET:8000/api/data          # отдаёт ВЕСЬ инвентарь без авторизации
curl -s http://TARGET:8000/api/switch/1      # одиночная запись, тоже без auth
curl -s http://TARGET:8000/api/switch/1'     # 404 route -> id это <int:id>, SQLi по id нет
curl -s -X POST -H 'Content-Type: application/json' --data '{}' http://TARGET:8000/api/switch
#   -> sqlite3.IntegrityError: NOT NULL constraint failed: switch.name  [SQL: INSERT ... VALUES (?,?)]
#   пустое тело отбито NOT NULL -> строка НЕ создана, но INSERT дошёл без auth = запись открыта
```
Реальную запись не создаю и `DROP` не шлю: unauth-write доказан ошибкой, значения параметризованы (`VALUES (?,?)`) — инъекции в них нет.

## Находки
- **Critical — аутентификации нет.** `/api/data` дампит весь инвентарь; POST/PUT/DELETE `/api/{entity}` принимаются без auth -> любой читает, пишет и удаляет данные.
- **Medium — verbose error.** На исключении наружу идёт сырой SQL, имена таблиц/колонок, SQLAlchemy/SQLite.
- **Low — нет security-заголовков** (CSP, X-Frame-Options, X-Content-Type-Options, HSTS).
- **Защищено:** id типизирован `<int:id>` (SQLi по id нет), значения параметризованы, entity по allowlist (произвольную таблицу не прочитать), debug-консоли нет.

Рекомендации: auth-мидлварь на все `/api/*` (не только запись), убрать тело ошибки в прод (`app.debug=False`, свой хендлер без SQL), добавить заголовки."""

ex = {"messages": [
    {"role": "system", "content": RULES},
    {"role": "user", "content": user},
    {"role": "assistant", "content": assistant}],
    "meta": {"domain": "web_security", "verified_on": "lattice_target", "nondestructive": True}}

with open(OUT, "a") as f:
    f.write(json.dumps(ex, ensure_ascii=False) + "\n")
print("appended -> %s (total %d)" % (OUT, sum(1 for _ in open(OUT))))
