import ctypes, hashlib, json, pathlib, shutil, subprocess, sys
Path = pathlib.Path

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

if sys.argv[1] == '--probe':
    library, value, model = Path(sys.argv[2]), int(sys.argv[3]), json.loads(Path(sys.argv[4]).read_text())
    d = ctypes.CDLL(str(library))
    d.purifier_answer.argtypes = [ctypes.c_int32]
    d.purifier_term.restype = None
    assert d.purifier_init() == 1
    assert [d.purifier_answer(i) for i in (0, 1)] == [value, value+1]
    loaded = {Path(line.split()[-1]).resolve() for line in Path('/proc/self/maps').read_text().splitlines() if line.split()[-1].startswith('/')}
    origins = []
    for entry in model['haskellRuntime']:
        path = Path(entry['path'])
        origins.append(dict(path=str(path), matches=path in loaded and sha(path)==entry['sha256']))
    d.purifier_term()
    print(json.dumps(dict(values=[value,value+1], origins=origins)))
    sys.exit(0)

prefix, other, out = map(Path, sys.argv[1:])
model = json.loads((prefix/'native-products.json').read_text())
competitor = json.loads((other/'native-products.json').read_text())
root = Path(model['library']['path'])
def probe(path, value, receipt=prefix/'native-products.json'):
    return subprocess.run([sys.executable,__file__,'--probe',str(path),str(value),str(receipt)],capture_output=True,text=True,timeout=25)

rows=[]
for label, path, value, receipt in [('original',root,42,prefix/'native-products.json'),('compiled-competitor',Path(competitor['library']['path']),99,other/'native-products.json')]:
    result=probe(path,value,receipt)
    assert result.returncode==0 and not result.stderr, result
    data=json.loads(result.stdout)
    assert all(row['matches'] for row in data['origins']), data
    rows.append(dict(case=label,receipt=data))
controls=out/'controls';controls.mkdir()
needed=subprocess.check_output(['patchelf','--print-needed',str(root)],text=True).splitlines()
for marker in ['libHSpurifier-local-answer','libHSsplit-','libHSrts-']:
    names=[name for name in needed if marker in name]
    if not names:
        # The RTS may be needed by the local dependency rather than the root.
        assert marker=='libHSrts-'
        continue
    assert len(names)==1, names
    copy=controls/(marker+'.so');shutil.copyfile(root,copy);copy.chmod(0o755)
    absent=controls/'absent'/Path(names[0]).name
    subprocess.run(['patchelf','--replace-needed',names[0],str(absent),str(copy)],check=True)
    result=probe(copy,42)
    assert result.returncode and str(absent) in result.stderr and 'cannot open shared object file' in result.stderr,result
    rows.append(dict(case='missing-'+marker,stderr=result.stderr))
replacement=probe(Path(competitor['library']['path']),42)
assert replacement.returncode and 'AssertionError' in replacement.stderr, replacement
assert model['library']['sha256']!=competitor['library']['sha256']
rows.append(dict(case='genuine-compiled-replacement-rejected',stderr=replacement.stderr))
(out/'runtime-controls.json').write_text(json.dumps(rows,indent=2)+'\n')
