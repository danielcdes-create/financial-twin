// Version-only release: reuse Expo's own transforms for an existing native tree.
// Native plugin/dependency changes still require a full Expo prebuild.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'..'));
const config=JSON.parse(fs.readFileSync(path.join(root,'app.json'),'utf8')).expo;
const {setVersionCode,setVersionName}=require(path.join(root,'node_modules/@expo/config-plugins/build/android/Version.js'));
const gradle=path.join(root,'android/app/build.gradle');
assert.ok(fs.existsSync(gradle),'Run Expo prebuild before syncing the native version.');
const old=fs.readFileSync(gradle,'utf8');
assert.match(old,/versionCode \d+/);assert.match(old,/versionName "[^"]+"/);
const next=setVersionName(config,setVersionCode(config,old));
assert.ok(next.includes(`versionCode ${config.android.versionCode}`));
assert.ok(next.includes(`versionName "${config.version}"`));
fs.writeFileSync(gradle,next);console.log(`Android version synchronized: ${config.version} / ${config.android.versionCode}`);
