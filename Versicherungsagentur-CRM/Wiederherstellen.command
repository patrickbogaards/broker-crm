#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
python3 restore.py
read -r -p 'Enter zum Schließen … '
