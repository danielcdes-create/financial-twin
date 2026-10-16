let failures=0;
const fs=require('node:fs'),ts=require(require.resolve('typescript',{paths:[process.cwd()]}));
for(const filename of ['App.tsx',...fs.readdirSync('components').filter(f=>f.endsWith('.tsx')).map(f=>'components/'+f)]){
 const code=fs.readFileSync(filename,'utf8');const source=ts.createSourceFile(filename,code,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 function walk(node){
  if(ts.isJsxText(node)&&ts.isJsxElement(node.parent)&&['View','Pressable','ScrollView','SafeAreaView','KeyboardAvoidingView'].includes(node.parent.openingElement.tagName.getText(source))){
   const raw=node.getText(source);
   if(raw.trim()||(!/[\r\n]/.test(raw)&&raw.length)){
    failures++;console.log(filename+':'+(source.getLineAndCharacterOfPosition(node.getStart()).line+1)+' Raw text inside a native layout container: '+JSON.stringify(raw));
   }
  }
  if(ts.isJsxOpeningElement(node)||ts.isJsxSelfClosingElement(node)){
  const name=node.tagName.getText(source),props=node.attributes.properties.filter(ts.isJsxAttribute).map(p=>p.name.text);
  if(name==='TextInput'&&!props.includes('accessibilityLabel')&&!props.includes('aria-labelledby')){failures++;console.log(filename+':'+(source.getLineAndCharacterOfPosition(node.getStart()).line+1)+' '+node.getText(source).slice(0,160));}
  if(name==='Pressable'&&props.filter(p=>p==='accessibilityRole').length>1){failures++;console.log('DUPLICATE ROLE '+filename+':'+(source.getLineAndCharacterOfPosition(node.getStart()).line+1));}
 }ts.forEachChild(node,walk)}walk(source);
}


if(failures)throw new Error(`${failures} accessibility or native text-container violations.`);
console.log("All editable inputs have accessible names; actions have unique roles; native layout containers have no literal text.");
