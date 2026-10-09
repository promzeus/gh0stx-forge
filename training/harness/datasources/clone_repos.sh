#!/usr/bin/env bash
# Клонирует публичные репозитории с раскрытыми находками/аудитами/writeup'ами
# (shallow clone, --depth 1, чтобы не тащить всю историю).
# Использование: bash clone_repos.sh ./corpus

set -euo pipefail
DEST="${1:-./corpus}"
mkdir -p "$DEST"
cd "$DEST"

repos=(
  "https://github.com/hackenproof-public/suidex-reports"
  "https://github.com/zhongeric/code4rena-reports"
  "https://github.com/SunWeb3Sec/DeFiHackLabs"
  "https://github.com/SunWeb3Sec/DeFiVulnLabs"
  "https://github.com/SmartContractSecurity/SWC-registry"
  "https://github.com/sayan011/Immunefi-bug-bounty-writeups-list"
  "https://github.com/swisskyrepo/PayloadsAllTheThings"
  "https://github.com/acorn421/awesome-smart-contract-datasets"
  "https://github.com/saeidshirazi/Awesome-Smart-Contract-Security"
)

for repo in "${repos[@]}"; do
  name="$(basename "$repo")"
  if [ -d "$name" ]; then
    echo "[.] $name уже склонирован, пропускаю (сделайте git pull вручную при необходимости)"
    continue
  fi
  echo "[*] Клонирую $repo"
  git clone --depth 1 "$repo" "$name" || echo "[!] не удалось склонировать $repo"
done

echo "[*] Готово. Репозитории лежат в $DEST"
echo "[*] Sherlock и Cantina — организации из множества отдельных репозиториев по контестам,"
echo "    их нужно клонировать выборочно через 'gh repo list sherlock-audit' / 'gh repo list cantina-competitions'"
