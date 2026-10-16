const {readdirSync}=require('node:fs');
const {spawnSync}=require('node:child_process');
const {join}=require('node:path');
const scripts=readdirSync(__dirname).filter(name=>/^test-.*\.cjs$/.test(name)).sort();
for(const script of scripts){
  const result=spawnSync(process.execPath,[join(__dirname,script)],{stdio:'inherit',cwd:join(__dirname,'..')});
  if(result.error||result.status!==0){console.error(`Regression failed: ${script}`);process.exit(result.status||1);}
}
console.log(`${scripts.length} client regression scripts passed.`);
