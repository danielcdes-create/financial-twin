"""Keep the current release pointer derived from a verified manifest."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def validate(manifest_path, config_path):
    manifest_path, config_path = Path(manifest_path), Path(config_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    apk = manifest_path.with_name(manifest_path.name.replace('.manifest.json', '.apk'))
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))['expo']
    if (manifest['version_name'], int(manifest['version_code'])) != (config['version'], int(config['android']['versionCode'])):
        raise ValueError('App config and compiled release disagree')
    if apk.stat().st_size != manifest['size_bytes'] or hashlib.sha256(apk.read_bytes()).hexdigest().upper() != manifest['sha256'].upper():
        raise ValueError('APK and release digest disagree')
    return manifest


def pointer(manifest):
    version, code = manifest['version_name'], manifest['version_code']
    return f'Release privado atual: **{version} / versionCode {code}**, [APK ARM64](mobile/artifacts/financial-twin-{version}.apk). SHA256: `{manifest["sha256"].upper()}`. Registro gerado do manifesto validado; homologação física consta do relatório da entrega.'


def update(readme, manifest):
    readme = Path(readme)
    source = readme.read_text(encoding='utf-8')
    match = re.search(r'^Release privado atual:.*$', source, re.MULTILINE)
    if match is None:
        raise ValueError('README has no unique release pointer')
    if len(re.findall(r'^Release privado atual:', source, re.MULTILINE)) != 1:
        raise ValueError('README has multiple release pointers')
    replacement = pointer(manifest)
    old = match.group(0).rstrip('\r')
    if old != replacement:
        # Preserve the original dated validation narrative when advancing.
        old_version = re.search(r'\*\*([^ ]+) / versionCode (\d+)\*\*', old)
        if old_version and (old_version.group(1), int(old_version.group(2))) != (manifest['version_name'], int(manifest['version_code'])):
            replacement += '\n\n' + old.replace('Release privado atual:', 'Histórico preservado (registro anterior):', 1)
        target = readme.with_name(readme.name + '.release.tmp')
        target.write_text(source[:match.start()] + replacement + source[match.end():], encoding='utf-8')
        target.replace(readme)


def check(readme, manifest):
    lines = [line for line in Path(readme).read_text(encoding='utf-8').splitlines() if line.startswith('Release privado atual:')]
    if lines != [pointer(manifest)]:
        raise ValueError('README current release pointer differs from verified manifest')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--app-config', required=True, type=Path)
    parser.add_argument('--readme', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = validate(args.manifest, args.app_config)
    if not args.check:
        update(args.readme, manifest)
    check(args.readme, manifest)
    print('Release pointer verified')
