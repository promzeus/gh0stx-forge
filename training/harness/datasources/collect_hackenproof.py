#!/usr/bin/env python3
"""
collect_hackenproof.py
-----------------------
Test-заготовка для сбора публичных данных с hackenproof.com в структурированный
корпус, пригодный для последующей разметки под instruction-tuning датасет.

Что делает:
1. Проходит по публичному листингу https://hackenproof.com/programs?page=N
   и собирает слаги всех программ (пагинация).
2. Для каждой программы забирает страницу https://hackenproof.com/programs/<slug>
   и вытаскивает структурированные секции: focus area, in-scope таргеты,
   severity/rewards таблицу, out-of-scope список, program rules.
3. Складывает всё в data/hackenproof_programs.jsonl — по одной программе на строку.

Ничего не отправляет никуда, кроме обычных GET-запросов к публичным страницам.
Уважайте rate limit — между запросами есть задержка (DELAY_SECONDS).

Использование:
    pip install -r requirements.txt
    python collect_hackenproof.py --max-pages 33 --out data/hackenproof_programs.jsonl

Дальше data/hackenproof_programs.jsonl можно скормить локальной модели с промптом
вида (см. README.md) — попросить её разложить каждую запись на
{target_type, vuln_classes, severity_table, out_of_scope_patterns} в чистом JSON,
и уже из этого собирать SFT-пары.
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://hackenproof.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (research-corpus-collector; contact: set-your-own-ua)"
}
DELAY_SECONDS = 1.5  # будьте вежливы к серверу


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def collect_slugs(max_pages: int) -> list[str]:
    slugs: list[str] = []
    for page in range(1, max_pages + 1):
        url = f"{BASE}/programs?page={page}"
        try:
            soup = get_soup(url)
        except requests.RequestException as e:
            print(f"[!] page {page} failed: {e}")
            continue
        found_this_page = 0
        for a in soup.select('a[href^="/programs/"]'):
            href = a.get("href", "")
            m = re.match(r"^/programs/([a-z0-9\-]+)/?$", href)
            if m:
                slug = m.group(1)
                if slug not in slugs:
                    slugs.append(slug)
                    found_this_page += 1
        print(f"[.] page {page}: +{found_this_page} slugs (total {len(slugs)})")
        if found_this_page == 0 and page > 1:
            # похоже, страницы закончились раньше max_pages
            break
        time.sleep(DELAY_SECONDS)
    return slugs


def section_text_after(soup: BeautifulSoup, heading_text: str) -> str:
    """Грубая эвристика: находим узел с текстом заголовка секции и
    забираем текст соседних элементов до следующего похожего заголовка."""
    heading = soup.find(string=re.compile(rf"^\s*{re.escape(heading_text)}\s*$", re.I))
    if not heading:
        return ""
    node = heading.parent
    collected = []
    sib = node.find_next_sibling()
    steps = 0
    while sib and steps < 40:
        text = sib.get_text(" ", strip=True)
        if text:
            collected.append(text)
        sib = sib.find_next_sibling()
        steps += 1
    return "\n".join(collected)


def parse_program(slug: str) -> dict:
    url = f"{BASE}/programs/{slug}"
    soup = get_soup(url)

    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else slug

    full_text = soup.get_text("\n", strip=True)

    record = {
        "slug": slug,
        "url": url,
        "title": title,
        # сырой текст страницы -- самый надёжный вариант, разметку по секциям
        # (focus area / in scope / severity / out of scope / rules) проще и
        # точнее сделать локальной моделью на этапе структурирования, чем
        # регулярками против верстки, которая может меняться.
        "raw_text": full_text,
    }
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=33)
    ap.add_argument("--out", type=str, default="data/hackenproof_programs.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число программ (0 = без лимита, для теста поставьте напр. 10)")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("[*] Собираю список программ...")
    slugs = collect_slugs(args.max_pages)
    if args.limit:
        slugs = slugs[: args.limit]
    print(f"[*] Всего программ к обработке: {len(slugs)}")

    with out_path.open("w", encoding="utf-8") as f:
        for i, slug in enumerate(slugs, 1):
            try:
                record = parse_program(slug)
            except requests.RequestException as e:
                print(f"[!] {slug} failed: {e}")
                continue
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(slugs)}] {slug} OK")
            time.sleep(DELAY_SECONDS)

    print(f"[*] Готово: {out_path}")


if __name__ == "__main__":
    main()
