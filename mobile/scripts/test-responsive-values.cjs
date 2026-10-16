const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript');
const React=require('react'),{act,create}=require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT=true;
const native={Platform:{OS:'web'},Text:'Text'};
function load(file){const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,{module:m,exports:m.exports,require:name=>name==='react'?React:name==='react-native'?native:load(path.resolve(path.dirname(file),name)+'.ts')});return m.exports;}
(async()=>{for(const file of ['components/SingleLineValue.tsx','components/SingleLineValue.web.tsx']){
const source=process.env.RESPONSIVE_ORIGINAL?path.join(process.env.RESPONSIVE_ORIGINAL,path.basename(file)):file;
const Value=load(source).default;let r;await act(async()=>{r=create(React.createElement(Value,{style:{fontSize:48}},'R$ 123.456.789,99'));});
const text=r.root.findByType('Text');assert.equal(text.props.children,'R$ 123.456.789,99');
assert.equal(text.props.numberOfLines,undefined,'Full amount may wrap');assert.equal(text.props.adjustsFontSizeToFit,undefined,'Do not shrink accessible font');
assert.equal(text.props.allowFontScaling,true);await act(async()=>r.unmount());}
const {compactLayout}=load('utils/responsiveLayout.ts');assert.equal(compactLayout(390,2),true);assert.equal(compactLayout(320,1),true);assert.equal(compactLayout(390,1),false);assert.equal(compactLayout(1280,2),false);
console.log('Shared financial values preserve full text and accessibility font size; narrow/font-scale layout passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
