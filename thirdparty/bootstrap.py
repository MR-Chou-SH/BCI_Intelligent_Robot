"""Snapshot local upstream sources; optionally install into their OWN environments."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['smolvla', 'openpi', 'discodemo'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--locked', action='store_true', help='Use the verified SmolVLA dependency lock')
    parser.add_argument('--torch-backend', default='cu128', help='Default CUDA12.8; cpu must be explicitly requested')
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    defaults = {'smolvla': Path.home()/'Robot-Arm/lerobot',
                'openpi': ROOT/'.cache/upstream/dexjoco/openpi',
                'discodemo': ROOT/'.cache/upstream/DiscoDemo'}
    source = (args.source or defaults[args.component]).resolve()
    target = ROOT / 'thirdparty' / args.component
    upstream = target / 'upstream'
    if not (source / 'pyproject.toml').is_file():
        raise FileNotFoundError('Missing upstream source: ' + str(source))
    if not upstream.exists():
        target.mkdir(parents=True, exist_ok=True)
        upstream.mkdir()
        if args.component == 'discodemo':
            package = source/'libs/FlashSAC'
            shutil.copytree(package/'flash_rl', upstream/'flash_rl',
                            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            shutil.copytree(package/'configs', upstream/'configs')
            for name in ('LICENSE','NOTICE','README.md'):
                if (source/name).is_file():shutil.copy2(source/name,upstream/name)
            shutil.copy2(package/'pyproject.toml',upstream/'pyproject.toml')
        elif args.component == 'openpi':
            # The local OpenPI tree is small; preserve its package/client layout.
            shutil.copytree(source, upstream, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '*.pyc'))
        else:
            shutil.copytree(source / 'src', upstream / 'src',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ['pyproject.toml', 'setup.py', 'README.md', 'LICENSE', 'NOTICE']:
                if (source / name).is_file():
                    shutil.copy2(source / name, upstream / name)
        files = {str(p.relative_to(upstream)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(upstream.rglob('*')) if p.is_file()}
        (target / 'source_receipt.json').write_text(json.dumps({'source': str(source), 'files': files}, indent=2)+'\n')
    receipt = json.loads((target / 'source_receipt.json').read_text())
    for name, sha in receipt['files'].items():
        if hashlib.sha256((upstream / name).read_bytes()).hexdigest() != sha:
            raise ValueError('Upstream snapshot changed: ' + name)
    # Complete snapshots prepared by the initial bootstrap before setup.py was
    # included. Add only the missing upstream build file; never overwrite code.
    if args.component == 'smolvla' and 'setup.py' not in receipt['files']:
        if (upstream/'setup.py').exists():
            raise ValueError('Untracked upstream setup.py; inspect before installation')
        shutil.copy2(source/'setup.py', upstream/'setup.py')
        receipt['files']['setup.py'] = hashlib.sha256((upstream/'setup.py').read_bytes()).hexdigest()
        (target/'source_receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    if args.install:
        uv = shutil.which('uv')
        if not uv:
            raise RuntimeError('uv is required; no automatic system installation')
        env = target / '.venv'
        if not env.exists():
            subprocess.run([uv, 'venv', '--python', args.python, str(env)], check=True)
        python = str(env / 'bin/python')
        flags = ['--offline'] if args.offline else []
        if args.component == 'smolvla':
            requirements = target / ('requirements.lock.txt' if args.locked else 'requirements.txt')
            subprocess.run([uv, 'pip', 'install', *flags, '--python', python,
                            '--torch-backend', args.torch_backend,
                            '-r', str(requirements)], check=True)
            subprocess.run([uv, 'pip', 'install', *flags, '--python', python,
                            '--no-deps', '--no-build-isolation', '-e', str(upstream)], check=True)
        elif args.component=='openpi':
            raise RuntimeError('OpenPI source is prepared; GPU training installation is deferred. See README.')
        else:
            raise RuntimeError('DiscoDemo uses the project .venv; install with setup_kitchen_environment.sh --with-rl')
    print(json.dumps({'component': args.component, 'source_snapshot': str(upstream), 'installed': args.install}))


if __name__ == '__main__':
    main()
