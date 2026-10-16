"""Build a source-only deployment archive without reading .env or financial data."""
import argparse
import hashlib
from pathlib import Path
import tarfile


def build(output: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    sources = list((root / 'backend' / 'app').rglob('*.py'))
    sources += [root / 'backend' / name for name in (
        'run_server.py', 'run_dev.py', 'requirements.txt', 'requirements.lock.txt')]
    sources += [root / 'deploy' / 'oracle' / name for name in (
        'financial-twin.service', 'fin.env.example', 'README.md')]
    entries = sorted(set(sources))
    for source in entries:
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Missing or linked source: {source.relative_to(root)}')
        if source.resolve() != source.absolute():
            raise ValueError('Linked parent directories are not allowed')
    if output.exists():
        raise FileExistsError('Choose a new output filename; existing packages are preserved')
    if output.resolve() in {source.resolve() for source in entries}:
        raise ValueError('Output cannot replace a source file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for source in entries:
                info = archive.gettarinfo(str(source), arcname=source.relative_to(root).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o644
                with source.open('rb') as content:
                    archive.addfile(info, content)
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(output.resolve()), 'files': len(entries), 'sha256': digest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    result = build(parser.parse_args().output)
    import json
    print(json.dumps(result, ensure_ascii=False))
