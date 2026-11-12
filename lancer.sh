#!/bin/sh
# Sous Linux, Python 3 est presque toujours installé : pas besoin de binaire.
cd "$(dirname "$0")"
exec python3 depannage.py "$@"
