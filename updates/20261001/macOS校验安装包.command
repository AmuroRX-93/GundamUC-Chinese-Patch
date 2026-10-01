#!/bin/zsh
cd -- "${0:A:h}"
case "$(/usr/bin/uname -m)" in
  arm64) UC_PY="./runtime/macos-arm64/python/bin/python3" ;;
  x86_64) UC_PY="./runtime/macos-x64/python/bin/python3" ;;
  *) echo "不支持的 Mac 芯片类型"; exit 1 ;;
esac
if [[ ! -x "$UC_PY" ]]; then
  echo "包内 Python 缺失或无执行权限，请完整重新解压。"
  read -r "?按回车关闭"
  exit 1
fi
"$UC_PY" -I -S -B -X utf8 run.py --verify-package "$@"
UC_EXIT=$?
read -r "?按回车关闭"
exit $UC_EXIT
