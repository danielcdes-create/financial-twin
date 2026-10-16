import hashlib
import importlib.util
import json
import sys
import tempfile
import subprocess
import zipfile
import unittest
from datetime import datetime
from pathlib import Path

from bson import Int64, ObjectId, json_util

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('recovery_release_pointer', 'scripts/release_pointer.py')
backup_module = load('recovery_backup', 'backend/scripts/backup_restore.py')
apk_sources = load('recovery_apk_sources', 'scripts/verify_apk_source.py')


class RecoveryOperationalTests(unittest.TestCase):
    def test_apk_source_gate_rejects_stale_bundle_dirty_head_and_native_input(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, build = parent / 'repo', parent / 'build'
            root.mkdir(); build.mkdir()
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding='utf-8')
            config = json.dumps({'expo': {'version': '1.20.33', 'android': {'versionCode': 79}}})
            write(root / 'mobile/app.json', config); write(build / 'app.json', config)
            source_paths = tuple(sorted(apk_sources.REQUIRED_SOURCES))
            for relative in source_paths: write(root / 'mobile' / relative, 'export const corrected = true;\n')
            native_paths = ('index.ts', 'android/src/main/java/expo/modules/notificationcapture/NotificationCaptureModule.kt',
                            'android/src/main/java/expo/modules/notificationcapture/NotificationCaptureService.kt',
                            'android/src/main/java/expo/modules/notificationcapture/NotificationInbox.kt')
            for relative in native_paths:
                for base in (root / 'mobile/modules/notification-capture', build / 'modules/notification-capture', build / 'node_modules/expo-notification-capture'):
                    write(base / relative, 'synthetic native input\n')
            git = lambda *args: subprocess.run(['git', '-C', str(root), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('init'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@localhost'); git('config', 'core.autocrlf', 'false')
            git('add', '.'); git('commit', '-m', 'Synthetic source reference')
            bundle = build / 'android/app/build/generated/assets/react/release/index.android.bundle'
            write(bundle, 'synthetic compiled bundle')
            source_map = build / 'android/app/build/generated/sourcemaps/react/release/index.android.bundle.map'
            write(source_map, json.dumps({'sources': list(source_paths), 'sourcesContent': ['export const corrected = true;\n'] * len(source_paths)}))
            apk = parent / 'candidate.apk'
            with zipfile.ZipFile(apk, 'w') as archive: archive.writestr('assets/index.android.bundle', bundle.read_bytes())
            self.assertEqual(apk_sources.verify(apk, build, root)['source_map_checked'], len(source_paths))
            absolute_paths = [str(root / 'mobile' / path) for path in source_paths]
            content = ['export const corrected = true;\n'] * len(source_paths)
            content[source_paths.index('app/handle-share.tsx')] = 'stale share route'
            write(source_map, json.dumps({'sources': absolute_paths, 'sourcesContent': content}))
            with self.assertRaisesRegex(ValueError, 'Embedded source'): apk_sources.verify(apk, build, root)
            content[source_paths.index('app/handle-share.tsx')] = None
            write(source_map, json.dumps({'sources': absolute_paths, 'sourcesContent': content}))
            with self.assertRaisesRegex(ValueError, 'omits local'): apk_sources.verify(apk, build, root)
            write(source_map, json.dumps({'sources': list(source_paths), 'sourcesContent': ['export const corrected = true;\n'] * len(source_paths)}))
            write(root / 'mobile/App.tsx', 'export const corrected = false;\n')
            with self.assertRaisesRegex(ValueError, 'clean'): apk_sources.verify(apk, build, root)
            git('add', '.'); git('commit', '-m', 'Changed source without rebuilding')
            with self.assertRaisesRegex(ValueError, 'Embedded source'): apk_sources.verify(apk, build, root)
            git('revert', '--no-edit', 'HEAD')
            write(build / 'node_modules/expo-notification-capture/index.ts', 'stale installed package\n')
            with self.assertRaisesRegex(ValueError, 'Native module'): apk_sources.verify(apk, build, root)

    def test_release_update_preserves_history_and_rejects_config_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / 'financial-twin-arm64.apk'
            apk.write_bytes(b'synthetic release artifact')
            info = {'version_name': '1.20.33', 'version_code': 79,
                    'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'size_bytes': apk.stat().st_size}
            manifest = root / 'financial-twin-arm64.manifest.json'
            manifest.write_text(json.dumps(info), encoding='utf-8')
            config = root / 'app.json'
            config.write_text(json.dumps({'expo': {'version': '1.20.33', 'android': {'versionCode': 79}}}))
            readme = root / 'README.md'
            old = 'Release privado atual: **1.20.26 / versionCode 72**, evidência antiga preservada.'
            readme.write_text('# FIN\n\n' + old + '\n\nOutros registros.\n', encoding='utf-8')
            checked = release.validate(manifest, config)
            release.update(readme, checked)
            release.check(readme, checked)
            first = readme.read_bytes()
            release.update(readme, checked)
            self.assertEqual(first, readme.read_bytes())
            self.assertIn(old.replace('Release privado atual:', 'Histórico preservado (registro anterior):'), readme.read_text(encoding='utf-8'))
            config.write_text(json.dumps({'expo': {'version': '1.20.32', 'android': {'versionCode': 78}}}))
            with self.assertRaises(ValueError):
                release.validate(manifest, config)
            apk.write_bytes(b'tampered')
            config.write_text(json.dumps({'expo': {'version': '1.20.33', 'android': {'versionCode': 79}}}))
            with self.assertRaises(ValueError):
                release.validate(manifest, config)

    def test_backup_preserves_int64_required_by_collection_validator(self):
        class Collection:
            def __init__(self, rows=()): self.rows = rows
            def find(self, *_): return iter(self.rows)
            def list_indexes(self): return [{'name': '_id_', 'key': {'_id': 1}}]
        row = {'_id': ObjectId(), 'legacy_row_id': Int64(999), 'at': datetime(2026, 10, 9)}
        class Database:
            def list_collections(self):
                return [{'name': 'typed', 'options': {'validator': {'$jsonSchema': {'properties': {'legacy_row_id': {'bsonType': 'long'}}}}}}]
            def __getitem__(self, _): return Collection([row])
            def __getattr__(self, _): return Collection()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'backup'
            result = backup_module.backup(Database(), destination)
            import gzip
            with gzip.open(destination / 'typed.jsonl.gz', 'rt', encoding='utf-8') as stream:
                recovered = json_util.loads(stream.readline())
            self.assertIsInstance(recovered['legacy_row_id'], Int64)
            self.assertEqual(row, recovered)
            self.assertEqual(result['collections']['typed']['count'], 1)


if __name__ == '__main__': unittest.main()
