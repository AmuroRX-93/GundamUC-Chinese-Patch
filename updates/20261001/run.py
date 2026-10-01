"""Portable entry point with local logs and an explicit package integrity check."""
from pathlib import Path
import datetime
import hashlib
import platform
import runpy
import sys
import traceback

PACKAGE = Path(__file__).resolve().parent


class Tee:
    def __init__(self, terminal, log):
        self.terminal, self.log = terminal, log

    def write(self, text):
        self.terminal.write(text)
        self.log.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


def verify_package():
    manifest = PACKAGE / 'SHA256SUMS.txt'
    entries = manifest.read_text(encoding='utf-8').splitlines()
    failures = []
    for line in entries:
        expected, name = line.split('  ', 1)
        path = (PACKAGE / name).resolve()
        if not path.is_relative_to(PACKAGE) or not path.is_file():
            failures.append(name + '：缺失或路径异常')
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != expected:
            failures.append(name + '：校验不符')
    for failure in failures:
        print(failure)
    if failures:
        print('安装包不完整或文件被修改。请重新完整解压最新累计差分包。')
        return 1
    print(f'安装包完整性检查通过：{len(entries)} 个文件。没有修改游戏。')
    return 0


def main():
    logs = PACKAGE / 'logs'
    try:
        logs.mkdir(exist_ok=True)
        name = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log'
        stream = (logs / name).open('x', encoding='utf-8')
    except OSError as error:
        print('无法创建日志，请将整个安装包解压到可写目录后运行：', error)
        return 1
    out, err = sys.stdout, sys.stderr
    status = 0
    with stream:
        sys.stdout, sys.stderr = Tee(out, stream), Tee(err, stream)
        try:
            print('高达 UC 2026.10.01 累计差分更新')
            print('Python:', platform.python_version(), platform.system(), platform.machine())
            print('日志：', logs / name)
            if sys.argv[1:] == ['--verify-package']:
                status = verify_package()
            else:
                if not all((PACKAGE / n).is_file() for n in ('install.py', 'engine.py')):
                    raise FileNotFoundError('缺少 install.py 或 engine.py，请完整重新解压累计差分包。')
                sys.path.insert(0, str(PACKAGE))
                runpy.run_path(str(PACKAGE / 'install.py'), run_name='__main__')
        except SystemExit as ex:
            status = ex.code if isinstance(ex.code, int) else (1 if ex.code else 0)
        except (EOFError, KeyboardInterrupt):
            print('\n操作已取消。')
            status = 1
        except Exception:
            traceback.print_exc()
            status = 1
        finally:
            print('退出码：', status)
            sys.stdout, sys.stderr = out, err
    return status


if __name__ == '__main__':
    raise SystemExit(main())
