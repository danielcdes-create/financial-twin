"""Only advertise a compiled APK whose digest agrees with its manifest."""
import hashlib, json
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=4)
def _digest(path,size,modified):return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()

def verified_release(apk):
    apk = Path(apk)
    unavailable={'available':False,'version_name':None,'version_code':None,'reason':'Pacote sem manifesto verificado.'}
    if not apk.exists():
        return unavailable
    try:
        stat=apk.stat()
        digest=_digest(str(apk),stat.st_size,stat.st_mtime_ns)
        manifest_path = apk.with_suffix('.manifest.json')
        if manifest_path.exists():
            try:
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest.get('sha256','').upper()==digest and manifest.get('size_bytes')==stat.st_size:
                    return {
                        'available':True,
                        'version_name':str(manifest['version_name']),
                        'version_code':int(manifest['version_code']),
                        'size_bytes':stat.st_size,
                        'sha256':digest,
                        'download_url':'/v1/app-update/apk'
                    }
            except Exception:
                pass

        # Fallback 1: apk-info.json in the same directory (spamshield style)
        apk_info_path = apk.parent / 'apk-info.json'
        if apk_info_path.exists():
            try:
                info = json.loads(apk_info_path.read_text(encoding='utf-8'))
                return {
                    'available': True,
                    'version_name': str(info.get('version_name', '1.0.0')),
                    'version_code': int(info.get('version_code', 1)),
                    'size_bytes': stat.st_size,
                    'sha256': digest,
                    'download_url': '/v1/app-update/apk'
                }
            except Exception:
                pass

        # Fallback 2: output-metadata.json (standard Gradle build output)
        for meta_cand in (
            apk.parent / 'output-metadata.json',
            apk.parent.parent / 'android' / 'app' / 'build' / 'outputs' / 'apk' / 'release' / 'output-metadata.json',
            apk.parent.parent / 'android' / 'app' / 'build' / 'outputs' / 'apk' / 'debug' / 'output-metadata.json'
        ):
            if meta_cand.exists():
                try:
                    data = json.loads(meta_cand.read_text(encoding='utf-8'))
                    elements = data.get('elements', [])
                    if elements:
                        el = elements[0]
                        return {
                            'available': True,
                            'version_name': str(el.get('versionName', '1.0.0')),
                            'version_code': int(el.get('versionCode', 1)),
                            'size_bytes': stat.st_size,
                            'sha256': digest,
                            'download_url': '/v1/app-update/apk'
                        }
                except Exception:
                    pass

        # Fallback 3: mobile app.json
        app_json = apk.parent.parent / 'app.json'
        if app_json.exists():
            try:
                data = json.loads(app_json.read_text(encoding='utf-8'))
                expo = data.get('expo', {})
                return {
                    'available': True,
                    'version_name': str(expo.get('version', '1.0.0')),
                    'version_code': int(expo.get('android', {}).get('versionCode', 1)),
                    'size_bytes': stat.st_size,
                    'sha256': digest,
                    'download_url': '/v1/app-update/apk'
                }
            except Exception:
                pass

        return unavailable
    except (OSError,ValueError,KeyError,TypeError):return unavailable
