"""Run the same isolated integration gates locally and in CI."""
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from app.config import get_settings
from app.main import configured_mongodb_uri
from app.synthetic_testing import require_synthetic_target

def run(folder):
    require_synthetic_target(configured_mongodb_uri(get_settings()))
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    os.environ['MEDIA_STORAGE_PATH']=str(folder/'synthetic-media')
    get_settings.cache_clear()
    jobs=[('check_account_security.py',None),('check_integration.py','core'),
          ('check_future_commitments.py','future.json'),('check_capture_batches.py','batches.json'),
          ('check_movement_deletion.py','deletion.json'),('check_convergence.py','convergence.json'),
          ('check_adversarial_fixes.py','A-regressions'),('check_account_profiles.py','accounts'),
          ('check_audit_1209.py','C-regressions'),('check_structural_consistency.py','structural.json'),
          ('check_structural_restart.py','restart.json'),
          ('check_android_recovery.py','android-recovery')]
    results=[]
    for script,destination in jobs:
        args=[sys.executable,str(Path(__file__).parent/script)]+([str(folder/destination)] if destination else [])
        with (folder/(script+'.log')).open('w',encoding='utf8') as log:
            result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
        results.append({'script':script,'exit':result.returncode});print(script,result.returncode,flush=True)
        (folder/'integrations.json').write_text(json.dumps(results,indent=2),encoding='utf8')
        if result.returncode:raise RuntimeError('Integration failed: '+script)

if __name__=='__main__':run(sys.argv[1])
