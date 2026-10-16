import {ensureVoiceEvidenceFile} from './voiceEvidenceFile';
import {readQueueAcknowledgement} from './queueAcknowledgements';
import {sameQueueRow, type QueueRow} from './syncQueue';
import {voiceEvidenceCommandFile, type VoiceEvidenceCommand} from './voiceDraft';

type Db={getAllAsync:<T>(sql:string,...params:any[])=>Promise<T[]>;runAsync:(sql:string,...params:any[])=>Promise<unknown>};
export async function enqueueVoiceEvidence(db:Db, command:VoiceEvidenceCommand):Promise<void>{
  const fileId=command.event_id;
  const file=voiceEvidenceCommandFile(command),uri=file.uri;
  const payload=JSON.stringify({voice_command_version:1,captured_at:command.captured_at});
  const expected:QueueRow={id:0,event_id:fileId,kind:'document',payload,local_uri:uri,filename:file.name,mime_type:file.mime};
  if(await readQueueAcknowledgement(db,expected))return;
  if(await ensureVoiceEvidenceFile(fileId,command.text)!==uri)throw new Error('O arquivo da evidência mudou de identidade. Preserve a tentativa.');
  await db.runAsync('INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)',fileId,'document',payload,uri,expected.filename,expected.mime_type,command.captured_at);
  const stored=(await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id')).find(row=>row.event_id===fileId);
  if(!stored||!sameQueueRow(stored,{...expected,id:stored.id}))throw new Error('A fila desta evidência não foi confirmada com o mesmo conteúdo. Preserve a tentativa.');
}
