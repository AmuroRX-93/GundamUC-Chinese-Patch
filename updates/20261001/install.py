"""Select a fingerprinted baseline, then run the transactional installer."""
from pathlib import Path
import argparse,sys
PACKAGE=Path(__file__).resolve().parent
# Embedded/isolated Python may omit the script directory from sys.path.
# Resolve only the companion shipped with this installer, regardless of cwd.
if not (PACKAGE/'engine.py').is_file():
 print('安装文件不完整：缺少同目录的 engine.py。请完整解压安装包，或把启动修复包中的 install.py 和 engine.py 一起覆盖到这里。')
 raise SystemExit(1)
sys.path.insert(0,str(PACKAGE))
import engine as e

def select(game,package=PACKAGE):
 u=game/'PS3_GAME/USRDIR'
 observed=[(e.digest(u/n),(u/n).stat().st_size) for n in e.FILES[:2]]
 for name in ('from_20260919','from_sourcehan','from_outline_v1'):
  q=package/'variants'/name;m=e.load(q)
  for version in ('new','old'):
   expected=[(r[version+'_sha256'],r[version+'_size']) for r in m['files'][:2]]
   if observed==expected:return m,q
 raise ValueError('资源版本不匹配。支持 9月19日汉化、思源黑体/HUD更新或前一版描边包；未修改游戏。')

def main():
 p=argparse.ArgumentParser();p.add_argument('--game');g=p.add_mutually_exclusive_group();g.add_argument('--check',action='store_true');g.add_argument('--install',action='store_true');a=p.parse_args()
 try:
  path=a.game
  if not path:
   try:
    import tkinter as tk
    from tkinter import filedialog
    w=tk.Tk();w.withdraw();path=filedialog.askdirectory(title='选择高达 UC 游戏目录（含 PS3_GAME）');w.destroy()
   except Exception:path=input('游戏目录：').strip().strip('"')
  if not path:print('已取消。');return
  game=e.root(path);u=game/'PS3_GAME/USRDIR'
  if a.check:
   if (u/e.STATE).exists():raise ValueError('发现中断安装，请运行 --install 自动恢复。')
   m,q=select(game);e.check(game,m,q);return
  if not a.install and input('安装思源黑体/HUD 修复和字幕细黑描边？输入 y 继续：').strip().lower()!='y':print('已取消。');return
  e.ensure_closed();e.recover(u);m,q=select(game);e.apply(game,m,q)
 except Exception as ex:print('错误：'+str(ex));sys.exit(1)
if __name__=='__main__':main()
