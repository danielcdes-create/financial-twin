/* global __dirname */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const braces=require('braces'),packagePath=path.dirname(require.resolve('braces/package.json'));
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
assert.equal(require('braces/package.json').version,'3.0.3');
const expected={
  'lib/constants.js':'c355cf54c07a89fd204eb08b747e27227e9a3522155885b45692455d9ac75f50',
  'lib/parse.js':'8f9de3e30302a13b019d107468477e6c29f8899b67d8aec4799da3dd8a69ffbb',
  'lib/compile.js':'dd163256e5df7f40d9fbc1e5822a26772c969b53238cbc2cf083a0efeb3a0637',
  'lib/expand.js':'657d394c6a821513fdcc106ccdd5d4e0230f3f207a7462fee353fca88a4c2b3c',
  'lib/stringify.js':'7706f0c5936941eacf35027f70d74ba54ce9bf2628a96089c57e7c4c45cb1c6f',
};
for(const [file,hash] of Object.entries(expected))assert.equal(sha(fs.readFileSync(path.join(packagePath,file),'utf8').replace(/\r\n/g,'\n')),hash,`Reviewed installed source: ${file}`);
const patchFile=path.join(__dirname,'../patches/braces@3.0.3.patch');
assert.equal(sha(fs.readFileSync(patchFile,'utf8').replace(/\r\n/g,'\n')),'768077ab2a846a22a6a946c78319afd490a97f6136ff5660ec0efbf31e732f64','Reviewed patch is unchanged');
const rejected=error=>error instanceof SyntaxError&&error.message==='Input nesting exceeds max depth (100)';
const wrap=(open,close,depth)=>open.repeat(depth)+'a,b'+close.repeat(depth);
for(const pattern of [wrap('{','}',101),wrap('(',')',101),wrap('({','})',51),wrap('{','}',4000),wrap('(',')',4000),wrap('({','})',2000),'{'.repeat(4000)+'a']){
  assert.ok(pattern.length<10000);
  for(const method of ['parse','compile','expand','stringify'])assert.throws(()=>braces[method](pattern,{maxDepth:Infinity}),rejected,`${method} rejects actual nesting before recursive walkers`);
  assert.throws(()=>braces(pattern),rejected);assert.throws(()=>braces(pattern,{expand:true}),rejected);
}
for(const pattern of [wrap('{','}',100),wrap('(',')',100),wrap('({','})',50)])for(const method of ['parse','compile','expand','stringify'])assert.doesNotThrow(()=>braces[method](pattern));
for(const [pattern,compiled,expanded] of [
  ['a/{b,c}/d','a/(b|c)/d',['a/b/d','a/c/d']],
  ['file{1..3}.txt','file([1-3]).txt',['file1.txt','file2.txt','file3.txt']],
  ['{a,{b,c}}','(a|(b|c))',['a','b','c']],
  ['a/(x|y)/{b,c}','a/(x|y)/(b|c)',['a/(x|y)/b','a/(x|y)/c']],
  ['\\{a,b\\}','{a,b}',['{a,b}']],
  ['[{a,b}]','[{a,b}]',['[{a,b}]']],
]){assert.equal(braces.compile(pattern),compiled);assert.deepEqual(braces.expand(pattern),expanded);}
// Quoted, bracketed and escaped brace text never opens a nested AST container.
for(const pattern of ['"'+wrap('{','}',4000)+'"','['+wrap('{','}',4000)+']','\\{'.repeat(400)+'x'+'\\}'.repeat(400)])assert.doesNotThrow(()=>braces.compile(pattern));
const ast=depth=>{let current={type:'text',value:'x'};for(let index=0;index<depth;index++){const parent={type:'root',nodes:[current]};current.parent=parent;current=parent;}return current;};
for(const method of ['compile','expand','stringify'])assert.throws(()=>braces[method](ast(4000)),rejected,`${method} bounds direct AST inputs as well`);
console.log('Braces: max 100 nested brace/paren containers enforced before walkers; direct AST walkers bounded; normal, escaped, quoted and bracketed patterns preserved; installed source and patch hashes verified.');
module.exports={version:'3.0.3',packagePath,patchHash:'768077ab2a846a22a6a946c78319afd490a97f6136ff5660ec0efbf31e732f64'};
