#!/usr/bin/env python3
"""Capture a genuine native Stack foreign-library profile on each CI runner."""
import json, os, pathlib, shutil, subprocess, tempfile
Path = pathlib.Path
repo = Path(__file__).resolve().parents[1]
def run(*args, **kw):
    return subprocess.check_output(list(map(str,args)),text=True,**kw).strip()
with tempfile.TemporaryDirectory(prefix='stack-native-capture-') as temporary:
    temp=Path(temporary)
    src=temp/'source'
    shutil.copytree(repo/'tests/fixtures/foreign-library',src)
    cli=Path(run('nix','build',str(repo)+'#cli','--no-link','--print-out-paths'))
    env=dict(os.environ)
    env.setdefault('XDG_CACHE_HOME',str(temp/'cache'))
    subprocess.run([str(cli/'bin/stack-purifier'),'--project',str(src),'lock','--update'],env=env,check=True)
    lock=json.loads((src/'stack-purifier.lock.json').read_text())
    system=run('nix','eval','--impure','--raw','--expr','builtins.currentSystem')
    profile=lock['profiles'][system]
    assert set(profile['projects'])=={'.','sibling'},profile
    assert {'stack-foreign-proof.cabal','sibling/purifier-local-answer.cabal'}<=set(lock['manifests'])
    other=temp/'competitor';shutil.copytree(src,other)
    implementation=other/'sibling/src/LocalAnswer.hs'
    implementation.write_text(implementation.read_text().replace('localAnswer = 42','localAnswer = 99'))
    expression='let f=builtins.getFlake '+json.dumps(str(repo))+'; pkgs=import f.inputs.nixpkgs {system='+json.dumps(system)+';overlays=[f.overlays.default];}; in import '+str(repo/'tests/native-products.nix')+' {inherit pkgs;src=/. + '+json.dumps(str(src))+';competitorSrc=/. + '+json.dumps(str(other))+';}'
    output=run('nix','build','--impure','--no-link','--print-out-paths','--expr',expression)
    subprocess.run(['nix','build','--impure','--no-link','--rebuild','--expr',expression],check=True)
    product=dict(abi='c',selector='stack-foreign-proof:flib:answer',exports=['purifier_answer','purifier_init','purifier_term'],runtime=dict(kind='ghc',lifecycle='process-once',threading='single-foreign-thread',init='purifier_init',term='purifier_term'))
    for field, value, error in [('selector','missing:flib:answer','selected native foreign-library owner missing or ambiguous'),('exports',product['exports']+['missing_symbol'],'native installed library lacks declared C exports')]:
        invalid=dict(product);invalid[field]=value
        declaration='builtins.fromJSON '+json.dumps(json.dumps(invalid))
        negative='let f=builtins.getFlake '+json.dumps(str(repo))+'; p=import f.inputs.nixpkgs {system='+json.dumps(system)+';overlays=[f.overlays.default];}; in (p.stack-purifier.buildStackProject {src=/. + '+json.dumps(str(src))+';nativeProducts.answer='+declaration+';}).packages.native'
        result=subprocess.run(['nix','build','--impure','--no-link','--expr',negative],text=True,capture_output=True)
        assert result.returncode and error in result.stderr,result
        print(result.stderr)
    receipt=dict(system=system,projects=profile['projects'],consumerOutput=output)
    if os.environ.get('STACK_NATIVE_CAPTURE_EVIDENCE'):
        evidence=Path(os.environ['STACK_NATIVE_CAPTURE_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
        for name in ['stack.yaml','stack.yaml.lock','stack-purifier.lock.json','stack-foreign-proof.cabal','sibling/purifier-local-answer.cabal']:
            destination=evidence/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src/name,destination)
        (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))
