"""Offline, standard-library installer; validate all outputs before any replacement."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,zlib

PACKAGE=Path(__file__).resolve().parent
STATE='.uc-outline-static-20260930-transaction'
FILES=('LAUNCHDATA.BND','GAMEDATA.BHD','GAMEDATA.BDT')
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(8*1024*1024):h.update(b)
 return h.hexdigest()
def write_json(p,d):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(d),encoding='utf-8');os.replace(t,p)
def root(p):
 p=Path(p).expanduser().resolve()
 if p.name.upper()=='USRDIR':p=p.parent
 if p.name.upper()=='PS3_GAME':p=p.parent
 if not (p/'PS3_GAME/USRDIR/LAUNCHDATA.BND').is_file():raise ValueError('请选择包含 PS3_GAME 的高达 UC 游戏目录。')
 return p
def load(package=PACKAGE):
 data=(package/'manifest.json').read_bytes()
 if hashlib.sha256(data).hexdigest()!=(package/'manifest.sha256').read_text(encoding='utf-8').strip():raise ValueError('安装包清单校验失败。')
 m=json.loads(data)
 if m['format']!='UC-OUTLINE-STATIC-20260930' or tuple(x['name'] for x in m['files'])!=FILES:raise ValueError('不支持的安装清单。')
 for r in m['files']:
  if r['payload']!=r['name']+'.zlib':raise ValueError('非法补丁路径。')
 return m
def ensure_closed():
 if os.name=='nt':
  r=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,check=True)
 else:r=subprocess.run(['ps','-axo','comm='],capture_output=True,check=True)
 if any(b'rpcs3' in line.lower() for line in r.stdout.splitlines()):raise ValueError('请先退出 RPCS3，再安装。')
def check(game,m,package=PACKAGE):
 u=game/'PS3_GAME/USRDIR';statuses=[]
 for r in m['files']:
  p=u/r['name']
  if p.is_symlink() or not p.is_file():raise ValueError('目标文件不存在或为链接：'+str(p))
  h=digest(p);size=p.stat().st_size
  statuses.append('same' if r['old_sha256']==r['new_sha256']==h and r['old_size']==r['new_size']==size else 'new' if h==r['new_sha256'] and size==r['new_size'] else 'old' if h==r['old_sha256'] and size==r['old_size'] else 'unknown')
  print(r['name']+': '+statuses[-1],flush=True)
  q=package/'payload'/r['payload']
  if digest(q)!=r['payload_sha256']:raise ValueError('补丁数据损坏：'+q.name)
 if all(s in ('new','same') for s in statuses):return 'new'
 if not all(s in ('old','same') for s in statuses):raise ValueError('资源版本不匹配或混装。支持的基线请见使用说明；未修改游戏。')
 return 'old'
def recover(u):
 state=u/STATE
 if not state.exists():return
 j=state/'journal.json'
 if not j.is_file():raise ValueError('发现无日志的安装临时目录，请保留目录并联系作者。')
 info=json.loads(j.read_text(encoding='utf-8'))
 if info.get('format')!='UC-OUTLINE-STATIC-20260930':raise ValueError('未知恢复日志。')
 if info['phase']!='committed':
  for name in FILES:
   old=state/(name+'.old')
   if old.exists():os.replace(old,u/name)
 for name in FILES:
  for suffix in ('.old','.new'):(state/(name+suffix)).unlink(missing_ok=True)
 j.unlink();(state/'journal.tmp').unlink(missing_ok=True);state.rmdir()
def apply(game,m,package=PACKAGE,fault_after=None):
 u=game/'PS3_GAME/USRDIR';recover(u)
 if check(game,m,package)=='new':print('已经安装此版，无需重复安装。');return
 space=sum(r['new_size'] for r in m['files'])+64*1024*1024
 if shutil.disk_usage(u).free<space:raise ValueError('可用空间不足，至少需要约 %.2f GB 临时空间。'%(space/1e9))
 state=u/STATE;state.mkdir();journal=state/'journal.json';write_json(journal,dict(format=m['format'],phase='preparing'))
 try:
  for r in m['files']:
   p=state/(r['name']+'.new');payload=zlib.decompress((package/'payload'/r['payload']).read_bytes())
   if hashlib.sha256(payload).hexdigest()!=r['decoded_sha256']:raise ValueError('补丁解压校验失败。')
   with p.open('xb') as f:
    if r['mode']=='append':
     with (u/r['name']).open('rb') as old:shutil.copyfileobj(old,f,8*1024*1024)
    elif r['mode']!='replace':raise ValueError('未知补丁模式。')
    f.write(payload);f.flush();os.fsync(f.fileno())
   if p.stat().st_size!=r['new_size'] or digest(p)!=r['new_sha256']:raise ValueError('合成文件校验失败：'+r['name'])
  # Recheck before committing, so external edits during preparation are rejected.
  if check(game,m,package)!='old':raise ValueError('安装过程中源文件变化。')
  write_json(journal,dict(format=m['format'],phase='committing'))
  for i,r in enumerate(m['files'],1):
   name=r['name'];os.replace(u/name,state/(name+'.old'));os.replace(state/(name+'.new'),u/name)
   if fault_after==i:raise RuntimeError('injected transaction failure')
  write_json(journal,dict(format=m['format'],phase='committed'))
 except BaseException:
  recover(u);raise
 recover(u)
 print('安装完成。字幕描边资源已校验；实际游戏显示尚待验证。')
