const {readdirSync}=require('node:fs');
const {spawnSync}=require('node:child_process');
const {join}=require('node:path');
const rootDir=join(__dirname,'..');
const preloadPath=join(__dirname,'preload-vm-guard.cjs');
const scripts=readdirSync(__dirname).filter(name=>/^test-.*\.cjs$/.test(name)).sort();
for(const script of scripts){
  const result=spawnSync(process.execPath,['-r',preloadPath,join(__dirname,script)],{stdio:'inherit',cwd:rootDir});
  if(result.error||result.status!==0){console.error(`Regression failed: ${script}`);process.exit(result.status||1);}
}
const mjsResult=spawnSync(process.execPath,['--no-warnings','--test','utils/syncQueue.test.mjs'],{stdio:'inherit',cwd:rootDir});
if(mjsResult.error||mjsResult.status!==0){console.error('Regression failed: utils/syncQueue.test.mjs');process.exit(mjsResult.status||1);}
console.log(`${scripts.length} client regression scripts + syncQueue.test.mjs passed.`);
