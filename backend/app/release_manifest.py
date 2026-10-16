"""Only advertise a compiled APK whose digest agrees with its manifest."""
import hashlib, json
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=4)
def _digest(path,size,modified):return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()

def verified_release(apk):
    unavailable={'available':False,'version_name':None,'version_code':None,'reason':'Pacote sem manifesto verificado.'}
    try:
        manifest=json.loads(apk.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        stat=apk.stat();digest=_digest(str(apk),stat.st_size,stat.st_mtime_ns)
        if manifest['sha256'].upper()!=digest or manifest['size_bytes']!=stat.st_size:return unavailable
        return {'available':True,'version_name':manifest['version_name'],'version_code':int(manifest['version_code']),'size_bytes':stat.st_size,'sha256':digest,'download_url':'/v1/app-update/apk'}
    except (OSError,ValueError,KeyError,TypeError):return unavailable
