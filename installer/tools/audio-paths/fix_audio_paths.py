#!/usr/bin/env python3
"""Restore UC sound path spelling from its UDF directory; never change audio bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

HERE = Path(__file__).resolve().parent


def write_json(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def actual(root, rel):
    p = root
    for part in Path(rel).parts:
        matches = [x for x in p.iterdir() if x.name.casefold() == part.casefold()]
        if len(matches) != 1:
            raise ValueError('路径缺失或大小写冲突：' + rel)
        p = matches[0]
        if p.is_symlink():
            raise ValueError('不修改符号链接：' + rel)
    return p


def make_plan(root, paths):
    desired = {}
    for rel in paths:
        if not rel.startswith('PS3_GAME/USRDIR/sound/'):
            continue
        if not actual(root, rel).is_file():
            raise ValueError('资源不是文件：' + rel)
        parts = Path(rel).parts
        for n in range(3, len(parts) + 1):
            name = '/'.join(parts[:n])
            desired[name.casefold()] = name
    moves = []
    # Children first: a renamed parent subsequently carries them with it.
    for rel in sorted(desired.values(), key=lambda x: (-len(Path(x).parts), x)):
        p = actual(root, rel)
        if p.name != Path(rel).name:
            moves.append({'old': p.relative_to(root).as_posix(),
                          'new': p.with_name(Path(rel).name).relative_to(root).as_posix(),
                          'is_file': p.is_file(),
                          'canonical': rel})
    return moves


def ensure_closed():
    cmd = ['tasklist', '/FO', 'CSV', '/NH'] if os.name == 'nt' else ['ps', '-axo', 'comm=']
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    if any('rpcs3' in line.lower() for line in result.stdout.splitlines()):
        raise ValueError('请先退出 RPCS3，再修复或回退路径。')


def perform(root, moves, journal, check_process=True):
    if check_process:
        ensure_closed()
    if journal.exists():
        raise ValueError('已有修复记录；请先检查或回退，不能覆盖记录。')
    state = {'format': 1, 'root': str(root), 'status': 'prepared', 'moves': []}
    for item in moves:
        row = dict(item)
        row['temporary'] = str(Path(row['old']).with_name('.uc-case-' + uuid.uuid4().hex))
        if row['is_file']:
            row['sha256'] = digest(root / row['old'])
        state['moves'].append(row)
    write_json(journal, state)
    for row in state['moves']:
        old, temporary, new = (root / row[k] for k in ('old', 'temporary', 'new'))
        if temporary.exists() or (new.exists() and not old.samefile(new)):
            raise ValueError('目标冲突，未覆盖：' + row['new'])
        row['phase'] = 'moving_to_temporary'
        write_json(journal, state)
        old.rename(temporary)
        row['phase'] = 'moving_to_final'
        write_json(journal, state)
        temporary.rename(new)
        row['phase'] = 'done'
        write_json(journal, state)
    for row in state['moves']:
        p = root / row['canonical']
        if not p.exists() or (row['is_file'] and digest(p) != row['sha256']):
            raise ValueError('回读校验失败；保留记录供回退。')
    state['status'] = 'verified'
    write_json(journal, state)
    return state


def restore(journal, check_process=True):
    if check_process:
        ensure_closed()
    state = json.loads(journal.read_text(encoding='utf-8'))
    root = Path(state['root'])
    for row in reversed(state['moves']):
        if not row.get('phase') or row.get('phase') == 'restored':
            continue
        old, temporary, new = (root / row[k] for k in ('old', 'temporary', 'new'))
        names = {p.name: p for p in old.parent.iterdir()}
        source = names.get(temporary.name) or names.get(new.name) or names.get(old.name)
        if source is None or source.is_symlink():
            raise ValueError('回退来源丢失：' + row['old'])
        if row['is_file'] and digest(source) != row['sha256']:
            raise ValueError('文件已被另行修改，停止回退：' + row['old'])
        if source.name != old.name:
            if old.name in names:
                raise ValueError('回退目标冲突：' + row['old'])
            if source != temporary:
                source.rename(temporary)
            temporary.rename(old)
        row['phase'] = 'restored'
        write_json(journal, state)
    state['status'] = 'restored'
    write_json(journal, state)


def main():
    parser = argparse.ArgumentParser(description='UC 音频路径大小写修复；默认只检查。')
    parser.add_argument('game', nargs='?', type=Path, help='包含 PS3_GAME 的游戏目录')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', type=Path, help='回退指定修复记录')
    args = parser.parse_args()
    if args.restore:
        restore(args.restore)
        print('已回退路径，音频内容未改。')
        return
    if args.game is None:
        parser.error('请指定游戏目录')
    root = args.game.expanduser().resolve()
    sfo = actual(root, 'PS3_GAME/PARAM.SFO').read_bytes()
    if b'BLJS10154\0' not in sfo:
        raise ValueError('仅适用于 BLJS10154。')
    mapping = json.loads((HERE / 'disc-paths.json').read_text(encoding='utf-8'))
    moves = make_plan(root, mapping['paths'].values())
    print('需恢复大小写：%d 个文件/目录；不更改音频内容。' % len(moves), flush=True)
    if args.apply and moves:
        journal = root / ('UC音频路径修复记录-' + uuid.uuid4().hex[:8] + '.json')
        perform(root, moves, journal)
        print('修复完成，全部改名音频 SHA-256 回读一致。回退记录：', journal)


if __name__ == '__main__':
    main()
