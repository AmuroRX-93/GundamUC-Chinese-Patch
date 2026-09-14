import hashlib, os, unittest
import bsdiff4
import delta

class DeltaTests(unittest.TestCase):
    def check(self, old, new):
        patch = bsdiff4.diff(old, new)
        self.assertEqual(delta.apply(old, patch, len(new), hashlib.sha256(new).hexdigest()), new)
        return patch
    def test_changed_shifted_and_reordered(self):
        old = os.urandom(16000)
        self.check(old, old[5000:]+b'Chinese text'*50+old[:5000])
    def test_empty_inputs_and_identical(self):
        for old,new in [(b'',b''),(b'',b'new'),(b'old',b''),(b'abc',b'abc')]: self.check(old,new)
    def test_corruption_rejected(self):
        p=self.check(b'old', b'new')
        for q in [p[:-1],b'INVALID!'+p[8:]]:
            with self.assertRaises((ValueError, OSError, EOFError)): delta.apply(b'old',q,3,hashlib.sha256(b'new').hexdigest())
    def test_wrong_source_or_target_rejected(self):
        old=b'a'*1000;new=b'a'*900+b'b'*100;p=self.check(old,new)
        with self.assertRaises(ValueError): delta.apply(b'z'*1000,p,len(new),hashlib.sha256(new).hexdigest())
        with self.assertRaises(ValueError): delta.apply(old,p,len(new),'0'*64)
    def test_size_limit(self):
        with self.assertRaises(ValueError): delta.apply(b'',b'',delta.MAX_SEGMENT+1,'')

unittest.main()
