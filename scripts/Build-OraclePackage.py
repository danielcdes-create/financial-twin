"""Build a source-only deployment archive without reading .env or financial data."""
import argparse
import gzip
import hashlib
from pathlib import Path
import tarfile


def _normalize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    info.mode = 0o644
    info.pax_headers = {}
    return info


def build(output: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    sources = list((root / 'backend' / 'app').rglob('*.py'))
    sources += [root / 'backend' / name for name in (
        'run_server.py', 'run_dev.py', 'requirements.txt', 'requirements.lock.txt')]
    sources += [root / 'backend' / 'scripts' / name for name in (
        'reset_account_password.py', 'configure_account_security.py', 'backup_restore.py')]
    sources += [root / 'backend' / 'migrations' / name for name in (
        'backfill_descriptor_keys.py',)]
    sources += [root / 'deploy' / 'oracle' / name for name in (
        'financial-twin.service', 'fin.env.example', 'README.md')]
    sources += [root / 'deploy' / 'vps' / name for name in (
        'Dockerfile', 'docker-compose.yml')]
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
    try:
        with output.open('xb') as stream:
            with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode='w') as archive:
                    for source in entries:
                        arcname = source.relative_to(root).as_posix()
                        info = _normalize_tarinfo(archive.gettarinfo(str(source), arcname=arcname))
                        with source.open('rb') as content:
                            archive.addfile(info, content)
                    # Also expose root-level Dockerfile and docker-compose.yml for /opt/financial-twin extraction
                    for top_name in ('Dockerfile', 'docker-compose.yml'):
                        vps_file = root / 'deploy' / 'vps' / top_name
                        info = _normalize_tarinfo(archive.gettarinfo(str(vps_file), arcname=top_name))
                        with vps_file.open('rb') as content:
                            archive.addfile(info, content)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(output.resolve()), 'files': len(entries) + 2, 'sha256': digest}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    result = build(parser.parse_args().output)
    import json
    print(json.dumps(result, ensure_ascii=False))
