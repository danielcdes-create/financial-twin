export type MutationDb = {getAllAsync:<T>(sql:string,...params:any[])=>Promise<T[]>;runAsync:(sql:string,...params:any[])=>Promise<unknown>};
export type MutationCommand = {command_key:string;path:string;body:string;result:string|null;updated_at:string};
const newId=()=> 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const value=Math.random()*16|0;return(c==='x'?value:(value&3)|8).toString(16)});
export const journaledMutation=(path:string,method:string)=>
  /^\/v1\/(?:transactions\/[^/]+\/(?:classification|review)|reviews\/[^/]+\/decision|(?:card-catalog|card-bills|account-profiles)\/[^/]+(?:\/restore)?|month-close\/\d{4}-\d{2}(?:\/.*)?|commitments\/[^/]+(?:\/payment)?)$/.test(path) && ['POST','PUT','PATCH','DELETE'].includes(method);
export async function pendingMutations(db:MutationDb,baseUrl:string){
  const rows=await db.getAllAsync<MutationCommand>('SELECT * FROM review_commands');
  return rows.filter(row=>row.command_key.startsWith('mutation:')&&!row.result&&JSON.parse(row.body).baseUrl===baseUrl);
}
export async function retainMutation(db:MutationDb,baseUrl:string,path:string,method:string,values:Record<string,any>){
  if(values.request_id){
    const existing=await db.getAllAsync<MutationCommand>('SELECT * FROM review_commands WHERE command_key=?',`mutation:${values.request_id}`);
    if(existing[0]){
      const previous=JSON.parse(existing[0].body);
      if(previous.baseUrl!==baseUrl||existing[0].path!==path||previous.method!==method||JSON.stringify(previous.values)!==JSON.stringify(values))throw new Error('Esta identidade de operação já está associada a outros dados. As tentativas anteriores foram preservadas.');
      if(existing[0].result&&!JSON.parse(existing[0].result).rejected)return existing[0];
    }
  }
  const content={...values};delete content.request_id;
  const pending=(await pendingMutations(db,baseUrl)).find(row=>row.path===path&&JSON.parse(row.body).method===method);
  if(pending){
    const previous={...JSON.parse(pending.body).values};delete previous.request_id;
    if(JSON.stringify(previous)!==JSON.stringify(content))throw new Error('Há uma tentativa anterior sem confirmação para este registro. Conclua a tentativa pendente antes de salvar novas alterações. Seu rascunho foi preservado.');
    return pending;
  }
  const operation=values.request_id||newId();
  const command={command_key:`mutation:${operation}`,path,body:JSON.stringify({baseUrl,method,values:{...values,request_id:operation}}),result:null,updated_at:new Date().toISOString()};
  await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',command.command_key,command.path,command.body,null,command.updated_at);
  return command;
}
export async function acknowledgeMutation(db:MutationDb,command:MutationCommand,response:any){
  const requestId=JSON.parse(command.body).values.request_id;
  if(response?.persisted!==true||response.request_id!==requestId)throw new Error('O servidor não confirmou a identidade da gravação. A tentativa continua preservada para conferência e reenvio.');
  try{await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',command.command_key,command.path,command.body,JSON.stringify(response),new Date().toISOString());}
  catch{response.local_finalization_pending=true;}
  return response;
}
export async function rejectMutation(db:MutationDb,command:MutationCommand,status:number,message:string){
  await db.runAsync('INSERT INTO review_commands(command_key,path,body,result,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(command_key) DO UPDATE SET path=excluded.path,body=excluded.body,result=excluded.result,updated_at=excluded.updated_at',command.command_key,command.path,command.body,JSON.stringify({rejected:true,status,message}),new Date().toISOString());
}
