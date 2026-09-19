import bsdiff4
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install.py'))
uc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uc)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def put(root, name, data):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def make_sfo(title='BLJS10154'):
    names = b'TITLE_ID\0APP_VER\0'
    vals = title.encode() + b'\0' + b'01.00\0'
    entries = struct.pack('<HHIII', 0, 0x204, 10, 10, 0) + struct.pack('<HHIII', 9, 0x204, 6, 6, 10)
    return struct.pack('<4s4I', b'\0PSF', 0x101, 52, 52+len(names), 2) + entries + names + vals


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='uc-portable-test-')
        self.root = Path(self.tmp.name).resolve()
        self.game = self.root / '游戏 A'
        self.dlc = self.root / 'DLC B'
        self.pkg = self.root / '独立包'
        self.pkg.mkdir()
        self.sfo = put(self.game, 'PS3_GAME/PARAM.SFO', make_sfo())
        put(self.dlc, 'PARAM.SFO', make_sfo())
        self.roots = {'base': self.game, 'dlc': self.dlc}
        old = b'original resource'; new = b'translated resource'
        self.target = put(self.game, 'PS3_GAME/USRDIR/LAUNCHDATA.BND', old)
        put(self.pkg, 'payload/launch', new)
        br = dict(path='PS3_GAME/USRDIR/LAUNCHDATA.BND', payload='payload/launch', original_size=len(old), original_md5=hashlib.md5(old).hexdigest(), size=len(new), sha256=sha(new))
        prefix = b'original prefix!'
        part = b'new binder'
        self.bdt = put(self.game, 'PS3_GAME/USRDIR/GAMEDATA.BDT', prefix)
        put(self.pkg, 'payload/append', part)
        bd = dict(path='PS3_GAME/USRDIR/GAMEDATA.BDT', original_size=len(prefix), original_md5=hashlib.md5(prefix).hexdigest(), size=len(prefix+part), sha256=sha(prefix+part), append=[dict(payload='payload/append', offset=len(prefix), size=len(part), sha256=sha(part))])
        drs=[]
        for name in ('USRDIR/mission/test.bhd.edat', 'USRDIR/mission/test.bdt.edat', 'USRDIR/system/packagelist/test.plist.edat'):
            old = b'original '+name.encode(); new=b'translated '+name.encode()
            put(self.dlc, name, old); put(self.pkg, 'payload/'+name, new)
            drs.append(dict(path=name,payload='payload/'+name,size=len(new),sha256=sha(new),accepted_sha256=[sha(old),sha(new)]))
        self.m=dict(format=1,release='test',game='BLJS10154',version='01.00',base=[br,bd],unchanged=[dict(path='PS3_GAME/PARAM.SFO',size=len(make_sfo()),md5=hashlib.md5(make_sfo()).hexdigest())],dlc=drs,dlc_groups=[dict(path=drs[2]['path'],requires=[r['path'] for r in drs[:2]])])
        self.m['format'] = 2
        for scope in ('base', 'dlc'):
            for r in self.m[scope]:
                old = (self.roots[scope] / r['path']).read_bytes()
                parts = r.pop('append', None)
                if parts:
                    r['prefix_size'] = len(old)
                    rows = [(part['payload'], part['offset']) for part in parts]
                else:
                    rows = [(r.pop('payload'), 0)]
                r['segments'] = []
                for name, offset in rows:
                    new = (self.pkg / name).read_bytes()
                    data = bsdiff4.diff(old, new)
                    (self.pkg / name).write_bytes(data)
                    r['segments'].append(dict(payload=name, old_offset=0, old_size=len(old), output_offset=offset,
                        size=len(new), sha256=sha(new), patch_size=len(data), patch_sha256=sha(data)))
        self.initial={p:p.read_bytes() for root in (self.game,self.dlc) for p in root.rglob('*') if p.is_file()}
        self.closed=patch.object(uc,'ensure_closed')
        self.closed.start()

    def tearDown(self):
        self.closed.stop()
        self.tmp.cleanup()

    def install(self, roots=None):
        roots=roots or self.roots
        changes=uc.plan(self.pkg,self.m,roots)
        return uc.apply(self.pkg,self.m,roots,changes)

    def unchanged(self):
        for p,b in self.initial.items():
            self.assertEqual(p.read_bytes(),b,str(p))

    def test_clean_install_restore_and_repeat(self):
        backup=self.install()
        self.assertEqual(uc.plan(self.pkg,self.m,self.roots),[])
        uc.restore(backup)
        self.unchanged()
        uc.restore(backup)
        self.unchanged()

    def test_base_only(self):
        backup=self.install({'base':self.game})
        self.assertTrue(backup.exists())
        for p,b in self.initial.items():
            if self.dlc in p.parents:self.assertEqual(p.read_bytes(),b)
        uc.restore(backup);self.unchanged()

    def test_partial_dlc_and_paired_archive(self):
        for r in self.m['dlc']:(self.dlc/r['path']).unlink()
        # No installed DLC resources is a supported subset; missing content isn't created.
        backup=self.install()
        for r in self.m['dlc']:self.assertFalse((self.dlc/r['path']).exists())
        uc.restore(backup)

    def test_missing_dependency_rejected(self):
        (self.dlc/self.m['dlc'][0]['path']).unlink()
        with self.assertRaisesRegex(ValueError,'不完整'):uc.plan(self.pkg,self.m,self.roots)
        self.assertEqual(self.target.read_bytes(),self.initial[self.target])

    def test_wrong_game(self):
        self.sfo.write_bytes(make_sfo('BLJS99999'))
        with self.assertRaisesRegex(ValueError,'版本不符'):uc.plan(self.pkg,self.m,self.roots)
        self.assertEqual(self.target.read_bytes(),self.initial[self.target])

    def test_corrupt_payload_rejected(self):
        (self.pkg/'payload/launch').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'损坏'):uc.plan(self.pkg,self.m,self.roots)
        self.unchanged()

    def test_unknown_dlc_hash_rejects_entire_install(self):
        (self.dlc/self.m['dlc'][0]['path']).write_bytes(b'unknown patch')
        with self.assertRaisesRegex(ValueError,'不受支持'):uc.plan(self.pkg,self.m,self.roots)
        self.assertEqual(self.target.read_bytes(),self.initial[self.target])

    def test_game_update_overlay_rejected(self):
        put(self.dlc, 'USRDIR/EBOOT.BIN', b'unrecognized update')
        with self.assertRaisesRegex(ValueError, '覆盖资源'):uc.plan(self.pkg,self.m,self.roots)
        self.assertEqual(self.target.read_bytes(),self.initial[self.target])

    def test_verified_101_overlay_preserved(self):
        data = b'fixture-known-update'
        eb = put(self.dlc, 'USRDIR/EBOOT.BIN', data)
        (self.dlc/'PARAM.SFO').write_bytes(make_sfo().replace(b'01.00', b'01.01'))
        self.m['update_profiles'] = [dict(app_version='01.01', files=[dict(path='USRDIR/EBOOT.BIN', size=len(data), sha256=sha(data))])]
        backup = self.install()
        self.assertEqual(eb.read_bytes(), data)
        uc.restore(backup)
        self.assertEqual(eb.read_bytes(), data)
        eb.write_bytes(b'other-update')
        with self.assertRaisesRegex(ValueError, '覆盖资源'):
            uc.plan(self.pkg, self.m, self.roots)

    def test_missing_101_executable_rejected(self):
        (self.dlc/'PARAM.SFO').write_bytes(make_sfo().replace(b'01.00', b'01.01'))
        with self.assertRaisesRegex(ValueError, '不完整'):
            uc.plan(self.pkg, self.m, self.roots)

    def test_alternate_dlc_envelope_roundtrip(self):
        row = self.m['dlc'][0]
        source = b'same-plaintext-different-edat-envelope'
        target = b'translated-alternate-envelope'
        (self.dlc/row['path']).write_bytes(source)
        patch_data = bsdiff4.diff(source, target)
        name = 'payload/alternate-dlc'
        put(self.pkg, name, patch_data)
        row['input_variants'] = [dict(size=len(target),sha256=sha(target),accepted_sha256=[sha(source)],segments=[dict(payload=name,old_offset=0,old_size=len(source),output_offset=0,size=len(target),sha256=sha(target),patch_size=len(patch_data),patch_sha256=sha(patch_data))])]
        backup = self.install()
        self.assertEqual((self.dlc/row['path']).read_bytes(), target)
        self.assertEqual(uc.plan(self.pkg,self.m,self.roots), [])
        uc.restore(backup)
        self.assertEqual((self.dlc/row['path']).read_bytes(), source)

    def test_mixed_case_paths_keep_original_spelling(self):
        renamed = self.target.with_name('LaunchData.bnd')
        temp = self.target.with_name('rename-stage')
        self.target.rename(temp);temp.rename(renamed)
        self.assertEqual(uc.safe(self.game,'ps3_game/usrdir/LAUNCHDATA.BND').name, 'LaunchData.bnd')
        backup = self.install()
        self.assertEqual(uc.safe(self.game,'PS3_GAME/USRDIR/launchdata.bnd').name,'LaunchData.bnd')
        uc.restore(backup)
        self.assertEqual(renamed.read_bytes(), self.initial[self.target])

    def test_previous_chinese_release_forward_update(self):
        row = self.m['base'][0]
        previous = b'previous-published-chinese-resource'
        latest = b'latest-chinese-resource-with-restyled-fonts'
        self.target.write_bytes(previous)
        delta = bsdiff4.diff(previous,latest)
        name='payload/from-previous-release'
        put(self.pkg,name,delta)
        row['input_variants']=[dict(accepted_sha256=[sha(previous)],size=len(latest),sha256=sha(latest),segments=[dict(payload=name,old_offset=0,old_size=len(previous),output_offset=0,size=len(latest),sha256=sha(latest),patch_size=len(delta),patch_sha256=sha(delta))])]
        backup=self.install()
        self.assertEqual(self.target.read_bytes(),latest)
        self.assertEqual(uc.plan(self.pkg,self.m,self.roots),[])
        uc.restore(backup)
        self.assertEqual(self.target.read_bytes(),previous)

    def test_unknown_base_bdt_not_blindly_appended(self):
        self.bdt.write_bytes(self.bdt.read_bytes()+b'other patch')
        with self.assertRaisesRegex(ValueError,'不受支持'):uc.plan(self.pkg,self.m,self.roots)

    def test_write_failure_rolls_back(self):
        original=uc.os.replace
        fail=[True]
        def injected(a,b):
            if Path(b)==self.bdt and fail[0]:
                fail[0]=False
                raise OSError('injected write failure')
            return original(a,b)
        with patch.object(uc.os,'replace',injected):
            with self.assertRaisesRegex(OSError,'injected'):self.install()
        self.unchanged()
        self.assertFalse((self.game/uc.LOCK).exists())

    def test_restore_refuses_other_modifications(self):
        backup=self.install();self.target.write_bytes(b'another patch')
        before=self.bdt.read_bytes()
        with self.assertRaisesRegex(ValueError,'其他补丁'):uc.restore(backup)
        self.assertEqual(self.bdt.read_bytes(),before)

    def test_space_failure_before_changes(self):
        with patch.object(uc.shutil,'disk_usage',return_value=type('DU',(),{'free':0})()):
            with self.assertRaisesRegex(ValueError,'空间不足'):self.install()
        self.unchanged()

    def test_traversal_and_symlink_rejected(self):
        for name in ('../x','/tmp/a','USRDIR/C:\\test'):
            with self.assertRaises(ValueError):uc.safe(self.game,name)
        if os.name!='nt':
            (self.game/'outside').symlink_to(self.pkg,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'符号链接'):uc.safe(self.game,'outside/payload/launch')

    def test_sudden_process_exit_can_be_restored(self):
        fixture=self.root/'fixture.json';fixture.write_text(json.dumps(self.m))
        script='''import importlib.util,json,os,pathlib,sys
sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parent))
spec=importlib.util.spec_from_file_location('uc',sys.argv[1]);uc=importlib.util.module_from_spec(spec);spec.loader.exec_module(uc)
root=pathlib.Path(sys.argv[2]);m=json.loads((root/'fixture.json').read_text());roots={'base':root/'游戏 A','dlc':root/'DLC B'}
uc.ensure_closed=lambda:None
orig=uc.os.replace
def interrupted(a,b):
 orig(a,b)
 if pathlib.Path(b)==roots['base']/'PS3_GAME/USRDIR/LAUNCHDATA.BND':os._exit(77)
uc.os.replace=interrupted
uc.apply(root/'独立包',m,roots,uc.plan(root/'独立包',m,roots))
'''
        result=subprocess.run([sys.executable,'-c',script,str(Path(uc.__file__).resolve()),str(self.root)],capture_output=True)
        self.assertEqual(result.returncode,77,result.stderr)
        lock=json.loads((self.game/uc.LOCK).read_text())
        with self.assertRaisesRegex(ValueError,'未恢复'):uc.plan(self.pkg,self.m,self.roots)
        uc.restore(Path(lock['backup']));self.unchanged()


if __name__=='__main__':unittest.main(verbosity=2)
