#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "=== Установка зависимостей ==="
python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null || { echo "Нужен Python 3.11+"; exit 1; }
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo
echo "Готово! Откройте .env и заполните API_ID, API_HASH, PHONE."
