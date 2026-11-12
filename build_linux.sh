#!/bin/sh
# Construit le binaire Linux dist/DepannagePC (a lancer SUR Linux).
# Conseil : construire sur une distribution ancienne (ex. Debian stable) pour
# que le binaire fonctionne aussi sur les distributions plus anciennes (glibc).
set -e
cd "$(dirname "$0")"
python3 -m pip install --user --upgrade pyinstaller
python3 -m PyInstaller --onefile --name DepannagePC --clean depannage.py
echo "OK : dist/DepannagePC"
