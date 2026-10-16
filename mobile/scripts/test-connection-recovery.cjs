const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),ts=require('typescript');
const cache=new Map();
function load(file){
  file=path.resolve(__dirname,'..',file);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  new Function('require','module','exports',code)(name=>name.startsWith('.')?load(path.relative(path.resolve(__dirname,'..'),path.resolve(path.dirname(file),name+'.ts'))):require(name),module,module.exports);
  return module.exports;
}
const {createApiClient,ApiError}=load('utils/apiClient.ts');
const {LatestQueries}=load('utils/latestQueries.ts');
const {createQueueSync}=load('utils/syncQueue.ts');
const reply=(status,body)=>new Response(JSON.stringify(body),{status});
const tick=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 let reads=0;const states=[];
 const api=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[0,0],fetch:async()=>{
   reads++;return reads<3?reply(502,{detail:'Proxy indisponível'}):reply(200,{mongodb:'connected'});
 }});
 api.subscribeAvailability(()=>states.push(api.getAvailability()));
 const pair=await Promise.all([api.json('/health'),api.json('/health')]);
 assert.equal(reads,3);assert.equal(pair[0].mongodb,'connected');assert.deepEqual(states,['reconnecting','connected']);
 let posts=0,removed=0;let row={id:1,event_id:'keep-event',kind:'event',payload:'{"event_id":"keep-event"}',local_uri:null,filename:null,mime_type:null};
 const events=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[0,0],fetch:async(_url,init)=>{
   if(init.method==='POST'){posts++;return posts===1?reply(502,{detail:'Gateway caiu'}):reply(200,{persisted:true,event_id:'keep-event'});}
   return reply(200,{mongodb:'connected'});
 }});
 const sync=createQueueSync({prepare:async()=>{await events.json('/health');return true},list:async()=>row?[row]:[],
   send:item=>events.json('/v1/events',{method:'POST',body:item.payload}),remove:async()=>{removed++;row=null},done:()=>{},
   recordFailure:async(_item,_error,attempts,next)=>{row={...row,attempt_count:attempts,next_attempt_at:next}},changed:()=>{}});
 await sync();assert.equal(posts,1);assert.equal(removed,0);assert.equal(row.event_id,'keep-event');assert.equal(row.attempt_count,1);
 row.next_attempt_at=null;await sync();assert.equal(posts,2);assert.equal(removed,1);assert.equal(row,null);
 let denied=0;
 const auth=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[0,0],fetch:async()=>{denied++;return reply(401,{detail:'Login necessário'})}});
 await assert.rejects(auth.json('/v1/reviews'),e=>e.status===401);assert.equal(denied,1);
 let abortedCalls=0;
 const cancel=new AbortController();
 const abortedApi=createApiClient({baseUrl:'http://fixture',token:()=>'',readRetryDelaysMs:[5000],fetch:async()=>{abortedCalls++;return reply(502,{})}});
 const waiting=abortedApi.json('/v1/reviews',{signal:cancel.signal});setTimeout(()=>cancel.abort(),20);
 await assert.rejects(waiting,e=>e.name==='AbortError');await tick(30);assert.equal(abortedCalls,1);
 let slow=0;
 const timeout=createApiClient({baseUrl:'http://fixture',token:()=>'',readTimeoutMs:40,readRetryDelaysMs:[0,0],fetch:(_url,init)=>{
   slow++;return new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true}));
 }});
 const started=Date.now();await assert.rejects(timeout.json('/health'),e=>e.retryable);assert.equal(slow,1);assert.ok(Date.now()-started<250);
 let available=true;const published=[],changes=[];
 const queries=new LatestQueries((pending,errors,failed)=>changes.push({pending,errors,failed}));
 const loadResource=async()=>{if(!available)throw new ApiError('502',502);return 'fresh'};
 available=false;await queries.run('início','/dashboard',loadResource,value=>published.push(value));
 await queries.run('revisão','/reviews',loadResource,value=>published.push(value));
 assert.equal(changes.at(-1).failed,true);available=true;await queries.retryFailed();assert.deepEqual(published,['fresh','fresh']);
 assert.deepEqual(changes.at(-1).errors,{});assert.equal(changes.at(-1).failed,false);
 let providerCalls=0;
 const provider=createApiClient({baseUrl:'http://fixture',token:()=>'',fetch:async url=>{
   if(url.endsWith('/health'))return reply(200,{mongodb:'connected'});
   providerCalls++;return reply(503,{detail:'Extração indisponível'});
 }});
 await provider.json('/health');await assert.rejects(provider.json('/v1/insights/purchase-scan',{method:'POST',body:'{}'}));
 assert.equal(providerCalls,1);assert.equal(provider.getAvailability(),'connected');
 console.log('Connection recovery: shared GET retries, no blind POST replay, queue preserved until ACK, auth/abort/timeout, failed resources and provider isolation — passed');
})().catch(error=>{console.error(error);process.exitCode=1});
