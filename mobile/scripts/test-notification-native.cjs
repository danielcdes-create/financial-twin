const {spawnSync}=require('node:child_process');
const {mkdtempSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
if(process.platform!=='win32'){console.log('SKIP compiled Kotlin fixtures: this local probe requires Windows Android Studio/Gradle runtime; Android build remains a separate gate.');process.exit(0);}
const result=spawnSync('powershell',['-NoProfile','-File',join(process.cwd(),'scripts','test-notification-native.ps1'),'-OutputDirectory',mkdtempSync(join(tmpdir(),'fin-notification-'))],{stdio:'inherit'});
if(result.error)throw result.error;
process.exitCode=result.status||0;
