#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
export PYTHONUTF8=1
exec python3 install.py "$@"
