#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
export PYTHONUTF8=1
if command -v python3 >/dev/null 2>&1; then
  python3 install.py "$@"
else
  echo "需要 Python 3.10 或更新版本。安装后重新打开；详见《先读我》。"
fi
printf '\n按回车关闭窗口。'
read answer
