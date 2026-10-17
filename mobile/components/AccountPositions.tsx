import {Alert} from '../utils/appAlert';
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import ControlledChoice from './ControlledChoice';
import {ACCOUNT_TYPES, AccountProfile, accountLabel, profilePayload} from '../utils/accountProfile';
import {creationId} from '../utils/commitmentCreate';
import {rebaseDraft} from '../utils/draftConflict';
import DiscardDraftAction from './DiscardDraftAction';
import {isAbort} from '../utils/apiClient';
import {useAsyncScope} from '../utils/useAsyncScope';
import {parseMoneyInput} from '../utils/moneyInput';
import {maskCivilDate, parseCivilDate} from '../utils/formMask';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Position = AccountProfile & {balance: number | null; balance_reference?: number | null; balance_as_of?: string; balance_reference_date?: string | null; balance_reference_token?:string; movement_count: number;
  unassigned_capture_count: number; pending_transfer_count: number; legacy_account_ambiguous?: boolean};
type Props = {api: (path: string, init?: RequestInit) => Promise<any>; money: (value: number) => string;
  onUpdated?: (items: Position[]) => void | Promise<void>; onOpen?: () => void};
const normalizeAmount = (value: string) => parseMoneyInput(value, {signed:true});
const referenceLabel = (item: Position) => item.balance_reference_date
  ? `${item.balance_reference_date.split('-').reverse().join('/')} (saldo conferido no dia)`
  : item.balance_as_of ? new Date(item.balance_as_of).toLocaleString('pt-BR') : '';
function Action({label, onPress, disabled, secondary, danger}: {label: string; onPress: () => void;
  disabled?: boolean; secondary?: boolean; danger?: boolean}) {
  return <Pressable accessibilityRole="button" accessibilityState={{disabled: !!disabled}} aria-disabled={!!disabled} disabled={disabled}
    onPress={onPress} style={[s.action, secondary && s.secondary, disabled && s.disabled]}>
    <Text style={[s.actionText, secondary && s.secondaryText, danger && s.danger]}>{label}</Text>
  </Pressable>;
}

export default function AccountPositions({api, money, onUpdated, onOpen}: Props) {
  const [items, setItems] = useState<Position[]>([]);
  const [readInfo, setReadInfo] = useState<{consistency: string; at: string} | null>(null);
  const balanceToken=useRef(''), balanceRequest=useRef({payload:'',id:''});
  const [balanceEditing, setBalanceEditing] = useState<string | null>(null);
  const [balanceConflict, setBalanceConflict] = useState(false);
  const [draft, setDraft] = useState<Partial<AccountProfile> | null>(null);
  const profileBase = useRef<Partial<AccountProfile>>({});
  const [profileCurrent,setProfileCurrent]=useState<Partial<AccountProfile>>({});
  const [profileConflict,setProfileConflict] = useState(false), [profileDifferences,setProfileDifferences] = useState<string[]>([]);
  const [value, setValue] = useState(''), [referenceDate, setReferenceDate] = useState('');
  const [filter, setFilter] = useState('abertas'), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const running = useRef(false), requestId = useRef(''), loadGeneration = useRef(0);
  const captureScope = useAsyncScope();
  const balanceSource=items.find(item=>item.key===balanceEditing);
  const balanceDirty=Boolean(balanceEditing)&&(referenceDate!==''||value!==(balanceSource?.balance==null?'':Number(balanceSource.balance).toFixed(2).replace('.',',')));
  const back=()=>{const profileDirty=Boolean(draft)&&JSON.stringify(profilePayload(draft!))!==JSON.stringify(profilePayload(profileBase.current));return protectUnsavedBack(profileDirty||balanceDirty,()=>{if(balanceEditing)setBalanceEditing(null);else setDraft(null);});};
  useLocalBack(back,Boolean(draft||balanceEditing),20);
  const load = useCallback(async (signal?: AbortSignal) => {
    const current = ++loadGeneration.current, valid=captureScope();
    const response = await api('/v1/account-positions', {signal});
    if(!valid() || current!==loadGeneration.current || signal?.aborted) throw Object.assign(new Error('Atualização interrompida'),{name:'AbortError'});
    if(current === loadGeneration.current && !signal?.aborted){
      setItems(response.items || []);
      setReadInfo({consistency: response.read_consistency || 'best_effort', at: response.read_at || ''});
    }
    return response.items || [];
  }, [api,captureScope]);
  useEffect(() => {
    const counter=loadGeneration;
    const controller = new AbortController();
    void load(controller.signal).catch(failure => {if (!controller.signal.aborted && !isAbort(failure)) setError(failure.message);})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => {controller.abort(); counter.current++;};
  }, [load]);
  const refreshAfterWrite = async () => {
    const valid = captureScope();
    const failed = (failure: any) => {if (valid()) setError(`A alteração foi salva. Não foi possível atualizar a lista: ${failure.message}`);};
    try {const rows = await load(); if(valid()) void Promise.resolve().then(() => onUpdated?.(rows)).catch(failed);}
    catch (failure: any) {failed(failure);}
  };
  const mutate = async (work: (valid:()=>boolean) => Promise<void>) => {
    if (running.current || !captureScope()()) return;
    running.current = true; setBusy(true); setError('');
    const valid = captureScope();
    try {await work(valid);} catch (failure: any) {
      if (!valid()) return;
      if (balanceEditing && [409, 428].includes(failure.status)) setBalanceConflict(true);
      if (draft?.key && [409,428].includes(failure.status)) {setProfileConflict(true);setProfileDifferences([]);}
      setError(failure.message || 'Não foi possível concluir.');
    }
    finally {running.current = false; if(valid()) setBusy(false);}
  };
  const change = (field: keyof AccountProfile, next: string) => setDraft(current => ({...current, [field]: next}));
  const open = (item?: Position) => {
    onOpen?.();
    setError(''); setBalanceEditing(null); requestId.current = creationId();
    const source = item ? {...item} : {instituicao: '', apelido: '', tipo: 'conta_corrente', status: 'aberta', moeda: 'BRL'};
    profileBase.current = source;setProfileConflict(false);setProfileDifferences([]);setDraft(source);
  };
  const saveProfile = () => mutate(async valid => {
    if (!draft || profileConflict) return;
    const payload = profilePayload(draft);
    if (!payload.instituicao.trim()) throw new Error('Informe a instituição.');
    for (const key of ['data_abertura', 'data_encerramento'] as const) {
      const raw = payload[key];
      if (raw) {
        const iso = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : parseCivilDate(raw);
        if (!iso) throw new Error('Use dd/mm/aaaa ou AAAA-MM-DD nas datas, ou deixe vazio.');
        payload[key] = iso;
      }
    }
    await api(draft.key ? `/v1/account-profiles/${draft.key}` : '/v1/account-profiles', {
      method: draft.key ? 'PATCH' : 'POST', body: JSON.stringify({...payload,
        ...(draft.key ? {expected_revision: draft.revision} : {request_id: requestId.current})}),
    });
    if(!valid())return;
    setFilter(payload.archived ? 'arquivadas' : payload.status === 'encerrada' ? 'encerradas' : 'abertas');
    setDraft(null); onOpen?.(); Alert.alert('Conta salva', 'Os dados da conta foram atualizados.'); await refreshAfterWrite();
  });
  const archive = (item: Partial<AccountProfile>) => mutate(async valid => {
    await api(`/v1/account-profiles/${item.key}`, {method: 'PATCH', body: JSON.stringify({
      ...profilePayload(item), archived: !item.archived, expected_revision: item.revision,
    })});
    if(!valid())return;
    setDraft(null); onOpen?.(); setFilter(item.archived ? item.status === 'encerrada' ? 'encerradas' : 'abertas' : 'arquivadas');
    Alert.alert(item.archived ? 'Conta restaurada' : 'Conta arquivada', 'O histórico da conta foi preservado.');
    await refreshAfterWrite();
  });
  const remove = (item: Partial<AccountProfile>) => mutate(async valid => {
    const preview = await api(`/v1/account-profiles/${item.key}/deletion`);
    if(!valid())return;
    if (!preview.can_delete) {
      Alert.alert('Conta com histórico', 'Esta conta tem histórico ou vínculos. Você pode arquivá-la e restaurá-la depois.', [
        {text: 'Cancelar', style: 'cancel'}, ...(!item.archived ? [{text: 'Arquivar', onPress: () => valid() ? archive(item) : undefined}] : []),
      ]); return;
    }
    Alert.alert('Excluir conta', `Excluir ${accountLabel(item)}? Esta conta não tem histórico ou vínculos.`, [
      {text: 'Cancelar', style: 'cancel'}, {text: 'Excluir', style: 'destructive', onPress: () => valid() ? mutate(async valid => {
        await api(`/v1/account-profiles/${item.key}`, {method: 'DELETE', body: JSON.stringify({expected_revision: preview.revision})});
        if(!valid())return;setDraft(null); onOpen?.(); Alert.alert('Conta excluída', 'A conta sem histórico ou vínculos foi excluída.'); await refreshAfterWrite();
      }) : undefined},
    ]);
  });
  const saveBalance = (key: string) => mutate(async valid => {
    const amount = normalizeAmount(value);
    if (amount === null) throw new Error('Informe um saldo válido, como 1.234,56.');
    const refIso = referenceDate ? (/^\d{4}-\d{2}-\d{2}$/.test(referenceDate.trim()) ? referenceDate.trim() : parseCivilDate(referenceDate.trim())) : '';
    if (referenceDate && !refIso) throw new Error('Use dd/mm/aaaa ou AAAA-MM-DD, ou deixe a data vazia.');
    const payload={amount,...(refIso?{as_of_date:refIso}:{}),expected_token:balanceToken.current};
    const serialized=JSON.stringify(payload);
    if(balanceRequest.current.payload!==serialized)balanceRequest.current={payload:serialized,id:creationId()};
    await api(`/v1/account-positions/${key}/balance-reference`,{method:'PUT',body:JSON.stringify({...payload,request_id:balanceRequest.current.id})});
    if(!valid())return;setBalanceEditing(null); setBalanceConflict(false); Alert.alert('Saldo de referência salvo', 'A referência foi registrada.'); await refreshAfterWrite();
  });
  const reconcile = () => mutate(async valid => {
    const result = await api('/v1/transfers/reconcile', {method: 'POST'});
    if(!valid())return;Alert.alert('Conciliação concluída', `${result.updated_count || 0} transferência(s) atualizada(s).`); await refreshAfterWrite();
  });
  const field = (label: string, name: keyof AccountProfile, maxLength: number, placeholder?: string, multiline = false) =>
    <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label}
      editable={!busy} value={String(draft?.[name] || '')} onChangeText={next => change(name, next)}
      maxLength={maxLength} placeholder={placeholder} placeholderTextColor="#92A4A2" multiline={multiline}
      autoCapitalize="none" style={[s.input, multiline && s.notes]} /></View>;
  const visible = items.filter(item => filter === 'todas' || (filter === 'arquivadas' ? item.archived : !item.archived &&
    (filter === 'encerradas' ? item.status === 'encerrada' : item.status !== 'encerrada')));
  const savedDraft = draft?.key ? items.find(item => item.key === draft.key) : null;
  const unsaved = !!savedDraft && JSON.stringify(profilePayload(savedDraft)) !== JSON.stringify(profilePayload(draft || {}));
  return <View>
    {!!error && <View accessibilityRole="alert" style={s.error}><Text style={s.warning}>{error}</Text>
      <Action label="Atualizar lista" secondary disabled={busy} onPress={() => mutate(async valid => {await load();})}/></View>}
    {draft ? <View>
      <Text accessibilityRole="header" style={s.title}>{draft.key ? 'Detalhes da conta' : 'Nova conta'}</Text>
      <Text style={s.help}>Os dados do cadastro podem ser editados. O saldo é conferido separadamente.</Text>
      {profileConflict && <View><Text accessibilityRole="alert" style={s.warning}>O cadastro mudou. Seu rascunho foi preservado; confira a versão atual.</Text>
        <Action label="Carregar cadastro atual e conferir" secondary disabled={busy} onPress={()=>mutate(async()=>{
          const rows:Position[]=await load(), current=rows.find(row=>row.key===draft.key);
          if(!current)throw new Error('O cadastro não está mais disponível. Seu rascunho permanece nesta tela.');
          const rebased=rebaseDraft(profilePayload(profileBase.current),profilePayload(current),profilePayload(draft));
          profileBase.current=current;setProfileCurrent(current);setDraft({...current,...rebased.draft});setProfileDifferences(rebased.conflicts);setProfileConflict(rebased.conflicts.length>0);
        })}/>{profileDifferences.map(field=><Text key={field} style={s.warning}>{field}: atual “{String((profileCurrent as any)[field]??'vazio')}”; seu rascunho “{String((draft as any)[field]??'vazio')}”.</Text>)}
        {profileDifferences.length>0&&<Action label="Conferi as diferenças e quero reaplicar meus campos" secondary disabled={busy} onPress={()=>{setProfileConflict(false);setProfileDifferences([])}}/>}</View>}
      {field('Apelido (opcional)', 'apelido', 80, 'Ex.: Itaú principal')}
      {field('Instituição', 'instituicao', 100, 'Banco, corretora ou carteira')}
      <ControlledChoice disabled={busy} label="Tipo de conta" value={draft.tipo || 'conta_corrente'} options={Object.keys(ACCOUNT_TYPES)}
        display={key => ACCOUNT_TYPES[key] || key} onChange={next => {if (!busy) change('tipo', next);}} allowClear={false}/>
      {field('Agência (opcional)', 'agencia', 20)}
      {field('Dígito da agência (opcional)', 'agencia_digito', 5)}
      {field('Número da conta (opcional)', 'numero', 30)}
      {field('Dígito da conta (opcional)', 'numero_digito', 5)}
      <ControlledChoice disabled={busy} label="Status" value={draft.status || 'aberta'} options={['aberta', 'encerrada']}
        display={key => key === 'aberta' ? 'Aberta' : 'Encerrada'} allowClear={false}
        onChange={next => {if (!busy) setDraft(current => ({...current, status: next, data_encerramento: next === 'aberta' ? null : current?.data_encerramento}));}}/>
      {field('Data de abertura (opcional)', 'data_abertura', 10, 'AAAA-MM-DD')}
      {draft.status === 'encerrada' && field('Data de encerramento (opcional)', 'data_encerramento', 10, 'AAAA-MM-DD')}
      <Text style={s.meta}>Moeda: BRL. Os saldos e lançamentos deste app são acompanhados em reais.</Text>
      {field('Observações (opcional)', 'observacoes', 1000, 'Informações úteis sobre esta conta', true)}
      <Action label={busy ? 'Salvando…' : 'Salvar cadastro'} disabled={busy||profileConflict} onPress={saveProfile}/>
      <Action label="Voltar sem salvar" secondary disabled={busy} onPress={back}/>
      <DiscardDraftAction disabled={busy} owner={draft.key || 'new-account'} label="Excluir rascunho da conta" onDiscard={() => {setDraft(null);setProfileConflict(false);setProfileDifferences([]);setError('');onOpen?.();}} />
      {!!draft.key && <View style={s.management}>
        <Text style={s.help}>Encerrar informa a situação no banco. Arquivar retira a conta da lista principal e mantém seu histórico.</Text>
        {unsaved && <Text style={s.warning}>Salve ou descarte as alterações antes de arquivar ou excluir.</Text>}
        <Action label={draft.archived ? 'Restaurar conta' : 'Arquivar conta'} secondary disabled={busy || unsaved}
          onPress={() => archive(items.find(item => item.key === draft.key) || draft)}/>
        <Action label="Excluir conta" secondary danger disabled={busy || unsaved}
          onPress={() => remove(items.find(item => item.key === draft.key) || draft)}/>
      </View>}
    </View> : <>
      <Text accessibilityRole="header" style={s.title}>Minhas contas</Text>
      <Text style={s.help}>Cadastre suas contas e informe o saldo conferido no banco ou na carteira. O app soma os lançamentos posteriores.</Text>
      <Action label="Adicionar conta" disabled={busy} onPress={() => open()}/>
      <View style={s.filters}>{[['abertas', 'Abertas'], ['encerradas', 'Encerradas'], ['arquivadas', 'Arquivadas'], ['todas', 'Todas']].map(([key, label]) =>
        <Pressable key={key} accessibilityRole="button" accessibilityState={{selected: filter === key}} aria-pressed={filter === key}
          disabled={busy} onPress={() => {setFilter(key); setBalanceEditing(null);}} style={[s.filter, filter === key && s.filterActive]}>
          <Text style={filter === key ? s.filterSelectedText : s.secondaryText}>{label}</Text>
        </Pressable>)}</View>
      {readInfo && <Text style={s.meta}>{readInfo.consistency === 'snapshot' ? 'Leitura coerente' : 'Saldos a confirmar'}
        {readInfo.at ? ` · conferida em ${new Date(readInfo.at).toLocaleString('pt-BR')}` : ''}</Text>}
      {loading && <Text style={s.help}>Carregando contas…</Text>}
      {!loading && !visible.length && <Text style={s.help}>Nenhuma conta neste filtro.</Text>}
      {visible.map(item => <View key={item.key} style={s.card}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Detalhes de ${accountLabel(item)}`} disabled={busy} onPress={() => open(item)}>
          <View style={s.row}><Text style={s.name}>{item.apelido || item.instituicao}</Text>
            <Text style={s.status}>{item.archived ? 'Arquivada · ' : ''}{item.status === 'encerrada' ? 'Encerrada' : 'Aberta'}</Text></View>
          <Text style={s.meta}>{item.apelido ? `${item.instituicao} · ` : ''}{ACCOUNT_TYPES[item.tipo] || item.tipo}
            {item.numero ? ` · final ${item.numero.slice(-4)}` : ''}</Text>
          <Text style={s.balance}>{item.balance == null ? 'Saldo a confirmar' : money(Number(item.balance))}</Text>
          <Text style={s.link}>Ver detalhes e editar ›</Text>
        </Pressable>
        <Text style={s.meta}>{item.tipo === 'criptoativos' ? 'Equivalente em BRL informado por você · ' : ''}
          {item.balance_as_of ? `Referência: ${referenceLabel(item)} · ${item.movement_count} lançamento(s) após a referência` : 'Sem saldo de referência'}</Text>
        {item.tipo === 'criptoativos' && Boolean(item.balance_reference_date || item.balance_as_of) && (Date.now() - Date.parse(String(item.balance_reference_date || item.balance_as_of))) > 30 * 86400000 && <Text style={s.warning}>Referência de criptoativos com mais de 30 dias. Atualize o saldo equivalente em BRL.</Text>}
        {item.unassigned_capture_count > 0 && <Text style={s.warning}>{item.unassigned_capture_count} captura(s) sem conta indicada podem alterar este saldo.</Text>}
        {item.pending_transfer_count > 0 && <Text style={s.warning}>{item.pending_transfer_count} transferência(s) aguardam conciliação. O saldo inclui essas movimentações informadas.</Text>}
        {item.legacy_account_ambiguous && <Text style={s.warning}>Há mais de uma conta histórica deste banco. Os lançamentos importados não foram somados automaticamente a este saldo.</Text>}
        {balanceEditing === item.key ? <View>
          {balanceConflict && <Action label="Carregar referência atual e conferir" secondary disabled={busy} onPress={() => mutate(async valid => {
            const rows: Position[] = await load(); const current = rows.find(row => row.key === item.key);
            if (!current) throw new Error('A conta não está mais disponível.');
            balanceToken.current = current.balance_reference_token || ''; balanceRequest.current = {payload:'',id:''};
            setBalanceConflict(false);
            Alert.alert('Referência atual carregada', `${current.balance_reference == null ? 'Sem saldo de referência' : money(Number(current.balance_reference))}${current.balance_as_of ? ` · ${referenceLabel(current)}` : ''}. Seu valor digitado foi preservado. Confira antes de salvar.`);
          })}/>}
          <TextInput accessibilityLabel="Saldo conferido em reais" editable={!busy} value={value} onChangeText={setValue} keyboardType="decimal-pad" placeholder="Saldo em R$" placeholderTextColor="#92A4A2" style={s.input}/>
          <TextInput accessibilityLabel="Data do saldo" editable={!busy} value={referenceDate} onChangeText={text => setReferenceDate(/^\d{4}-/.test(text) ? text : maskCivilDate(text))} placeholder="Data do saldo (dd/mm/aaaa ou AAAA-MM-DD), opcional" placeholderTextColor="#92A4A2" style={s.input}/>
          <Text style={s.meta}>Deixe a data vazia para usar o saldo de agora. Uma data anterior permite conferir os lançamentos posteriores.</Text>
          <Action label="Salvar saldo conferido" disabled={busy || balanceConflict} onPress={() => saveBalance(item.key)}/>
          <Action label="Cancelar" secondary disabled={busy} onPress={back}/>
          <DiscardDraftAction disabled={busy} owner={item.key} label="Excluir rascunho do saldo" onDiscard={() => {setBalanceEditing(null);setValue('');setReferenceDate('');setBalanceConflict(false);setError('');}} />
        </View> : <Action label={item.balance == null ? 'Informar saldo' : 'Atualizar saldo conferido'} secondary disabled={busy}
          onPress={() => {setError(''); setBalanceConflict(false); balanceToken.current=item.balance_reference_token||'';balanceRequest.current={payload:'',id:''};setBalanceEditing(item.key); setReferenceDate(''); setValue(item.balance == null ? '' : Number(item.balance).toFixed(2).replace('.', ','));}}/>}
      </View>)}
      <Action label="Conciliar transferências com extratos" disabled={busy} onPress={reconcile}/>
    </>}
  </View>;
}

const s = StyleSheet.create({
  title: {color: '#F4F7F5', fontSize: 20, fontWeight: '700', marginBottom: 6},
  help: {color: '#AFC1BF', fontSize: 13, lineHeight: 20, marginBottom: 10},
  card: {backgroundColor: '#142326', borderRadius: 13, padding: 14, marginTop: 10},
  row: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10},
  name: {color: '#F4F7F5', fontSize: 16, fontWeight: '700', flex: 1},
  status: {color: '#67DFC3', fontSize: 12, fontWeight: '700'},
  balance: {color: '#F4F7F5', fontSize: 22, fontWeight: '700', marginTop: 10},
  meta: {color: '#AFC1BF', fontSize: 12, lineHeight: 18, marginTop: 5},
  warning: {color: '#F9C978', fontSize: 13, lineHeight: 20},
  error: {backgroundColor: '#30251A', borderRadius: 10, padding: 12, marginBottom: 12},
  link: {color: '#67DFC3', fontSize: 13, fontWeight: '700', paddingVertical: 12},
  field: {marginBottom: 14}, label: {color: '#AFC1BF', fontSize: 12, fontWeight: '600', marginBottom: 6},
  input: {minHeight: 48, backgroundColor: '#0B191B', borderRadius: 10, borderWidth: 1, borderColor: '#315054', color: '#F4F7F5', padding: 12, marginTop: 5},
  notes: {minHeight: 90, textAlignVertical: 'top'},
  action: {minHeight: 48, backgroundColor: '#49C5AA', padding: 13, borderRadius: 10, alignItems: 'center', justifyContent: 'center', marginTop: 10},
  actionText: {color: '#061412', fontWeight: '800', textAlign: 'center'},
  secondary: {backgroundColor: '#142326', borderWidth: 1, borderColor: '#315054'},
  secondaryText: {color: '#C5D8D4', fontWeight: '600'}, danger: {color: '#FF9C94'}, disabled: {opacity: 0.5},
  management: {borderTopWidth: 1, borderTopColor: '#315054', marginTop: 20, paddingTop: 16},
  filters: {flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginVertical: 14},
  filter: {minHeight: 44, padding: 10, borderRadius: 10, backgroundColor: '#142326'},
  filterActive: {backgroundColor: '#49C5AA'}, filterSelectedText: {color: '#061412', fontWeight: '700'},
});