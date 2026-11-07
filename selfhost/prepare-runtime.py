"""Prepare an owner image from exact tested wheel bytes and public locked deps."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-dirty-prototype',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    wheel=args.wheel.resolve();out=args.output.resolve()
    subprocess.run([sys.executable,str(root/'scripts/verify_wheel.py'),str(wheel.parent)],check=True,cwd=root)
    clean=not subprocess.check_output(['git','status','--porcelain'],text=True,cwd=root).strip()
    if not clean and not args.allow_dirty_prototype:raise ValueError('Clean source is required')
    if out.exists():raise ValueError('Use a fresh context directory')
    requirements=subprocess.check_output(['uv','export','--frozen','--no-dev','--extra','http',
        '--no-emit-project','--format','requirements-txt'],text=True,cwd=root)
    if any(value in requirements for value in ('git+',' @ ','file://','--index-url','--extra-index-url')):
        raise ValueError('Runtime dependencies must use public hash-locked registry distributions')
    out.mkdir(parents=True);(out/'wheels').mkdir()
    shutil.copy2(wheel,out/'wheels'/wheel.name)
    requirements+='\n/wheels/'+wheel.name+' --hash=sha256:'+sha(wheel)+'\n'
    (out/'requirements.txt').write_text(requirements)
    resources={'selfhost/entrypoint.py':'selfhost/entrypoint.py',
        'owner-migrations/migrate.py':'src/controlplane_events/migrate.py',
        'owner-migrations/migrations/0001.sql':'src/controlplane_events/migrations/0001.sql'}
    for destination,source in resources.items():
        path=out/destination;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/source,path)
    receipt={'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=root).strip(),
        'source_clean':clean,'service_wheel':wheel.name,'wheel_sha256':{wheel.name:sha(wheel)},
        'requirements_sha256':sha(out/'requirements.txt'),
        'source_resource_sha256':{destination:sha(root/source) for destination,source in resources.items()}}
    (out/'runtime-receipt.json').write_text(json.dumps(receipt,sort_keys=True)+'\n')
    print(json.dumps({'context_prepared':True,'source_sha':receipt['source_sha'],'source_clean':clean}))


if __name__=='__main__':main()
