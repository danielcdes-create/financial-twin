"""Reject an Android candidate whose embedded sources do not match clean Git HEAD."""
import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

REQUIRED_SOURCES = {
    'App.tsx', 'app/handle-share.tsx', 'utils/moneyInput.ts', 'utils/localBack.ts', 'utils/nativeInbox.ts',
    'utils/voiceEvidence.ts', 'utils/voiceDraft.ts', 'utils/captureSubmission.ts', 'utils/syncQueue.ts',
    'utils/useClassificationOptions.ts', 'components/AccountPositions.tsx', 'components/CardManager.tsx',
    'components/CommitmentManager.tsx', 'components/FinancialLab.tsx', 'components/FutureCommitmentEditor.tsx',
    'components/RecordReview.tsx', 'components/ProvisionalReview.tsx', 'components/StatementBatchReview.tsx',
    'components/TransactionEditor.tsx', 'components/VoiceCapture.tsx', 'components/CapturePanel.tsx',
    'components/MonthClose.tsx', 'components/PurchaseCheck.tsx',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def verify(apk, build_project, root):
    apk, build_project, root = Path(apk).resolve(), Path(build_project).resolve(), Path(root).resolve()
    git = lambda *args: subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    if git('status', '--porcelain'):
        raise ValueError('Candidate requires clean, committed sources')
    commit = git('rev-parse', 'HEAD')
    config = json.loads((root / 'mobile/app.json').read_text(encoding='utf-8-sig'))['expo']
    build_config = json.loads((build_project / 'app.json').read_text(encoding='utf-8-sig'))['expo']
    if (config['version'], config['android']['versionCode']) != (build_config['version'], build_config['android']['versionCode']):
        raise ValueError('Build project and committed configuration differ')
    bundle = build_project / 'android/app/build/generated/assets/react/release/index.android.bundle'
    with zipfile.ZipFile(apk) as archive:
        embedded = archive.read('assets/index.android.bundle')
    if embedded != bundle.read_bytes():
        raise ValueError('APK bundle differs from the inspected build output')
    source_map = json.loads((build_project / 'android/app/build/generated/sourcemaps/react/release/index.android.bundle.map').read_text(encoding='utf-8'))
    tracked = {str(Path(p).relative_to('mobile')).replace('\\', '/') for p in git('ls-files', 'mobile').splitlines()}
    checked = []
    contents = source_map.get('sourcesContent', [])
    if len(contents) != len(source_map['sources']):
        raise ValueError('Build map must retain every embedded source content')
    for name, content in zip(source_map['sources'], contents):
        name = name.replace('\\', '/')
        if 'node_modules/' in name:
            continue
        # Compare whole path suffixes: 'financial-app/' is not the route 'app/'.
        relative = next((p for p in sorted(tracked, key=len, reverse=True)
                         if Path(p).suffix in ('.tsx', '.ts', '.js', '.json') and (name == p or name.endswith('/' + p))), None)
        if relative is None:
            continue
        if content is None:
            raise ValueError('Build map omits local source content: ' + relative)
        current = (root / 'mobile' / relative).read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        if current != content.removeprefix('\ufeff').replace('\r\n', '\n'):
            raise ValueError('Embedded source differs from HEAD: ' + relative)
        checked.append(relative)
    if not REQUIRED_SOURCES.issubset(checked):
        raise ValueError('Required correction sources missing from bundle: ' + str(sorted(REQUIRED_SOURCES.difference(checked))))
    native = []
    module = Path('modules/notification-capture')
    for relative in ('index.ts', 'android/src/main/java/expo/modules/notificationcapture/NotificationCaptureModule.kt',
                     'android/src/main/java/expo/modules/notificationcapture/NotificationCaptureService.kt',
                     'android/src/main/java/expo/modules/notificationcapture/NotificationInbox.kt'):
        source = root / 'mobile' / module / relative
        for base in (build_project / module, build_project / 'node_modules/expo-notification-capture'):
            if source.read_bytes() != (base / relative).read_bytes():
                raise ValueError('Native module build input differs: ' + str(base / relative))
        native.append({'path': str(module / relative).replace('\\', '/'), 'sha256': digest(source)})
    return {'source_commit': commit, 'version_name': config['version'], 'version_code': config['android']['versionCode'],
            'apk_sha256': digest(apk), 'size_bytes': apk.stat().st_size, 'bundle_sha256': hashlib.sha256(embedded).hexdigest().upper(),
            'source_map_checked': len(set(checked)), 'source_map_paths': sorted(set(checked)), 'native_inputs': native,
            'clean_head': True, 'scope': 'Embedded JS and build inputs; physical installation remains unverified.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apk', required=True, type=Path)
    parser.add_argument('--build-project', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.apk, args.build_project, args.root)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_map_paths', 'native_inputs')}, indent=2))
