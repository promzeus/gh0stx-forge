# Сбор материала → датасет → дообучение

Три шага. Первые два — сбор сырья, третий встраивается в основной RFT-пайплайн
(`../../RUNBOOK.md`). Гонять на боксе gx10 (открытый WAN), не в облачной песочнице:
та за прокси и до hackenproof.com через `requests`/`curl` не достучится. Логика скрипта
проверена (структура страниц, слаги, публичный доступ без логина), финальный прогон
делается на боксе.

## Шаг 1. Собрать материал

```bash
pip install -r requirements.txt

# HackenProof: публичные страницы всех программ (без логина, слаги совпадают с dashboard)
python collect_hackenproof.py --limit 10   # сначала тест на 10 программах
python collect_hackenproof.py              # полный прогон (323 программы, задержка между запросами)

# Внешние репозитории с раскрытыми находками/writeup'ами/PoC
bash clone_repos.sh ./corpus
```

Результат:
- `data/hackenproof_programs.jsonl` — строка на программу: слаг, url, заголовок, сырой
  текст страницы (scope/severity/out-of-scope/rules вперемешку, без разбивки — см. Шаг 2).
- `corpus/` — склонированные репозитории: Code4rena, DeFiHackLabs, SWC-registry и др.

Полный список источников, включая нескриптуемые (Solodit, HackerOne Hacktivity, PortSwigger
Academy — API с ключом либо выборочный просмотр) — в `../../data/security-training-resources.md`.

## Шаг 2. Структурировать сырьё в пары для SFT

`raw_text` с HackenProof специально не распарсен регулярками — вёрстка меняется, регулярки
ломаются. Прогнать через локальную модель промптом:

```
Ниже сырой текст страницы bug bounty программы. Извлеки JSON со полями:
{
  "target_type": "smart_contract | blockchain_protocol | web | mobile | api",
  "in_scope_targets": [...],
  "vulnerability_classes_rewarded": [...],
  "severity_table": {"critical": "...", "high": "...", "medium": "...", "low": "..."},
  "out_of_scope_patterns": [...],
  "hard_rules": [...]
}
Если поле не найдено в тексте — ставь null. Не выдумывай данные, которых нет в тексте.

ТЕКСТ:
<<<{raw_text}>>>
```

Из структурированных записей строятся SFT-пары двух типов:
1. `code/scope → likely vuln classes + severity` (классификация/триаж).
2. `raw finding description → полированный отчёт по шаблону` (шаблон —
   https://docs.hackenproof.com/bug-bounty/reports-basics).

Репозитории из `corpus/` (Code4rena, DeFiHackLabs, SWC-registry) уже дают почти готовые
пары «уязвимый код + PoC + severity» — их нормализовать в единый JSON, не генерировать.

## Шаг 3. Дообучение

Пайплайн тот же, что в `../../RUNBOOK.md`: ZeRO-3 LoRA на fused-базе, merge, NVFP4
граф-реквант, деплой. Отличие — доменные данные (крипто/смарт-контракты + протокольный
слой) и явное обучение на out-of-scope паттернах, иначе модель заваливает шумом, который
платформы всё равно отклонят. Формат messages с системным промптом security research
assistant в рамках authorized engagements — как в текущем корпусе.

Цель артефакта — рабочий PoC: HackenProof на каждой странице повторяет «AI-generated
reports without runnable PoC are not accepted». Грейдить на исполнимом PoC, как harvest
self-test.

## Границы

- Скрипт ходит только по публичным `hackenproof.com/programs/<slug>`, dashboard не трогает,
  ничего не сабмитит.
- Решение владельца: в датасет идёт и то, что видно из личного кабинета, включая ещё не
  disclosed находки — модель локальная и в общий доступ не выкладывается. Такие записи
  помечать `source: private_dashboard` в meta, чтобы отделялись одним фильтром. Остаточный
  риск на владельце: копирование undisclosed-данных может нарушать disclosure-термины
  программы, и локальная модель может выдать выученную находку тому, кто к ней обращается.
- Держать `DELAY_SECONDS` — не превращать сбор публичных страниц в нагрузочный сканер.
- Автосбор проверить на соответствие ToS HackenProof до полного прогона — аккаунт тот же,
  что ведёт в GUR CTF-воронку.
