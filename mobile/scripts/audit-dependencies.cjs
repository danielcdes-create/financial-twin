const fs=require('node:fs'),{spawnSync}=require('node:child_process');
require('./test-node-forge-security.cjs');
require('./test-braces-security.cjs');
const result=spawnSync('pnpm',['audit','--json'],{encoding:'utf8',shell:process.platform==='win32',maxBuffer:20*1024*1024});
if(result.error)throw result.error;
let report;try{report=JSON.parse(result.stdout)}catch{throw new Error('Dependency audit did not return a valid report.');}
if(report.error)throw new Error('Dependency advisory service unavailable.');
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(report,null,2));
const findings=Object.values(report.advisories||{}),expiry=new Date('2027-11-01T00:00:00Z');
const isBuildToolPath=p=>p.includes('>@expo/cli>')||p.includes('>@expo/config>')||p.includes('>@expo/config-plugins>')||p.includes('>@expo/metro-config>')||p.includes('>@expo/fingerprint>')||p.includes('>eslint-config-expo>');
for(const advisory of findings){
 const reviewedForge=advisory.module_name==='node-forge'&&
   Array.isArray(advisory.findings)&&advisory.findings.length>0&&
   advisory.findings.every(f=>f.version==='1.4.0'&&!f.bundled&&f.paths.every(p=>p.includes('>@expo/cli>')));
 const reviewedBraces=advisory.module_name==='braces'&&advisory.github_advisory_id==='GHSA-vfj7-8cjw-p6xm'&&
   Array.isArray(advisory.findings)&&advisory.findings.length>0&&advisory.findings.every(f=>f.version==='3.0.3'&&
     Array.isArray(f.paths)&&f.paths.length>0&&f.paths.every(p=>p.endsWith('>micromatch>braces')&&
       (p.includes('>metro-file-map>')||p.includes('>@expo/metro-file-map>'))));
 const reviewedDecodeUri=advisory.module_name==='decode-uri-component'&&advisory.github_advisory_id==='GHSA-qr28-fpjm-wq7f'&&
   Array.isArray(advisory.findings)&&advisory.findings.length>0&&
   advisory.findings.every(f=>f.version==='0.5.0'&&!f.bundled&&f.paths.every(p=>p.endsWith('>query-string>decode-uri-component')));
 const reviewedBuildTool=(
   (advisory.module_name==='zod'&&advisory.github_advisory_id==='GHSA-mrhx-c7xc-m3w5'&&advisory.findings?.every(f=>f.version==='3.25.76'&&!f.bundled&&f.paths.every(isBuildToolPath)))||
   (advisory.module_name==='deepmerge'&&advisory.github_advisory_id==='GHSA-gcqx-w48m-w2h4'&&advisory.findings?.every(f=>f.version==='4.3.1'&&!f.bundled&&f.paths.every(isBuildToolPath)))||
   (advisory.module_name==='glob'&&advisory.github_advisory_id==='GHSA-3g3m-2fjr-6pw4'&&advisory.findings?.every(f=>f.version==='13.0.6'&&!f.bundled&&f.paths.every(isBuildToolPath)))||
   (advisory.module_name==='minimatch'&&advisory.github_advisory_id==='GHSA-g3hv-75wg-m249'&&advisory.findings?.every(f=>f.version==='10.2.6'&&!f.bundled&&f.paths.every(isBuildToolPath)))
 );
 if((!reviewedForge&&!reviewedBraces&&!reviewedDecodeUri&&!reviewedBuildTool)||new Date()>=expiry)throw new Error(`Unmitigated dependency advisory: ${advisory.github_advisory_id||advisory.id}.`);
 if(reviewedForge&&advisory.github_advisory_id==='GHSA-86w9-cpqp-85rv')console.log('GHSA-86w9-cpqp-85rv remains in registry metadata: installed node-forge 1.4.0 has the reviewed upstream backport and verified RSA rejection. This narrow gate exception expires 2027-11-01.');
 if(reviewedBraces)console.log('GHSA-vfj7-8cjw-p6xm remains in registry metadata: the exact braces 3.0.3 Metro dependency has the reviewed depth guard with verified parser/walker and patch hashes. This narrow gate exception expires 2027-11-01.');
}
if(result.status!==0&&!findings.length)throw new Error('Dependency audit failed without an explained advisory.');
console.log('Dependency security gate passed.');
