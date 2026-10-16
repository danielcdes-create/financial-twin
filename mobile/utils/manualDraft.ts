import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {emptyManualCapture, hasManualCapture, type ManualCapture} from './manualCapture';

export const MANUAL_DRAFT_KEY = 'manual-capture';
type Db = {getAllAsync: <T>(sql:string,...params:any[]) => Promise<T[]>; runAsync:(sql:string,...params:any[]) => Promise<unknown>};
type Stored = {draft: ManualCapture; event?: any};
type Owner = {write:Promise<void>; generation:number; listeners:Set<() => void>};
const owners = new WeakMap<Db,Owner>();
function owner(db:Db) {let value=owners.get(db);if (!value) {value={write:Promise.resolve(),generation:0,listeners:new Set()};owners.set(db,value);}return value;}
function enqueue(db:Db,task:() => Promise<void>) {const current=owner(db);const write=current.write.catch(() => undefined).then(task);current.write=write;return write;}
export const flushManualDraft = (db:Db) => owner(db).write;
function save(db:Db,state:Stored,generation:number) {return enqueue(db,async () => {
  if (owner(db).generation !== generation) return;
  if (!hasManualCapture(state.draft)) await db.runAsync('DELETE FROM batch_drafts WHERE batch_id=?',MANUAL_DRAFT_KEY);
  else await db.runAsync('INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?) ON CONFLICT(batch_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at',MANUAL_DRAFT_KEY,JSON.stringify(state),new Date().toISOString());
});}
export function deleteManualDraft(db:Db,expectedState?:string) {
  const current=owner(db);
  return enqueue(db,async () => {
    ++current.generation;
    if (expectedState !== undefined) {
      const rows=await db.getAllAsync<{state:string}>('SELECT * FROM batch_drafts WHERE batch_id=?',MANUAL_DRAFT_KEY);
      if (rows[0]?.state !== expectedState) throw new Error('O rascunho mudou. Atualize a lista e confira antes de excluir.');
    }
    await db.runAsync(expectedState === undefined ? 'DELETE FROM batch_drafts WHERE batch_id=?' : 'DELETE FROM batch_drafts WHERE batch_id=? AND state=?',...(expectedState === undefined ? [MANUAL_DRAFT_KEY] : [MANUAL_DRAFT_KEY,expectedState]));
    const remaining=await db.getAllAsync<{state:string}>('SELECT * FROM batch_drafts WHERE batch_id=?',MANUAL_DRAFT_KEY);
    if (remaining.some(row => row.state !== undefined)) throw new Error('Não foi possível excluir o rascunho. Atualize para conferir.');
    current.listeners.forEach(listener => listener());
  });
}
export function useManualDraft(db:Db,onError:(message:string) => void) {
  const [draft,setDraft]=useState(emptyManualCapture),[ready,setReady]=useState(false);
  const stored=useRef<Stored>({draft:emptyManualCapture()}),mounted=useRef(true),editVersion=useRef(0),error=useRef(onError);
  useLayoutEffect(() => {error.current=onError;},[onError]);
  useLayoutEffect(() => {mounted.current=true;return () => {mounted.current=false;};},[db]);
  useEffect(() => {
    mounted.current=true;let valid=true;
    const current=owner(db),version=editVersion.current,generation=current.generation;
    const cleared=() => {stored.current={draft:emptyManualCapture()};if (mounted.current) {setDraft(stored.current.draft);setReady(true);}};
    current.listeners.add(cleared);
    void current.write.catch(() => undefined).then(() => db.getAllAsync<{state:string}>('SELECT * FROM batch_drafts WHERE batch_id=?',MANUAL_DRAFT_KEY)).then(rows => {
      if (!valid) return;
      if (generation === current.generation && version === editVersion.current && rows[0]?.state) {const state=JSON.parse(rows[0].state);stored.current={...state,draft:{...emptyManualCapture(),...state.draft}};setDraft(stored.current.draft);}
      setReady(true);
    }).catch(() => {if (valid) error.current('Não foi possível recuperar o rascunho. Preserve os dados locais e tente novamente.');});
    return () => {valid=false;mounted.current=false;current.listeners.delete(cleared);};
  },[db]);
  const change=<K extends keyof ManualCapture>(key:K,value:ManualCapture[K]) => {
    ++editVersion.current;stored.current={draft:{...stored.current.draft,[key]:value}};setDraft(stored.current.draft);
    void save(db,stored.current,owner(db).generation).catch(() => {if (mounted.current) error.current('O preenchimento não pôde ser guardado como rascunho. Mantenha esta tela aberta e tente salvar novamente.');});
  };
  const prepare=async (event:any) => {
    await flushManualDraft(db).catch(() => undefined);
    stored.current={...stored.current,event:stored.current.event || event};
    await save(db,stored.current,owner(db).generation);return stored.current.event;
  };
  const clear=() => deleteManualDraft(db);
  return {draft,ready,change,prepare,clear};
}
