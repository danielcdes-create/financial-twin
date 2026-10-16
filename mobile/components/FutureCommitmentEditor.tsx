import React, {useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import {captureDateIso} from '../utils/captureDate';
import {signedBatchAmount} from '../utils/statementBatch';
import {Alert} from '../utils/appAlert';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {rebaseDraft} from '../utils/draftConflict';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';
import {closureWarning, futureRemovalLabel, sumAmounts} from '../utils/commitmentPresentation';

type Props = {source: 'installment' | 'obligation' | 'managed'; identity: string; name: string; kind?: 'assinatura' | 'parcelamento';
  api: (path: string, init?: RequestInit) => Promise<any>; onUpdated: () => void | Promise<void>;
  onRemoved?: (receipt: {delete_request_id: string}) => void;};
const newId = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
  const r = Math.random() * 16 | 0; return (c === 'x' ? r : r & 3 | 8).toString(16);
});
const displayDate = (value: string | null) => value ? value.split('-').reverse().join('/') : '';
const rowFields = (row:any) => ({amount:signedBatchAmount(row.amount)||row.amount, due_date:row.date_input.trim()?captureDateIso(row.date_input)||row.date_input:null, cancelled:Boolean(row.cancelled)});
type Difference = {row_id:number; field:string; original:any; current:any; local:any};
function Button({label, accessibilityLabel, onPress, disabled, danger}: {label: string; accessibilityLabel?: string; onPress: () => void; disabled?: boolean; danger?: boolean}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel || label} onPress={onPress} disabled={disabled}
    style={[s.button, danger && s.dangerButton, disabled && s.disabled]}><Text style={s.buttonText}>{label}</Text></Pressable>;
}

export default function FutureCommitmentEditor(p: Props) {
  const [preview, setPreview] = useState<any>(null), [rows, setRows] = useState<any[]>([]);
  const [mode, setMode] = useState<'edit' | 'delete'>('edit'), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [confirmation, setConfirmation] = useState<any>(null), [page, setPage] = useState(0), [done, setDone] = useState('');
  const [conflict,setConflict]=useState(false),[undoId,setUndoId]=useState('');
  const [differences,setDifferences]=useState<Difference[]>([]);
  const bases=useRef(new Map<number,ReturnType<typeof rowFields>>());
  const back=()=>{
    if(confirmation){setConfirmation(null);return;}
    return protectUnsavedBack(conflict||rows.some(row=>JSON.stringify(rowFields(row))!==JSON.stringify(bases.current.get(row.row_id))),()=>{setPreview(null);setError('');});
  };
  useLocalBack(back,Boolean(preview),30);
  const running = useRef(false);
  const path = `/v1/future-commitments/${p.source}/${encodeURIComponent(p.identity)}`;
  const captureScope = useAsyncScope(path);
  const open = async (action: 'edit' | 'delete', preserveDraft = false) => {
    if (running.current) return;
    running.current = true; setBusy(true); setError(''); setDone('');
    const valid = captureScope();
    try {
      const fresh = await p.api(`${path}?refresh=${Date.now()}`);
      if (!valid()) return;
      const freshRows=fresh.rows.filter((row:any)=>row.editable).map((row:any)=>({...row,original_amount:row.amount,amount:String(row.amount).replace('.',','),date_input:displayDate(row.due_date),cancelled:false}));
      let overlaps:Difference[]=[];
      if(preserveDraft){
        const merged=freshRows.map((current:any)=>{
          const old=rows.find(row=>row.row_id===current.row_id),base=bases.current.get(current.row_id);
          if(!old||!base)return current;
          const remote=rowFields(current),local=rowFields(old),rebased=rebaseDraft(base,remote,local);
          overlaps.push(...rebased.conflicts.map(field=>({row_id:current.row_id,field,original:base[field as keyof typeof base],current:remote[field as keyof typeof remote],local:local[field as keyof typeof local]})));
          return {...current,amount:rebased.draft.amount.replace('.',','),date_input:displayDate(rebased.draft.due_date),cancelled:rebased.draft.cancelled};
        });
        for(const old of rows){
          const base=bases.current.get(old.row_id);
          if(base&&!freshRows.some((current:any)=>current.row_id===old.row_id)&&JSON.stringify(rowFields(old))!==JSON.stringify(base)){
            overlaps.push({row_id:old.row_id,field:'availability',original:'Disponível',current:'Parcela paga, excluída ou indisponível',local:'Edição preservada'});merged.push(old);
          }
        }
        setRows(merged);
      }else setRows(freshRows);
      const oldBases=bases.current;
      bases.current=new Map(freshRows.map((row:any)=>{
        const base=rowFields(row),old=oldBases.get(row.row_id);
        for(const difference of overlaps.filter(item=>item.row_id===row.row_id&&item.field!=='availability'))if(old)(base as Record<string,any>)[difference.field]=(old as Record<string,any>)[difference.field];
        return [row.row_id,base];
      }));
      for(const difference of overlaps.filter(item=>item.field==='availability')){const old=oldBases.get(difference.row_id);if(old)bases.current.set(difference.row_id,old);}
      setDifferences(overlaps);setConflict(overlaps.length>0);
      setMode(action); setPreview(fresh); setConfirmation(null); setPage(0);
      if (action === 'delete' && fresh.editable_count) setConfirmation({method: 'DELETE', body: {request_id: newId(), preview_token: fresh.preview_token}});
    } catch (failure: any) {if(valid())setError(failure.message || 'Não foi possível conferir o cronograma.');}
    finally {running.current = false; if(valid())setBusy(false);}
  };
  const change = (row_id: number, field: string, value: any) => setRows(current => current.map(row => row.row_id === row_id ? {...row, [field]: value} : row));
  const resolve=(difference:Difference,local:boolean)=>{
    if(difference.field==='availability'){setRows(current=>current.filter(row=>row.row_id!==difference.row_id));bases.current.delete(difference.row_id);}
    else if(!local){const field=difference.field==='due_date'?'date_input':difference.field;change(difference.row_id,field,difference.field==='due_date'?displayDate(difference.current):difference.field==='amount'?String(difference.current).replace('.',','):difference.current);}
    const base=bases.current.get(difference.row_id);if(base&&difference.field!=='availability')(base as Record<string,any>)[difference.field]=difference.current;
    const remaining=differences.filter(item=>item!==difference);setDifferences(remaining);setConflict(remaining.length>0);setConfirmation(null);
  };
  const prepare = () => {
    if(conflict||differences.length)return;
    setError('');
    const edits = [];
    for (const row of rows) {
      const amount = signedBatchAmount(row.amount), due_date = row.date_input.trim() ? captureDateIso(row.date_input) : null;
      if (!amount || Number(amount) <= 0 || (row.date_input.trim() && !due_date)) {setError(`Confira o valor positivo e a data da parcela ${row.number || row.row_id}.`); return;}
      if (amount !== Number(row.original_amount).toFixed(2) || due_date !== row.due_date || row.cancelled)
        edits.push({row_id: row.row_id, amount, due_date, cancelled: row.cancelled});
    }
    if (!edits.length) {setError('Altere uma parcela futura antes de salvar.'); return;}
    setConfirmation({method: 'PUT', body: {request_id: newId(), preview_token: preview.preview_token, rows: edits}});
  };
  const send = async () => {
    if (running.current || !confirmation || !captureScope()()) return;
    running.current = true; setBusy(true); setError('');
    const valid = captureScope();
    let acknowledged=false;
    try {
      const saved = await p.api(path, {method: confirmation.method, body: JSON.stringify(confirmation.body)});
      if (!saved.persisted || saved.identity !== p.identity || saved.source !== p.source) throw new Error('O servidor não confirmou esta alteração. Tente novamente.');
      acknowledged=true;
      if (!valid()) return;
      if (confirmation.method === 'DELETE') {
        const deleteRequestId = String(saved.delete_request_id || confirmation.body.request_id);
        setUndoId(deleteRequestId);
        p.onRemoved?.({delete_request_id: deleteRequestId});
      }
      setDone(confirmation.method === 'DELETE' ? 'Compromissos futuros excluídos. Histórico preservado.' : 'Parcelas futuras atualizadas. Histórico preservado.');
      Alert.alert(confirmation.method === 'DELETE' ? 'Compromissos futuros excluídos' : 'Parcelas futuras atualizadas', 'A alteração foi confirmada. O histórico foi preservado.');
      setPreview(null); setConfirmation(null);
      void Promise.resolve().then(p.onUpdated).catch(failure=>{if(valid())setError(`A alteração foi confirmada. Não foi possível atualizar a lista: ${failure.message}`);});
    } catch (failure: any) {
      if(!valid())return;
      setError(acknowledged?`A alteração foi confirmada. Não foi possível atualizar a lista: ${failure.message}`:failure.message || 'Não foi possível salvar. Os dados da revisão continuam nesta tela.');
      if (!acknowledged && failure.status === 409) {setConfirmation(null);setConflict(true);}
    } finally {running.current = false; if(valid())setBusy(false);}
  };
  const undoDelete = async () => {
    if (running.current || !undoId || !captureScope()()) return;
    running.current = true; setBusy(true); setError('');
    const valid = captureScope();
    try {
      const saved = await p.api(`${path}/undo`, {method: 'POST', body: JSON.stringify({request_id: newId(), delete_request_id: undoId})});
      if (!saved.restored || saved.identity !== p.identity || saved.source !== p.source) throw new Error('O servidor não confirmou o desfazer.');
      if (!valid()) return;
      setUndoId('');
      setDone('Exclusão futura desfeita. O histórico foi preservado.');
      Alert.alert('Exclusão futura desfeita', 'As parcelas futuras voltaram ao planejamento. Pagamentos já vinculados não foram alterados.');
      void Promise.resolve().then(p.onUpdated).catch(failure => {if (valid()) setError(`A reversão foi confirmada. Não foi possível atualizar a lista: ${failure.message}`);});
    } catch (failure: any) {
      if (valid()) setError(failure.message || 'Não foi possível desfazer.');
    } finally {running.current = false; if (valid()) setBusy(false);}
  };
  return <View style={s.box}>
    {!preview && <View style={s.actions}>
      {p.source !== 'managed' && <Button label="Editar futuras" accessibilityLabel={`Editar futuras de ${p.name}`} disabled={busy} onPress={() => open('edit')} />}
      <Button label="Remover parcelas futuras" accessibilityLabel={`Remover parcelas futuras de ${p.name}`} disabled={busy} onPress={() => open('delete')} />
    </View>}
    {preview && <>
      {conflict&&<View><Text style={s.warning}>A fonte mudou. Seus valores digitados foram preservados. Compare a origem, seu rascunho e o cronograma atual antes de confirmar.</Text><Button label="Carregar cronograma atual e conferir rascunho" disabled={busy} onPress={()=>open(mode,true)}/>
        {differences.map(difference=><View key={`${difference.row_id}:${difference.field}`} style={s.row}><Text style={s.title}>Parcela {difference.row_id} · {({amount:'Valor',due_date:'Vencimento',cancelled:'Exclusão',availability:'Disponibilidade'} as Record<string,string>)[difference.field]}</Text><Text style={s.note}>Origem: {String(difference.original??'não informado')}</Text><Text style={s.note}>Seu rascunho: {String(difference.local??'não informado')}</Text><Text style={s.warning}>Atual: {String(difference.current??'não informado')}</Text><Button label={difference.field==='availability'?`Retirar alteração indisponível da parcela ${difference.row_id}`:`Manter atual: parcela ${difference.row_id}, ${difference.field}`} disabled={busy} onPress={()=>resolve(difference,false)}/>{difference.field!=='availability'&&<Button label={`Aplicar meu valor: parcela ${difference.row_id}, ${difference.field}`} disabled={busy} onPress={()=>resolve(difference,true)}/>}</View>)}
      </View>}
      <Text style={s.title}>{mode === 'delete' ? 'Excluir compromissos futuros?' : 'Editar parcelas futuras'}</Text>
      <Text style={s.note}>{p.name}</Text>
      <Text style={s.note}>Pagamentos, parcelas faturadas e histórico ficam preservados.</Text>
      <Text style={s.note}>{closureWarning(p.kind === 'assinatura' ? 'assinatura' : 'financiamento')}</Text>
      {preview.needs_review && <Text style={s.warning}>A fonte importada mudou desde uma correção anterior. Confira novamente o cronograma.</Text>}
      {!preview.editable_count && <Text style={s.warning}>Nenhuma parcela futura disponível para alteração. Confira o status e os vínculos no histórico.</Text>}
      {mode === 'edit' && !confirmation && <>
        <Text style={s.note}>{preview.rows.length - rows.length === 1 ? '1 parcela do histórico ou indisponível preservada.' : `${preview.rows.length - rows.length} parcelas do histórico ou indisponíveis preservadas.`}</Text>
        {rows.slice(page * 10, page * 10 + 10).map(row => <View key={row.row_id} style={s.row}>
          <Text style={s.title}>Parcela {row.number || row.row_id}</Text>
          {!row.due_date && <Text style={s.note}>Mês previsto: {row.month} · dia não informado.</Text>}
          <Text style={s.note}>Valor (R$)</Text>
          <TextInput accessibilityLabel={`Valor da parcela ${row.number || row.row_id}`} value={row.amount} onChangeText={value => change(row.row_id, 'amount', value)} editable={!busy} keyboardType="decimal-pad" style={s.input} />
          <Text style={s.note}>Vencimento {row.due_date ? '' : '(dia opcional)'}</Text>
          <TextInput accessibilityLabel={`Vencimento da parcela ${row.number || row.row_id}`} value={row.date_input} onChangeText={value => change(row.row_id, 'date_input', value)} editable={!busy} placeholder="DD/MM/AAAA" placeholderTextColor="#92A4A2" style={s.input} />
          <Pressable accessibilityRole="checkbox" accessibilityLabel={`Remover parcela ${row.number || row.row_id}`} accessibilityState={{checked: row.cancelled}} aria-checked={row.cancelled} disabled={busy} onPress={() => change(row.row_id, 'cancelled', !row.cancelled)} style={s.check}>
            <Text style={s.warning}>{row.cancelled ? '☑' : '☐'} Remover esta parcela futura</Text>
          </Pressable>
        </View>)}
        {rows.length > 10 && <View style={s.actions}><Button label="Anteriores" disabled={busy || page === 0} onPress={() => setPage(value => value - 1)} /><Button label="Próximas" disabled={busy || (page + 1) * 10 >= rows.length} onPress={() => setPage(value => value + 1)} /></View>}
        {!!rows.length && <Button label="Revisar alterações" disabled={busy||conflict} onPress={prepare} />}
      </>}
      {confirmation && <View accessibilityRole="alert">
        <Text style={s.note}>{mode === 'delete' ? futureRemovalLabel(preview.editable_count, p.name, sumAmounts((preview.rows || []).filter((row: any) => row.editable).map((row: any) => row.amount || 0))) : confirmation.body.rows.length === 1 ? '1 parcela será alterada.' : `${confirmation.body.rows.length} parcelas serão alteradas.`}</Text>
        {mode === 'delete' && <Text style={s.note}>Pagamentos já realizados e já vinculados ficam fora desta remoção.</Text>}
        {p.source === 'managed' && <Text style={s.note}>A exclusão também interrompe as próximas renovações previstas deste cadastro.</Text>}
        {(confirmation.body.rows || []).map((row: any) => <Text key={row.row_id} style={s.note}>Parcela {preview.rows.find((item: any) => item.row_id === row.row_id)?.number || row.row_id}: {row.cancelled ? 'excluir' : `R$ ${Number(row.amount).toFixed(2).replace('.', ',')} · ${displayDate(row.due_date) || 'manter o mês previsto'}`}</Text>)}
        <Button label={busy ? 'Salvando…' : mode === 'delete' ? 'Confirmar remoção' : 'Confirmar alterações'} danger={mode === 'delete'} disabled={busy} onPress={() => send()} />
        {mode === 'edit' && <Button label="Voltar à edição" disabled={busy} onPress={() => setConfirmation(null)} />}
      </View>}
      <Button label="Cancelar" disabled={busy} onPress={back} />
      <DiscardDraftAction disabled={busy} owner={path} label="Excluir rascunho das parcelas" onDiscard={() => {setPreview(null);setRows([]);setConfirmation(null);setConflict(false);setError('');setDone('');}} />
    </>}
    {!!done && <Text accessibilityRole="alert" style={s.success}>{done}</Text>}
    {!!undoId && !preview && !p.onRemoved && <Button label="Desfazer exclusão de futuras" disabled={busy} onPress={undoDelete} />}
    {!!error && <Text accessibilityRole="alert" style={s.warning}>{error}</Text>}
  </View>;
}
const s = StyleSheet.create({box: {gap: 10, marginTop: 12}, actions: {flexDirection: 'row', flexWrap: 'wrap', gap: 8}, button: {minHeight: 48, padding: 12, borderRadius: 10, backgroundColor: '#1B4440', justifyContent: 'center', alignItems: 'center'},
  dangerButton: {backgroundColor: '#6B2931'}, buttonText: {color: '#F4F7F5', fontWeight: '700', fontSize: 13}, disabled: {opacity: .5}, title: {color: '#F4F7F5', fontWeight: '700', fontSize: 15}, note: {color: '#AFC1BF', fontSize: 13, lineHeight: 20}, warning: {color: '#F6C564', fontSize: 13, lineHeight: 20}, success: {color: '#65DEC2', fontSize: 13},
  row: {borderColor: '#315054', borderWidth: 1, borderRadius: 12, padding: 12, gap: 7, marginVertical: 8}, input: {minHeight: 48, padding: 11, borderRadius: 10, backgroundColor: '#18292C', color: '#F4F7F5', fontSize: 15}, check: {minHeight: 48, justifyContent: 'center'}});
