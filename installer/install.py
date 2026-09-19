#!/usr/bin/env python3
"""Offline, standard-library installer for a fingerprinted Gundam UC resource release."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import time
import uuid
from delta import apply as apply_delta

HERE = Path(__file__).resolve().parent
CHUNK = 8 * 1024 * 1024
LOCK = '.gundam-uc-cn.lock'


def digest(path, algorithm='sha256', limit=None):
    h = hashlib.new(algorithm)
    with open(path, 'rb') as f:
        left = limit
        while left is None or left:
            data = f.read(CHUNK if left is None else min(CHUNK, left))
            if not data:
                if left:
                    raise ValueError('文件被截断：' + str(path))
                break
            h.update(data)
            if left is not None:
                left -= len(data)
    return h.hexdigest()


def safe(root, name):
    p = PurePosixPath(name)
    if not p.parts or p.is_absolute() or any(x in ('.', '..') or ':' in x or '\\' in x for x in p.parts):
        raise ValueError('非法相对路径：' + name)
    root = Path(root).resolve()
    target = root
    for part in p.parts:
        # Original UDF names and older ISO9660-derived bundles differ in case.
        # Resolve each existing component, but never silently choose a collision.
        matches = [x for x in target.iterdir() if x.name.casefold() == part.casefold()] if target.is_dir() else []
        if len(matches) > 1:
            raise ValueError('存在大小写冲突，未修改文件：' + str(target / part))
        target = matches[0] if matches else target / part
        if target.is_symlink():
            raise ValueError('不支持资源目录内的符号链接：' + str(target))
    return target


def write_json(path, value):
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sfo(path):
    data = path.read_bytes()
    magic, version, keys, values, count = struct.unpack_from('<4s4I', data)
    if magic != b'\0PSF' or count > 1024:
        raise ValueError('PARAM.SFO 格式不正确')
    result = {}
    for i in range(count):
        key, fmt, length, cap, off = struct.unpack_from('<HHIII', data, 20 + 16 * i)
        name = data[keys+key:data.index(b'\0', keys+key)].decode('utf-8')
        result[name] = data[values+off:values+off+length].rstrip(b'\0').decode('utf-8', 'replace')
    return result


def game_root(path):
    root = Path(path).expanduser().resolve()
    if root.name.upper() == 'USRDIR':
        root = root.parent
    if root.name.upper() == 'PS3_GAME':
        root = root.parent
    if not safe(root, 'PS3_GAME/PARAM.SFO').is_file():
        raise ValueError('请选择包含 PS3_GAME 的已解密游戏文件夹；不直接修改 ISO。')
    return root


def check_game(root, m):
    meta = sfo(safe(root, 'PS3_GAME/PARAM.SFO'))
    if meta.get('TITLE_ID') != m['game'] or meta.get('APP_VER') != m['version']:
        raise ValueError('仅支持日版 BLJS10154 / APP_VER 01.00；所选游戏版本不符。')


def load(package):
    raw = (package / 'manifest.json').read_bytes()
    expected = (package / 'manifest.sha256').read_text().split()[0]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('安装清单损坏，请重新解压完整安装包。')
    m = json.loads(raw)
    if m['format'] not in (2, 3) or m['game'] != 'BLJS10154':
        raise ValueError('不支持的补丁格式')
    return m


def payload_check(package, m):
    rows = [part for r in m['base'] + m['dlc'] for v in [r] + r.get('input_variants', []) for part in v['segments']]
    for i, r in enumerate(rows):
        p = safe(package, r['payload'])
        if digest(p) != r['patch_sha256'] or p.stat().st_size != r['patch_size']:
            raise ValueError('差分文件损坏：' + r['payload'])
        if (i + 1) % 100 == 0:
            print('校验差分包：%d/%d' % (i + 1, len(rows)), flush=True)


def check_update(root, meta, m):
    """Allow only manifest-fingerprinted update overlays; never replace EBOOT."""
    names = {'eboot.bin', 'launchdata.bnd', 'gamedata.bhd', 'gamedata.bdt'}
    overlays = [p for p in safe(root, 'USRDIR').iterdir() if p.name.casefold() in names]
    if not overlays:
        if meta.get('APP_VER') == '01.01':
            raise ValueError('1.01 升级数据不完整，缺少已验证的 EBOOT.BIN。')
        return
    for profile in m.get('update_profiles', []):
        if meta.get('APP_VER') != profile['app_version']:
            continue
        expected = {r['path'].casefold(): r for r in profile['files']}
        found = {p.relative_to(root).as_posix().casefold() for p in overlays}
        if found != set(expected):
            continue
        if all(p.is_file() and not p.is_symlink() and p.stat().st_size == expected[p.relative_to(root).as_posix().casefold()]['size']
               and digest(p) == expected[p.relative_to(root).as_posix().casefold()]['sha256'] for p in overlays):
            print('已核对升级版本：' + profile['app_version'], flush=True)
            return
    raise ValueError('存在未适配或指纹不符的本体覆盖资源；未写入任何文件。')


def plan(package, m, roots):
    roots = {s: Path(p).resolve() for s, p in roots.items()}
    check_game(roots['base'], m)
    for scope, root in roots.items():
        if (root / LOCK).exists():
            raise ValueError('检测到另一安装过程或未恢复的中断。请先恢复对应备份：' + str(root / LOCK))
    payload_check(package, m)
    print('正在核对游戏资源版本，期间不修改游戏。', flush=True)
    for r in m['unchanged']:
        p = safe(roots['base'], r['path'])
        if not p.is_file() or p.stat().st_size != r['size'] or digest(p, 'md5') != r['md5']:
            raise ValueError('原版资源不匹配（其他补丁、加密或不完整文件）：' + r['path'])
    selected = [('base', r) for r in m['base']]
    if 'dlc' in roots:
        dr = roots['dlc']
        meta = sfo(safe(dr, 'PARAM.SFO'))
        if meta.get('TITLE_ID') != m['game']:
            raise ValueError('DLC 文件夹的游戏编号不匹配。')
        check_update(dr, meta, m)
        group_names = {x['path'].casefold() for x in m['dlc_groups']}
        plist_dir = safe(dr, 'USRDIR/system/packagelist')
        if plist_dir.exists():
            for p in plist_dir.iterdir():
                if p.is_file() and not p.name.startswith('._') and p.name.casefold().endswith('.plist.edat'):
                    rel = p.relative_to(dr).as_posix()
                    if rel.casefold() not in group_names:
                        raise ValueError('存在本版未验证的 DLC 索引，暂不安装：' + rel)
        for group in m['dlc_groups']:
            if safe(dr, group['path']).is_file():
                for dep in group['requires']:
                    if not safe(dr, dep).is_file():
                        raise ValueError('DLC 安装不完整，缺少：' + dep)
        for r in m['dlc']:
            p = safe(dr, r['path'])
            if p.is_file():
                # Both archive halves must be present before replacing either.
                for a, b in (('.bhd.edat', '.bdt.edat'), ('.bdt.edat', '.bhd.edat'), ('.bhd', '.bdt'), ('.bdt', '.bhd')):
                    if r['path'].endswith(a) and not safe(dr, r['path'][:-len(a)] + b).is_file():
                        raise ValueError('DLC 任务资源不成对：' + r['path'])
                selected.append(('dlc', r))
    changes = []
    already = 0
    for scope, r in selected:
        p = safe(roots[scope], r['path'])
        if not p.is_file():
            raise ValueError('缺少游戏文件：' + str(p))
        sha = digest(p)
        variants = [r] + [dict(v, path=r['path']) for v in r.get('input_variants', [])]
        if any(sha == v['sha256'] and p.stat().st_size == v['size'] for v in variants):
            already += 1
            continue
        variant_selected = False
        if r.get('input_variants'):
            matching = [v for v in variants if sha in v.get('accepted_sha256', [])]
            if scope == 'base' and not matching and p.stat().st_size == r['original_size'] and digest(p, 'md5') == r['original_md5']:
                matching = [r]
            if len(matching) != 1:
                raise ValueError('资源版本不受支持或清单匹配不唯一：' + str(p))
            r = matching[0]
            variant_selected = sha in r.get('accepted_sha256', [])
        if scope == 'base':
            # An unrecognized patched BDT is refused even if its original prefix survives.
            valid = sha in r.get('accepted_sha256', []) if variant_selected else p.stat().st_size == r['original_size'] and digest(p, 'md5') == r['original_md5']
        else:
            valid = sha in r['accepted_sha256']
        if not valid:
            raise ValueError('资源版本不受支持，未写入任何游戏文件：' + str(p))
        # Preserve the real on-disk spelling through staging and rollback.
        r = dict(r, path=p.relative_to(roots[scope]).as_posix())
        changes.append(dict(scope=scope, row=r, old_sha256=sha, old_size=p.stat().st_size))
    print('检查通过：需更新 %d 个文件，已有本版 %d 个文件。' % (len(changes), already), flush=True)
    return changes


def ensure_closed():
    """Fail closed if process enumeration is unavailable; never kill a running game."""
    if os.name == 'nt':
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace', timeout=20)
        running = 'rpcs3.exe' in result.stdout.lower()
    else:
        result = subprocess.run(['ps', '-A', '-o', 'comm='], capture_output=True, text=True, errors='replace', timeout=20)
        running = any(Path(line.strip()).name.lower().startswith('rpcs3') for line in result.stdout.splitlines())
    if result.returncode:
        raise ValueError('无法检查 RPCS3 是否退出；未修改游戏。请在正常终端运行安装器。')
    if running:
        raise ValueError('请先完整退出 RPCS3，再安装或恢复汉化。')


def copy_checked(src, dst, sha):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, 'rb') as a, open(dst, 'xb') as b:
        shutil.copyfileobj(a, b, CHUNK)
        b.flush()
        os.fsync(b.fileno())
    if digest(dst) != sha:
        raise ValueError('写入校验失败：' + str(dst))


def prepare(package, source, dst, r):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(source, 'rb') as a, open(dst, 'xb') as b:
        left = r.get('prefix_size', 0)
        if left > source.stat().st_size:
            raise ValueError('原始资源被截断')
        while left:
            data = a.read(min(CHUNK, left))
            if not data:
                raise ValueError('原始资源被截断')
            b.write(data)
            left -= len(data)
        for part in r['segments']:
            pad = part['output_offset'] - b.tell()
            if not 0 <= pad <= 15:
                raise ValueError('差分输出偏移不正确')
            if part['old_offset'] < 0 or part['old_size'] < 0 or part['old_offset'] + part['old_size'] > source.stat().st_size:
                raise ValueError('差分源数据范围不正确')
            a.seek(part['old_offset'])
            original = a.read(part['old_size'])
            patch = safe(package, part['payload']).read_bytes()
            if len(patch) != part['patch_size'] or hashlib.sha256(patch).hexdigest() != part['patch_sha256']:
                raise ValueError('差分文件在检查后发生变化')
            result = apply_delta(original, patch, part['size'], part['sha256'])
            b.write(b'\0' * pad)
            b.write(result)
        b.flush()
        os.fsync(b.fileno())
    if dst.stat().st_size != r['size'] or digest(dst) != r['sha256']:
        raise ValueError('差分重建资源校验失败')


def space_check(requirements):
    by_device = {}
    for root, size in requirements:
        device = root.stat().st_dev
        if device not in by_device:
            by_device[device] = [root, 0]
        by_device[device][1] += size
    for root, needed in by_device.values():
        needed += 256 * 1024 * 1024
        if shutil.disk_usage(root).free < needed:
            raise ValueError('空间不足：%s 至少需要 %.2f GiB 空闲用于暂存和回退。' % (root, needed / 1024**3))


def apply(package, m, roots, changes):
    roots = {s: Path(p).resolve() for s, p in roots.items()}
    if not changes:
        print('已安装相同版本，无需重复安装。')
        return None
    ensure_closed()
    ident = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    backup_parent = safe(roots['base'], 'GundamUC_CN_Backups')
    backup = backup_parent / ident
    affected = {c['scope'] for c in changes}
    stages = {s: safe(roots[s], '.uc-cn-stage-' + ident) for s in affected}
    space_check([(roots[s], sum(c['row']['size'] for c in changes if c['scope'] == s)) for s in affected] +
                [(roots['base'], sum(c['old_size'] for c in changes))])
    locks = []
    journal = dict(format=1, release=m['release'], id=ident, state='preparing', roots={s: str(p) for s, p in roots.items()}, files=[])
    committed = False
    try:
        for s in sorted(affected):
            lp = roots[s] / LOCK
            with open(lp, 'x', encoding='utf-8') as f:
                json.dump(dict(id=ident, backup=str(backup)), f, ensure_ascii=False)
            locks.append(lp)
        backup.mkdir(parents=True)
        write_json(backup / 'restore.json', journal)
        for i, c in enumerate(changes):
            r, s = c['row'], c['scope']
            target = safe(roots[s], r['path'])
            if digest(target) != c['old_sha256']:
                raise ValueError('检查后游戏文件发生变化：' + r['path'])
            old = safe(backup, s + '/' + r['path'])
            copy_checked(target, old, c['old_sha256'])
            new = safe(stages[s], r['path'])
            prepare(package, target, new, r)
            journal['files'].append(dict(scope=s, path=r['path'], old_sha256=c['old_sha256'], new_sha256=r['sha256']))
            write_json(backup / 'restore.json', journal)
            if (i + 1) % 20 == 0:
                print('准备资源和回退：%d/%d' % (i + 1, len(changes)), flush=True)
        ensure_closed()
        for c in changes:
            if digest(safe(roots[c['scope']], c['row']['path'])) != c['old_sha256']:
                raise ValueError('准备期间资源被其他程序修改，停止安装。')
        journal['state'] = 'committing'
        write_json(backup / 'restore.json', journal)
        committed = True
        for c in changes:
            s, r = c['scope'], c['row']
            target = safe(roots[s], r['path'])
            if digest(target) != c['old_sha256']:
                raise ValueError('安装期间资源被修改：' + r['path'])
            os.replace(safe(stages[s], r['path']), target)
        for c in changes:
            if digest(safe(roots[c['scope']], c['row']['path'])) != c['row']['sha256']:
                raise ValueError('安装后资源校验失败')
        journal['state'] = 'installed'
        write_json(backup / 'restore.json', journal)
        print('安装完成。恢复备份：' + str(backup), flush=True)
    except BaseException:
        if committed:
            print('安装中断，正在恢复本次修改……', flush=True)
            try:
                restore(backup, roots, check_process=False)
            except BaseException as e:
                print('自动恢复未完成；请保留备份并运行恢复功能：%s\n%s' % (backup, e), file=sys.stderr)
                locks = []  # Leave the locks as a visible recovery requirement.
                raise
        else:
            journal['state'] = 'cancelled_before_write'
            if backup.exists():
                write_json(backup / 'restore.json', journal)
        raise
    finally:
        for lp in locks:
            if lp.exists() and json.loads(lp.read_text(encoding='utf-8'))['id'] == ident:
                lp.unlink()
        for stage in stages.values():
            if stage.exists():
                shutil.rmtree(stage)
    return backup


def restore(backup, overrides=None, check_process=True):
    backup = Path(backup).resolve()
    journal = json.loads((backup / 'restore.json').read_text(encoding='utf-8'))
    if journal.get('format') != 1:
        raise ValueError('不支持的备份格式')
    if check_process:
        ensure_closed()
    roots = {s: Path(p).resolve() for s, p in journal['roots'].items()}
    roots.update(overrides or {})
    pending = []
    for r in journal['files']:
        old = safe(backup, r['scope'] + '/' + r['path'])
        if digest(old) != r['old_sha256']:
            raise ValueError('备份损坏，未开始恢复：' + r['path'])
        target = safe(roots[r['scope']], r['path'])
        current = digest(target)
        if current not in (r['old_sha256'], r['new_sha256']):
            raise ValueError('当前文件已被其他补丁改动，停止恢复：' + r['path'])
        if current != r['old_sha256']:
            pending.append((r, old, target))
    for root in roots.values():
        lock = root / LOCK
        if lock.exists() and json.loads(lock.read_text(encoding='utf-8'))['id'] != journal['id']:
            raise ValueError('另一安装事务尚未结束，请先处理其备份。')
    space_check([(target.parent, old.stat().st_size) for _, old, target in pending])
    journal['state'] = 'restoring'
    write_json(backup / 'restore.json', journal)
    for r, old, target in pending:
        if digest(target) != r['new_sha256']:
            raise ValueError('恢复期间目标发生变化，停止恢复。')
        tmp = target.with_name('.' + target.name + '.restore-' + uuid.uuid4().hex)
        try:
            copy_checked(old, tmp, r['old_sha256'])
            os.replace(tmp, target)
            if digest(target) != r['old_sha256']:
                raise ValueError('恢复后校验失败')
        finally:
            if tmp.exists():
                tmp.unlink()
    journal['state'] = 'restored'
    write_json(backup / 'restore.json', journal)
    for root in roots.values():
        lock = root / LOCK
        if lock.exists() and json.loads(lock.read_text(encoding='utf-8'))['id'] == journal['id']:
            lock.unlink()
        stage = safe(root, '.uc-cn-stage-' + journal['id'])
        if stage.exists():
            shutil.rmtree(stage)
    print('已恢复到本次安装前的状态；备份继续保留。', flush=True)


def choose(title):
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        try:
            return filedialog.askdirectory(title=title, mustexist=True)
        finally:
            window.destroy()
    except Exception:
        return input(title + '\n请输入文件夹路径（可拖入后去掉引号）：\n').strip().strip('\"\'')


def main():
    parser = argparse.ArgumentParser(description='高达 UC 独立汉化安装器（BLJS10154；支持范围以包内版本清单为准）')
    parser.add_argument('--game', type=Path)
    parser.add_argument('--dlc', type=Path, help='可选：RPCS3 的 dev_hdd0/game/BLJS10154')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--restore', type=Path, help='含 restore.json 的备份文件夹')
    args = parser.parse_args()
    if not any((args.game, args.restore)):
        print('高达 UC 差分汉化包\n1 安装或更新汉化\n2 只检查、不安装\n3 恢复安装前资源')
        mode = input('请选择 1/2/3：').strip()
        if mode == '3':
            selected = choose('选择含 restore.json 的汉化备份文件夹')
            if not selected:
                return
            args.restore = Path(selected)
        elif mode in ('1', '2'):
            args.check_only = mode == '2'
            selected = choose('选择包含 PS3_GAME 的游戏文件夹')
            if not selected:
                return
            args.game = Path(selected)
            if input('是否同时汉化已经安装的 DLC？输入 y，或直接回车仅安装本体：').strip().lower() == 'y':
                selected = choose('选择 RPCS3 的 dev_hdd0/game/BLJS10154 文件夹')
                if not selected:
                    return
                args.dlc = Path(selected)
        else:
            return
    roots = {}
    if args.game:
        roots['base'] = game_root(args.game)
    if args.dlc:
        roots['dlc'] = args.dlc.expanduser().resolve()
    if args.restore:
        restore(args.restore, roots)
        return
    if 'base' not in roots:
        parser.error('需要 --game')
    m = load(HERE)
    changes = plan(HERE, m, roots)
    if args.check_only:
        print('只读检查完成，游戏文件未修改。')
    else:
        apply(HERE, m, roots, changes)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as e:
        print('\n未完成：' + str(e), file=sys.stderr)
        sys.exit(1)
