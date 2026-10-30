#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 fehlt. Installiere Python 3 (Version 3.10 oder neuer) und starte diese Datei erneut.'
  echo 'Die Anleitung findest du in START-HIER.html.'
  read -r -p 'Enter zum Schließen … '
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo 'Bitte Python 3.10 oder neuer installieren. Siehe START-HIER.html.'
  read -r -p 'Enter zum Schließen … '
  exit 1
fi
python3 app.py
