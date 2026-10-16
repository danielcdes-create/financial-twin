import React, {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {loadCursorItems} from '../utils/loadCursorItems';
import ControlledChoice from './ControlledChoice';
import {AccountProfile, accountLabel} from '../utils/accountProfile';
import {useSQLiteContext} from '../utils/queueDb';
import {batchBody, BatchRow, selectNewBatchRows} from '../utils/statementBatch';
import {useAsyncScope} from '../utils/useAsyncScope';
import {useLocalBack} from '../utils/localBack';
import {Alert} from '../utils/appAlert';
import {batchDraftSource, preserveConflictingDraft} from '../utils/reviewDraftGuard';
import {deleteLocalDraft, flushDraftWrites, trackDraftWrite} from '../utils/localDrafts';

type Props = {api: (path: string, init?: RequestInit) => Promise<any>; visible: boolean; revision: number;
  accounts: any[]; profiles: AccountProfile[]; onUpdated: () => void | Promise<void>;
  onResolved?: (batchId: string) => void};
const KINDS: Record<string, string> = {expense: 'Compra ou despesa', income: 'Receita ou entrada', refund: 'Estorno ou reembolso', bill_payment: 'Pagamento de fatura', own_transfer: 'Transferência entre minhas contas', unknown: 'Tipo a conferir'};
const LABELS: Record<string, string> = {new: 'Nova movimentação', duplicate: 'Já está no app — não será inserida', possible_duplicate: 'Pode já estar no app — confira', invalid: 'Dados a corrigir', blocked: 'Conferência necessária'};
const sourceRows=(data:any):BatchRow[]=>data.rows.map((row:any)=>({...row,date:row.date?row.date.split('-').reverse().join('/'):'',
  amount:row.amount==null?'':String(row.amount).replace('.',','),selected:true,confirmed_distinct:false}));

function Action({label, onPress, disabled, secondary}: {label: string; onPress: () => void; disabled?: boolean; secondary?: boolean}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{disabled: !!disabled}} disabled={disabled} onPress={onPress} style={[s.action, secondary && s.secondary, disabled && s.disabled]}>
    <Text style={[s.actionText, secondary && s.secondaryText]}>{label}</Text>
  </Pressable>;
}

export default function StatementBatchReview(p: Props) {
  const db = useSQLiteContext();
  const {api, visible, revision} = p;
  const [batches, setBatches] = useState<any[]>([]), [batch, setBatch] = useState<any>(null);
  const [rows, setRows] = useState<BatchRow[]>([]), [account, setAccount] = useState(''), [partial, setPartial] = useState(false);
  const [brl, setBrl] = useState(false);
  const [preview, setPreview] = useState<any>(null), [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [ready, setReady] = useState(false);
  const [rowPage, setRowPage] = useState(0);
  const [checking, setChecking] = useState(false), [checkRevision, setCheckRevision] = useState(0);
  const [conflict, setConflict] = useState<any>(null), [notice, setNotice] = useState('');
  const running = useRef(false), writes = useRef(Promise.resolve()), version = useRef(0);
  const captureScope=useAsyncScope(visible), listGeneration=useRef(0);
  const draftEpoch=useRef(0),editorEpoch=useRef(0),persistedDraft=useRef<string|null>(null);
  const [hasDraft,setHasDraft]=useState(false);
  const back=async()=>{const valid=captureScope();await writes.current;if(batch)await flushDraftWrites(db,'batch',batch.batch_id);if(!valid())return;version.current++;setBatch(null);setResult(null);setPreview(null);setReady(false);setChecking(false);};
  useLocalBack(back,Boolean(visible&&batch),20);
  useLayoutEffect(()=>{const owner=editorEpoch;owner.current++;return()=>{owner.current++}},[batch?.batch_id,visible]);
  const load = useCallback(async () => {
    const valid=captureScope(), current=++listGeneration.current;
    try {const items=await loadCursorItems(api,`/v1/capture-batches?refresh=${Date.now()}`);if(valid()&&current===listGeneration.current)setBatches(items);}
    catch (failure: any) { if(valid()&&current===listGeneration.current&&failure.name!=='AbortError')setError(failure.message || 'Não foi possível carregar os lotes.'); }
  }, [api,captureScope]);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    const current=++listGeneration.current;
    void loadCursorItems(api,`/v1/capture-batches?revision=${revision}`,{signal:controller.signal})
      .then(items => {if (!controller.signal.aborted&&current===listGeneration.current) setBatches(items);})
      .catch(failure => {if (!controller.signal.aborted&&current===listGeneration.current&&failure.name!=='AbortError') setError(failure.message || 'Não foi possível carregar os lotes.');});
    return () => controller.abort();
  }, [api, visible, revision]);
  useEffect(() => {
    if (!batch || !ready || result || conflict) return;
    if(persistedDraft.current!==batch.batch_id&&!account&&!partial&&!brl&&JSON.stringify(rows)===JSON.stringify(sourceRows(batch)))return;
    const snapshot = JSON.stringify({rows, account, partial, brl, source: batchDraftSource(batch)});
    const id = batch.batch_id, valid=captureScope(),epoch=draftEpoch.current;
    writes.current = writes.current.catch(() => undefined).then(async () => {
      if(epoch!==draftEpoch.current)return;
      await db.runAsync('INSERT INTO batch_drafts(batch_id,state,updated_at) VALUES(?,?,?) ON CONFLICT(batch_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at', id, snapshot, new Date().toISOString());
      if(valid()&&epoch===draftEpoch.current){persistedDraft.current=id;setHasDraft(true);}
    }).catch((failure: any) => {if(valid()&&epoch===draftEpoch.current)setError('Não foi possível salvar sua revisão no aparelho: ' + failure.message);});
    trackDraftWrite(db,'batch',id,writes.current);
  }, [account, batch, brl, conflict, db, partial, ready, result, rows,captureScope]);
  useEffect(() => {
    if (!visible || !batch || !ready || result || conflict || !account) return;
    const controller = new AbortController();
    const checkedVersion = version.current;
    const timer = setTimeout(() => {
      if (running.current) return;
      setChecking(true);
      return api(`/v1/capture-batches/${batch.batch_id}/preview`, {method: 'POST',
        body: JSON.stringify(batchBody(account, rows, partial, brl)), signal: controller.signal})
        .then(checked => {if (!controller.signal.aborted && version.current === checkedVersion) {setPreview(checked); setError('');}})
        .catch((failure: any) => {if (!controller.signal.aborted && failure.name !== 'AbortError' && version.current === checkedVersion) setError(failure.message || 'Não foi possível conferir o que já existe.');})
        .finally(() => {if (!controller.signal.aborted) setChecking(false);});
    }, 450);
    // Aborting intentionally skips the request's finally. Release its indicator
    // here as well, including when the editor is hidden or no comparison follows.
    return () => {clearTimeout(timer); controller.abort(); setChecking(false);};
  }, [account, api, batch, brl, checkRevision, conflict, partial, ready, result, rows, visible]);
  const open = async (id: string) => {
    if (running.current) return;
    running.current = true; setBusy(true);const valid=captureScope(); setError(''); setReady(false); setChecking(false);
    try {
      await writes.current;await flushDraftWrites(db,'batch',id);
      const data = await p.api(`/v1/capture-batches/${id}`);
      if (data.status !== 'draft' && data.status !== 'committed') throw new Error('Este lote foi removido. Atualize a lista de lotes.');
      const stored = await db.getAllAsync<{state: string}>('SELECT * FROM batch_drafts WHERE batch_id=?', id);
      if(!valid())return;
      const draft = stored[0] ? JSON.parse(stored[0].state) : null;
      const outdated = data.status === 'draft' && draft && draft.source !== batchDraftSource(data);
      const compatible = outdated || data.status !== 'draft' ? null : draft;
      persistedDraft.current=draft?id:null;setHasDraft(!!draft);
      setConflict(outdated ? draft : null); setNotice('');
      setBatch(data); setResult(data.result || null); setPreview(null);
      setRowPage(0);
      setRows(compatible?.rows || sourceRows(data));
      setAccount(compatible?.account || ''); setPartial(compatible?.partial || false); setBrl(!!compatible?.brl); setReady(true); version.current++;
    } catch (failure: any) { if(!valid())return;setError(failure.message || 'Não foi possível abrir o lote.'); }
    finally { running.current = false; if(valid())setBusy(false); }
  };
  const change = (id: number, field: keyof BatchRow, value: any) => {
    version.current++; setPreview(null); setError('');
    setRows(current => current.map(row => row.row_id === id ? {...row, [field]: value, ...(field !== 'selected' && field !== 'confirmed_distinct' ? {confirmed_distinct: false} : {})} : row));
  };
  const check = () => {if (!running.current && batch) {setPreview(null); setError(''); setCheckRevision(value => value + 1);}};
  const confirmForgetDraft=async()=>{
    if(running.current||!batch)return;
    const id=batch.batch_id,scopeValid=captureScope(),epoch=editorEpoch.current;
    const valid=()=>scopeValid()&&epoch===editorEpoch.current;
    running.current=true;setBusy(true);let snapshot:{batch_id:string;state:string;updated_at:string}|null=null;
    try{await writes.current;await flushDraftWrites(db,'batch',id);snapshot=(await db.getAllAsync<{batch_id:string;state:string;updated_at:string}>('SELECT * FROM batch_drafts WHERE batch_id=?',id))[0]||null;}
    catch(failure:any){if(valid())setError('Não foi possível conferir o rascunho: '+failure.message);return;}
    finally{running.current=false;if(valid())setBusy(false);}
    if(!valid())return;
    Alert.alert('Excluir rascunho do lote?','As edições locais das linhas e da conta serão excluídas. O lote e o arquivo original continuam disponíveis para uma nova revisão.',[
      {text:'Cancelar',style:'cancel'},
      {text:'Excluir rascunho',style:'destructive',onPress:async()=>{
        if(!valid()||running.current)return;running.current=true;setBusy(true);setError('');version.current++;
        try{
          let current:any=null;
          try{current=await api(`/v1/capture-batches/${id}`)}catch{/* Local deletion remains available offline. */}
          if(!valid())return;
          await writes.current;await flushDraftWrites(db,'batch',id);
          if(snapshot)await deleteLocalDraft(db,{kind:'batch',id,title:'',description:'',updatedAt:snapshot.updated_at,row:snapshot,pendingCreation:false});
          else if((await db.getAllAsync('SELECT * FROM batch_drafts WHERE batch_id=?',id)).length)throw new Error('O rascunho mudou. Confira antes de excluir.');
          draftEpoch.current++;
          if(!valid())return;editorEpoch.current++;persistedDraft.current=null;setHasDraft(false);setConflict(null);setPreview(null);setChecking(false);setRowPage(0);
          if(current&&['draft','committed'].includes(current.status)){
            setBatch(current);setResult(current.result||null);setRows(sourceRows(current));setAccount('');setPartial(false);setBrl(false);setReady(current.status==='draft');
            setNotice('Rascunho excluído. As linhas atuais estão prontas para uma nova revisão.');
          }else{setBatch(null);setResult(null);setReady(false);setNotice('Rascunho excluído. Reabra o lote para carregar as linhas atuais.');}
        }catch(failure:any){if(valid())setError('Não foi possível excluir o rascunho: '+failure.message);}
        finally{running.current=false;if(scopeValid())setBusy(false);}
      }},
    ]);
  };
  const resolveConflict = async (recover: boolean) => {
    if (running.current || !batch || !conflict) return;
    running.current = true; setBusy(true);const valid=captureScope();
    try {
      await writes.current;await preserveConflictingDraft(db, 'batch', batch.batch_id, conflict);if(!valid())return;
      if (recover) {setRows(conflict.rows); setAccount(conflict.account || ''); setPartial(!!conflict.partial); setBrl(!!conflict.brl);}
      version.current++; setPreview(null); setConflict(null); setError('');
      setNotice('O rascunho anterior foi preservado no aparelho. Confira novamente as linhas antes de inserir.');
    } catch (failure: any) {if(!valid())return;setError('Não foi possível preservar o rascunho anterior: ' + failure.message);}
    finally {running.current = false; if(valid())setBusy(false);}
  };
  const commit = async () => {
    if (running.current || !preview || !batch || conflict) return;
    running.current = true; setBusy(true);const valid=captureScope(); setError('');
    try {
      await writes.current;
      const saved = await p.api(`/v1/capture-batches/${batch.batch_id}/commit`, {method: 'POST',
        body: JSON.stringify({...batchBody(account, rows, partial, brl), preview_token: preview.preview_token})});
      if (!saved.persisted || saved.batch_id !== batch.batch_id) throw new Error('O servidor não confirmou este lote. Tente novamente com a mesma revisão.');
      if(!valid())return;setResult(saved); setPreview(null); setReady(false);
      p.onResolved?.(batch.batch_id);
      try {draftEpoch.current++;await writes.current;await flushDraftWrites(db,'batch',batch.batch_id);await db.runAsync('DELETE FROM batch_drafts WHERE batch_id=?', batch.batch_id);persistedDraft.current=null;if(valid())setHasDraft(false);}
      catch {setNotice('Lote confirmado no servidor. O rascunho local foi preservado porque a limpeza não terminou.');}
      if(!valid())return;Alert.alert('Lote confirmado', `${saved.inserted_count || 0} movimentação(ões) inserida(s). A revisão foi preservada no histórico.`);
      void Promise.resolve().then(p.onUpdated).catch(()=>{if(valid())setNotice('Lote confirmado no servidor. Atualize a tela para conferir os dados atuais.');});
      await load();
    } catch (failure: any) {
      if(!valid())return;setError(failure.message || 'Não foi possível confirmar. Sua revisão continua salva.');
      if (failure.status === 409) setPreview(null);
    } finally { running.current = false; if(valid())setBusy(false); }
  };
  const remove = async (item: any) => {
    if (running.current || !captureScope()()) return;
    running.current = true; setBusy(true);const valid=captureScope(); setError('');
    try {
      await writes.current;
      const saved = await api(`/v1/capture-batches/${item.batch_id}`, {method: 'DELETE', body: JSON.stringify({expected_sha256: item.sha256})});
      if (!saved.persisted || saved.batch_id !== item.batch_id || saved.status !== 'discarded') throw new Error('O servidor não confirmou a remoção deste lote. Atualize a lista antes de tentar novamente.');
      if(!valid())return;p.onResolved?.(item.batch_id);
      if (batch?.batch_id === item.batch_id) {version.current++; setReady(false); setChecking(false); setPreview(null); setBatch(null); setResult(null);}
      setBatches(current => current.filter(value => value.batch_id !== item.batch_id));
      try {draftEpoch.current++;await writes.current;await flushDraftWrites(db,'batch',item.batch_id);await db.runAsync('DELETE FROM batch_drafts WHERE batch_id=?', item.batch_id);if(persistedDraft.current===item.batch_id)persistedDraft.current=null;if(valid())setHasDraft(false);}
      catch {setNotice('Lote removido no servidor. O rascunho local foi preservado porque a limpeza não terminou.');}
      if(!valid())return;Alert.alert('Lote removido', 'O lote pendente foi removido.');
      await load();
    } catch (failure: any) {if(!valid())return;setError(failure.message || 'Não foi possível remover. O lote continua disponível para conferir.');}
    finally {running.current = false; if(valid())setBusy(false);}
  };
  const confirmRemove = (item: any) => {const valid=captureScope();return Alert.alert('Remover lote pendente?',
    `“${item.filename}” será retirado da lista de revisão. As movimentações já importadas e o arquivo original serão preservados.`,
    [{text: 'Cancelar', style: 'cancel'}, {text: 'Remover lote', style: 'destructive', onPress: () => valid()?remove(item):undefined}]);};
  const accountOptions = p.accounts.filter(item => ['conta_corrente', 'cartao_credito'].includes(item.tipo)).map(item =>
    ({ref: `ledger:${item.id}`, label: `${item.instituicao} · ${item.tipo === 'cartao_credito' ? 'Cartão' : 'Conta'}${item.cartao_final ? ' · ' + item.cartao_final : ''}`}));
  for (const profile of p.profiles) accountOptions.push({ref: `profile:${profile.key}`, label: `${accountLabel(profile)} · ${profile.status === 'encerrada' ? 'Encerrada' : profile.archived ? 'Arquivada' : 'Conta do app'}`});
  return <View style={p.visible ? s.panel : s.hidden} accessibilityElementsHidden={!p.visible} importantForAccessibility={p.visible ? 'auto' : 'no-hide-descendants'}>
    <Text accessibilityRole="header" style={s.heading}>Lotes para conferir</Text>
    {!batch && <>
      <Text style={s.note}>O arquivo vira um lote de revisão. Confira as linhas e a conta antes de inserir.</Text>
      {batches.length === 0 && <Text style={s.note}>Nenhum lote aguardando revisão.</Text>}
      {batches.map(item => <View key={item.batch_id} style={s.row}>
        <Text style={s.title}>{item.filename} · {item.row_count} linhas</Text>
        {!!item.created_at && <Text style={s.note}>Capturado em {new Date(item.created_at).toLocaleString('pt-BR', {timeZone: 'America/Sao_Paulo'})}</Text>}
        {batches.some(other => other.batch_id !== item.batch_id && other.sha256 === item.sha256) && <Text style={s.warning}>Há outro lote deste mesmo arquivo.</Text>}
        <Action label={`Revisar ${item.filename}`} disabled={busy} onPress={() => open(item.batch_id)} secondary />
        <Action label={`Remover lote ${item.filename}`} disabled={busy} onPress={() => confirmRemove(item)} secondary />
      </View>)}
      <Action label="Atualizar lotes" onPress={() => load()} disabled={busy} secondary />
    </>}
    {batch && !result && <>
      <Text style={s.title}>{batch.filename} · {rows.length} linhas</Text>
      <Text style={s.note}>O rascunho fica salvo neste aparelho. Saldo, limite e previsões não são movimentações.</Text>
      {!!conflict && <View accessibilityRole="alert"><Text style={s.warning}>O rascunho salvo usa outra versão da extração ou ainda não tem identificação de versão. Ele foi preservado e não foi aplicado às linhas atuais.</Text>
        <Action label="Usar linhas atuais e preservar rascunho anterior" disabled={busy} secondary onPress={() => resolveConflict(false)} />
        <Action label="Conferi a mudança: recuperar minhas edições" disabled={busy} secondary onPress={() => resolveConflict(true)} />
      </View>}
      {(batch.warnings || []).map((warning: string, index: number) => <Text key={index} style={s.warning}>{warning}</Text>)}
      {!!batch.account_hint && <Text style={s.note}>Conta indicada no documento: {batch.account_hint}. Confirme abaixo.</Text>}
      <ControlledChoice disabled={busy} label="Conta ou cartão do extrato" value={account} options={accountOptions.map(item => item.ref)}
        display={ref => accountOptions.find(item => item.ref === ref)?.label || ref} allowClear={false}
        onChange={value => {if (busy) return; version.current++; setAccount(value); setPreview(null); setRows(current => current.map(row => ({...row, confirmed_distinct: false})));}} />
      {!account && <Text style={s.note}>Escolha a conta para identificar automaticamente o que já está no app.</Text>}
      {rows.some(row => row.currency === null || row.currency === '') && <Pressable accessibilityRole="checkbox" accessibilityLabel="Confirmar moeda BRL das linhas sem moeda"
        accessibilityState={{checked: brl, disabled: busy}} aria-checked={brl} aria-disabled={busy} disabled={busy}
        onPress={() => {version.current++; setBrl(value => !value); setPreview(null);}} style={s.choice}>
        <Text style={s.warning}>{brl ? '☑' : '☐'} Conferi o arquivo: as linhas sem moeda informada são em reais (BRL).</Text>
      </Pressable>}
      {!batch.complete && <Pressable accessibilityRole="checkbox" accessibilityState={{checked: partial, disabled: busy}} aria-checked={partial} aria-disabled={busy} disabled={busy}
        onPress={() => {version.current++; setPartial(value => !value); setPreview(null);}} style={s.choice}>
        <Text style={s.note}>{partial ? '☑' : '☐'} Estou importando apenas as linhas visíveis deste arquivo parcial.</Text>
      </Pressable>}
      <Text style={s.note}>Linhas {rowPage * 10 + 1} a {Math.min(rows.length, (rowPage + 1) * 10)} de {rows.length}</Text>
      {rows.slice(rowPage * 10, (rowPage + 1) * 10).map(row => {
        const outcome = preview?.rows.find((item: any) => item.row_id === row.row_id);
        return <View key={row.row_id} style={s.row}>
          <Text style={s.title}>Linha {row.row_id}</Text>
          {!!row.bank_ref && <Text style={s.note}>Identificador bancário: {row.bank_ref}</Text>}
          <Text style={s.note}>Moeda da fonte: {row.currency === undefined ? 'BRL' : row.currency || 'não determinada'}{row.currency && row.currency !== 'BRL' ? ' · não pode ser inserida como BRL' : ''}</Text>
          <TextInput accessibilityLabel={`Descrição da linha ${row.row_id}`} value={row.description} editable={!busy} onChangeText={value => change(row.row_id, 'description', value)} style={s.input} placeholder="Descrição" placeholderTextColor="#8BA3A0" />
          <View style={s.fields}>
            <View style={s.half}><Text style={s.note}>Data</Text><TextInput accessibilityLabel={`Data da linha ${row.row_id}`} value={row.date} editable={!busy} onChangeText={value => change(row.row_id, 'date', value)} placeholder="DD/MM/AAAA" placeholderTextColor="#8BA3A0" style={s.input} /></View>
            <View style={s.half}><Text style={s.note}>Valor com sinal</Text><TextInput accessibilityLabel={`Valor da linha ${row.row_id}`} value={row.amount} editable={!busy} onChangeText={value => change(row.row_id, 'amount', value)} placeholder="-12,59" placeholderTextColor="#8BA3A0" keyboardType="numbers-and-punctuation" style={s.input} /></View>
          </View>
          <ControlledChoice disabled={busy} label={`Tipo da linha ${row.row_id}`} value={row.kind} options={Object.keys(KINDS)} display={value => KINDS[value] || value}
            onChange={value => {if (!busy) change(row.row_id, 'kind', value);}} allowClear={false} />
          <Pressable accessibilityRole="checkbox" accessibilityLabel={`Incluir linha ${row.row_id}`} accessibilityState={{checked: row.selected && outcome?.status !== 'duplicate', disabled: busy || outcome?.status === 'duplicate'}} aria-checked={row.selected && outcome?.status !== 'duplicate'} aria-disabled={busy || outcome?.status === 'duplicate'}
            disabled={busy || outcome?.status === 'duplicate'} onPress={() => change(row.row_id, 'selected', !row.selected)} style={s.choice}>
            <Text style={s.note}>{row.selected && outcome?.status !== 'duplicate' ? '☑' : '☐'} Incluir esta linha</Text>
          </Pressable>
        </View>;
      })}
      {rows.length > 10 && <View style={s.fields}>
        <View style={s.half}><Action label="Linhas anteriores" disabled={busy || rowPage === 0} secondary onPress={() => setRowPage(value => value - 1)} /></View>
        <View style={s.half}><Action label="Próximas linhas" disabled={busy || (rowPage + 1) * 10 >= rows.length} secondary onPress={() => setRowPage(value => value + 1)} /></View>
      </View>}
      <Text style={s.note}>Saídas usam valor negativo; entradas usam positivo. Transferências entre suas contas exigem origem e destino na captura por Digitar.</Text>
      {/* Async comparison must not insert/remove blocks above editable rows. */}
      {checking && <Text accessibilityLiveRegion="polite" style={s.note}>Comparando com as movimentações que já estão no app…</Text>}
      {preview && <View accessibilityLiveRegion="polite" style={s.summary}>
        <Text style={s.title}>{preview.new_count} novas · {preview.duplicate_count} já existentes · {preview.possible_duplicate_count} possíveis coincidências{preview.rows.some((row: any) => ['invalid', 'blocked'].includes(row.status)) ? ` · ${preview.rows.filter((row: any) => ['invalid', 'blocked'].includes(row.status)).length} para corrigir` : ''}</Text>
        <Text style={s.note}>Datas, valores, descrição e conta são comparados. Confira os resultados por linha abaixo antes de inserir.</Text>
        <Action label="Selecionar só as novas" disabled={busy || checking || !preview.scan_complete} secondary onPress={() => {
          version.current++; setRows(current => selectNewBatchRows(current, preview.rows)); setPreview(null);
        }} />
      </View>}
      {rows.slice(rowPage * 10, (rowPage + 1) * 10).map(row => {
        const outcome = preview?.rows.find((item: any) => item.row_id === row.row_id);
        if (!outcome) return null;
        return <View key={row.row_id} style={s.row}>
          <Text style={s.title}>Conferência da linha {row.row_id} · {row.description}</Text>
          <Text style={outcome.status === 'new' ? s.new : s.warning}>{outcome.status === 'possible_duplicate' && outcome.confidence === 'high' ? 'Provavelmente já está no app — confira' : LABELS[outcome.status]}</Text>
          <Text style={s.note}>{outcome.reason}</Text>
          {outcome.matches.map((match: any, index: number) => <View key={index} style={s.match}>
            <Text style={s.note}>{match.source === 'deleted' ? 'Registro removido anteriormente' : match.source === 'capture' ? 'Captura existente' : 'Extrato existente'}: {match.description}</Text>
            <Text style={s.note}>{String(match.date).split('-').reverse().join('/')} · {Number(match.amount).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'})}{!match.account_known ? ' · conta não identificada' : ''}</Text>
          </View>)}
          {(outcome.status === 'possible_duplicate' || row.confirmed_distinct) && <Pressable accessibilityRole="checkbox" accessibilityLabel={`Confirmar linha ${row.row_id} como diferente`}
            accessibilityState={{checked: row.confirmed_distinct, disabled: busy}} aria-checked={row.confirmed_distinct} aria-disabled={busy} disabled={busy} onPress={() => change(row.row_id, 'confirmed_distinct', !row.confirmed_distinct)} style={s.choice}>
            <Text style={s.warning}>{row.confirmed_distinct ? '☑' : '☐'} Conferi os registros e esta é uma movimentação diferente.</Text>
          </Pressable>}
        </View>;
      })}
      <Action label={checking ? 'Conferindo…' : 'Conferir duplicidades novamente'} onPress={check} disabled={busy || checking || !!conflict || !account} />
      {preview && <>
        <Text style={s.title}>{preview.insert_count} novas · {preview.duplicate_count} duplicadas barradas</Text>
        <Text style={s.note}>Ao confirmar, só as linhas selecionadas e aprovadas entram no Extrato. As duplicidades são conferidas novamente no servidor.</Text>
        <Action label={busy ? 'Inserindo…' : preview.insert_count ? `Inserir ${preview.insert_count} movimentações` : 'Concluir sem inserir'} onPress={() => commit()} disabled={busy || checking || !preview.scan_complete || preview.rows.some((row: any) => row.selected && (['invalid', 'blocked'].includes(row.status) || (row.status === 'possible_duplicate' && !rows.find(item => item.row_id === row.row_id)?.confirmed_distinct)))} />
      </>}
      {(hasDraft||!!conflict||!!account||partial||brl||JSON.stringify(rows)!==JSON.stringify(sourceRows(batch)))&&<Action label="Excluir rascunho deste lote" disabled={busy} secondary onPress={confirmForgetDraft} />}
      <Action label="Remover este lote" disabled={busy} secondary onPress={() => confirmRemove(batch)} />
    </>}
    {result && <View accessibilityRole="alert"><Text style={s.new}>{result.inserted_count} movimentações inseridas · {result.skipped_count} linhas não inseridas.</Text>
      <Text style={s.note}>O arquivo e a conferência ficam preservados no histórico.</Text>
      {(result.rows || []).filter((row: any) => !row.will_insert).map((row: any) => <Text key={row.row_id} style={s.note}>
        Linha {row.row_id}: {!row.selected ? 'não selecionada' : LABELS[row.status] || 'não inserida'}. {row.reason}
      </Text>)}
    </View>}
    {batch && <Action label={result ? 'Voltar aos lotes' : 'Fechar e retomar depois'} disabled={busy} secondary onPress={back} />}
    {!!error && <Text accessibilityRole="alert" style={s.warning}>{error}</Text>}
    {!!notice && <Text accessibilityLiveRegion="polite" style={s.note}>{notice}</Text>}
  </View>;
}

const s = StyleSheet.create({panel: {gap: 12, paddingVertical: 16}, hidden: {display: 'none'}, heading: {color: '#F4F7F5', fontWeight: '700', fontSize: 20},
  title: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'}, note: {color: '#AFC1BF', fontSize: 13, lineHeight: 20}, warning: {color: '#F6C564', fontSize: 13, lineHeight: 20}, new: {color: '#65DEC2', fontSize: 14, lineHeight: 21},
  row: {borderColor: '#315054', borderWidth: 1, borderRadius: 14, padding: 12, gap: 10}, fields: {flexDirection: 'row', flexWrap: 'wrap', gap: 8}, half: {flex: 1, minWidth: 130},
  summary: {backgroundColor: '#142326', borderRadius: 12, padding: 12, gap: 8}, match: {borderLeftWidth: 2, borderLeftColor: '#F6C564', paddingLeft: 9},
  input: {minHeight: 48, borderRadius: 10, backgroundColor: '#142326', color: '#F4F7F5', padding: 11, fontSize: 15, borderColor: '#315054', borderWidth: 1},
  choice: {minHeight: 48, justifyContent: 'center'}, action: {minHeight: 48, borderRadius: 12, backgroundColor: '#49C5AA', padding: 13, justifyContent: 'center', alignItems: 'center'},
  actionText: {color: '#061412', fontWeight: '700'}, secondary: {backgroundColor: '#142326', borderColor: '#315054', borderWidth: 1}, secondaryText: {color: '#C5D8D4'}, disabled: {opacity: .45}});
