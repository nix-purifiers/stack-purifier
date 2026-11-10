# SPDX-License-Identifier: MIT
"""Install and validate selected native Cabal foreign-library runtime closures."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys


def command(*args, **kwargs):
    return subprocess.check_output([str(a) for a in args], text=True, **kwargs).strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dynamic_exports(path):
    # Read only the required ELF dynamic symbol table. GNU nm's BFD reader
    # also interprets unrelated ARM attributes on these native GHC outputs.
    rows = [line.split() for line in command('readelf', '--dyn-syms', '--wide', path).splitlines()]
    return {row[7] for row in rows if len(row) >= 8 and row[0].endswith(':')
            and row[0][:-1].isdigit() and row[6] != 'UND'
            and row[4] in {'GLOBAL', 'WEAK'} and row[5] in {'DEFAULT', 'PROTECTED'}}


def elf(path, system):
    data = path.read_bytes()
    require(data[:6] == b'\x7fELF\x02\x01', f'not a little-endian ELF64 library: {path}')
    kind, machine = struct.unpack_from('<HH', data, 16)
    expected = {'x86_64-linux': 62, 'aarch64-linux': 183}.get(system)
    require(expected is not None and machine == expected and kind == 3,
            f'not a native shared object for {system}: {path}')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    # GHC's genuine foreign-library output has PT_INTERP on this SDK.
    # Its zero entry point and absence of DF_1_PIE distinguish it from PIE.
    require(struct.unpack_from('<Q', data, 24)[0] == 0, f'executable entry point in foreign library: {path}')
    for i in range(count):
        header = phoff + i * size
        if struct.unpack_from('<I', data, header)[0] == 2:
            offset = struct.unpack_from('<Q', data, header + 8)[0]
            length = struct.unpack_from('<Q', data, header + 32)[0]
            for pos in range(offset, offset + length, 16):
                tag, value = struct.unpack_from('<QQ', data, pos)
                require(not (tag == 0x6FFFFFFB and value & 0x08000000),
                        f'PIE executable is not a foreign library: {path}')


def loader_map(path, ldd):
    env = {'PATH': os.environ['PATH'], 'LC_ALL': 'C'}
    text = command(ldd, path, env=env, stderr=subprocess.STDOUT)
    require('not found' not in text, f'missing runtime dependency of {path}: {text}')
    result = {}
    for line in text.splitlines():
        match = re.match(r'\s*(\S+) => (/\S+) \(', line)
        if match:
            result[match[1]] = Path(match[2]).resolve(strict=True)
        else:
            direct = re.match(r'\s*(/\S+) \(', line)
            if direct:
                path = Path(direct[1]).resolve(strict=True)
                result[path.name] = path
    return result


def main(out, spec_path, inspect, setup, ldd):
    out=Path(out).resolve();spec=json.loads(Path(spec_path).read_text());project=out/'project';models=[]
    for relative in spec['profile']['projects']:
        package=(project/relative).resolve(strict=True)
        require(package.is_relative_to(project), 'Stack project escapes retained source')
        args=['stack','--no-nix','--stack-yaml',str(out/'session/stack.yaml')]
        dist=Path(command(*args,'path','--dist-dir',cwd=package))
        if not dist.is_absolute():dist=package/dist
        dist=dist.resolve(strict=True)
        require(dist.is_relative_to(package), 'native Cabal configuration escapes its package')
        destination=out/('configured-'+str(len(models))+'.json')
        subprocess.run(['runghc','-package=Cabal',str(inspect),str(dist),'-',str(destination)],cwd=package,check=True)
        model=json.loads(destination.read_text())
        require(model['buildType']=='Simple','nativeProducts currently requires Simple local Cabal packages')
        subprocess.run(['runghc','-package=Cabal',str(setup),'copy','--builddir='+str(dist)],cwd=package,check=True)
        model['sourceRoot']=str(package);models.append(model)
    key,product=next(iter(spec['products'].items()))
    candidates=[(m,f) for m in models for f in m['foreignLibraries'] if f['selector']==product['selector']]
    require(len(candidates)==1,'selected native foreign-library owner missing or ambiguous')
    model,native=candidates[0]
    require(model['compiler']=='ghc-'+spec['profile']['ghc'],'actual native GHC differs from captured Stack compiler')
    require(model['platform']==spec['system'],'native Cabal platform differs from target')
    require(native['type']=='ForeignLibNativeShared','selected native product is not a Cabal shared foreign library')
    require(native['versionInfo']=='Nothing' and native['versionLinux']=='Nothing','initial Stack nativeProducts contract requires an unversioned native foreign library')
    filename='lib'+native['component']+'.so'
    built=(Path(model['sourceRoot'])/native['nativeBuildDirectory']/filename).resolve(strict=True)
    installed=(Path(native['nativeLibraryDirectory'])/filename).resolve(strict=True)
    require(built.is_relative_to(project) and installed.is_relative_to(out),'native foreign library escaped retained build/install trees')
    elf(installed,spec['system'])
    require(set(product['exports'])<=dynamic_exports(installed),'native installed library lacks declared C exports')
    symbols=[line.split() for line in command('readelf','--dyn-syms','--wide',installed).splitlines()]
    funcs={r[7] for r in symbols if len(r)>=8 and r[3]=='FUNC' and r[6]!='UND'}
    require({product['runtime']['init'],product['runtime']['term']}<=funcs,'GHC init/term must be native dynamic functions')
    headers=native['installIncludes'];require(headers,'native foreign library must declare install-includes for its C API')
    (out/'lib').mkdir();(out/'include').mkdir()
    target=out/'lib'/filename;shutil.copyfile(installed,target)
    header_evidence=[]
    for name in headers:
        relative=Path(name);require(not relative.is_absolute() and '..' not in relative.parts,'escaping native public header')
        original=(Path(native['nativeIncludeDirectory'])/relative).resolve(strict=True)
        require(original.is_relative_to(out),'native installed header escaped output')
        destination=out/'include'/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,destination)
        header_evidence.append({'path':name,'source':str(original),'sha256':digest(original)})
    runtime=[u for u in native['units'] if u['package'].startswith('rts-')]
    require(len(runtime)==1 and runtime[0]['includeDirs'],'actual native GHC RTS headers are absent')
    header_roots=[Path(p).resolve(strict=True) for p in runtime[0]['includeDirs'] if (Path(p)/'HsFFI.h').is_file()]
    require(len(header_roots)==1,'actual registered RTS must own one HsFFI.h include directory')
    sdk_headers=header_roots[0]
    require(str(sdk_headers).startswith('/nix/store/'),'GHC RTS headers are outside immutable SDK')
    shutil.copytree(sdk_headers,out/'include/ghc',symlinks=False)
    loaded=loader_map(installed,ldd);haskell=[];support=[]
    for name,path in loaded.items():
        require(str(path).startswith('/nix/store/') or path.is_relative_to(out),'ambient runtime dependency: '+str(path))
        if name.startswith('libHS'):
            owners=[u for u in native['units'] if any(re.fullmatch('lib'+re.escape(hs)+'(?:-ghc'+re.escape(spec['profile']['ghc'])+')?\\.so',name) for hs in u['hsLibraries']) and any(path.parent==Path(d).resolve() for d in u['dynamicLibraryDirs']+u['libraryDirs'])]
            require(len(owners)==1,'loaded Haskell library lacks unique native configured dependency owner: '+name)
            haskell.append({'soname':name,'path':str(path),'sha256':digest(path),'unit':owners[0]['unit'],'package':owners[0]['package']})
        else:support.append({'soname':name,'path':str(path),'sha256':digest(path)})
    result={'kind':'native-Stack-Cabal-configured-foreign-library','product':product,'system':spec['system'],'compiler':model['compiler'],'owner':native,'sourceRoot':model['sourceRoot'],'nativeBuild':{'path':str(built),'sha256':digest(built)},'nativeInstall':{'path':str(installed),'sha256':digest(installed)},'library':{'path':str(target),'sha256':digest(target)},'headers':header_evidence,'sdkHeaders':str(sdk_headers),'haskellRuntime':haskell,'hostSupport':support}
    (out/'native-products.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')

if __name__=='__main__':main(*sys.argv[1:])
