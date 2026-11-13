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
        return unavailable
    except (OSError,ValueError,KeyError,TypeError):return unavailable
