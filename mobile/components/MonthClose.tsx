import React, { useCallback, useEffect, useRef, useState } from 'react';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { Alert } from '../utils/appAlert';
import SingleLineValue from './SingleLineValue';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {requireMoneyInput} from '../utils/moneyInput';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Item = { id: string; title: string; help: string; status: 'pending' | 'done' | 'not_applicable'; note: string; completed: boolean; needs_review: boolean; can_not_apply: boolean; blockers: string[]; source_token: string };
type Balance = { opening: string | null; closing: string | null; flow: string; expected: string | null; difference: string | null; cash_account: boolean };
type Group = { id: string; title: string; kind: 'general' | 'account' | 'card'; items: Item[]; balance: Balance | null; detail: string; completed: number; total: number };
type Checklist = { month: string; start: string; end: string; revision: number; status: string; closed_at: string | null; source_changed: boolean; groups: Group[]; completed: number; total: number; progress: number; can_close: boolean; month_ended: boolean; attestation: string; totals: { income: string; expenses: string }; last_close?: { totals: { income: string; expenses: string } } };
type Props = { api: (path: string, init?: RequestInit) => Promise<any>; money: (value: number) => string; onBack: () => void; onNavigate: (tab: 'Contas' | 'Cartões' | 'Movimentações' | 'Revisão' | 'Compromissos') => void };
const previousMonth = () => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit' }).formatToParts(new Date());
  const year = Number(parts.find(p => p.type === 'year')!.value), month = Number(parts.find(p => p.type === 'month')!.value);
  return `${month === 1 ? year - 1 : year}-${String(month === 1 ? 12 : month - 1).padStart(2, '0')}`;
};
const shiftMonth = (month: string, offset: number) => { const [year, number] = month.split('-').map(Number); const next = new Date(Date.UTC(year, number - 1 + offset, 1)); return `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}`; };
const dateLabel = (value: string) => value.split('-').reverse().join('/');
const cleanPastedMoney = (raw: string) => raw.replace(/[\u00A0\s]+/g, '').replace(/^([+-]?)R\$/i, '$1');
const decimal = (raw: string) => requireMoneyInput(cleanPastedMoney(raw), {signed:true});
const newId = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const n = Math.floor(Math.random() * 16); return (c === 'x' ? n : (n & 3) | 8).toString(16); });

function Action({ label, onPress, disabled = false, primary = false, accessibilityLabel }: { label: string; onPress: () => void; disabled?: boolean; primary?: boolean; accessibilityLabel?: string }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel || label} disabled={disabled} onPress={onPress} style={[s.action, primary && s.primary, disabled && s.disabled]}><Text style={primary ? s.primaryText : s.link}>{label}</Text></Pressable>;
}

export default function MonthClose({ api, money, onBack, onNavigate }: Props) {
  const [month, setMonth] = useState(previousMonth), [monthInput, setMonthInput] = useState(previousMonth);
  const [data, setData] = useState<Checklist | null>(null), [loading, setLoading] = useState(true), [saving, setSaving] = useState(false), [error, setError] = useState('');
  const [expanded, setExpanded] = useState('general'), [onlyPending, setOnlyPending] = useState(false);
  const [editingItem, setEditingItem] = useState<Item | null>(null), [note, setNote] = useState('');
  const [balanceEditing, setBalanceEditing] = useState<Group | null>(null), [opening, setOpening] = useState(''), [closing, setClosing] = useState('');
  const [customGroup, setCustomGroup] = useState<string | null>(null), [customTitle, setCustomTitle] = useState(''), [customId, setCustomId] = useState(newId);
  const [draftRevision,setDraftRevision] = useState<number | null>(null);
  const [draftConflict,setDraftConflict] = useState(false);
  const [readVersion,setReadVersion]=useState(0),[failedReadVersion,setFailedReadVersion]=useState(0);
  const running = useRef(false), generation = useRef(0);
  const path = `/v1/month-close/${month}`;
  const captureScope = useAsyncScope(month);
  const load = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true); setError('');
    try { const response = await api(`/v1/month-close/${month}`); if (current === generation.current) {setData(response);setReadVersion(version=>version+1);} }
    catch (failure: any) { if (current === generation.current) setError(failure.message || 'Não foi possível carregar o fechamento.'); }
    finally { if (current === generation.current) setLoading(false); }
  }, [api, month]);
  useEffect(() => { const counter = generation; const timer = setTimeout(() => load(), 0); return () => { clearTimeout(timer); counter.current++; }; }, [load]);
  const resetForms = () => { setDraftRevision(null);setDraftConflict(false);setEditingItem(null); setBalanceEditing(null); setCustomGroup(null); setNote(''); setOpening('');setClosing('');setCustomTitle(''); setCustomId(newId()); };
  const dirty=Boolean(editingItem&&note!==editingItem.note||customGroup&&customTitle||balanceEditing&&(opening!==(balanceEditing.balance?.opening?.replace('.',',')||'')||closing!==(balanceEditing.balance?.closing?.replace('.',',')||'')));
  const back=()=>protectUnsavedBack(dirty,()=>{if(editingItem||balanceEditing||customGroup)resetForms();else onBack();});
  useLocalBack(back,true,10);
  const selectMonth = (value: string) => {
    if (running.current || loading || saving) return;
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value) || Number(value.slice(0, 4)) < 1900 || Number(value.slice(0, 4)) > 2200) {
      setError('Use mês AAAA-MM, com ano entre 1900 e 2200.'); return;
    }
    resetForms(); setMonthInput(value); setExpanded('general');
    if (value === month) return load();
    setData(null); setMonth(value);
  };
  const mutate = async (suffix: string, method: string, body: Record<string, unknown>) => {
    if (!data || running.current || loading || !captureScope()()) return;
    running.current = true; setSaving(true); setError('');
    const valid = captureScope();
    try {
      const response = await api(path + suffix, { method, body: JSON.stringify({ ...body, expected_revision: draftRevision ?? data.revision }) });
      if (!valid()) return;
      setData(response); resetForms();
    } catch (failure: any) {
      if (!valid()) return;
      // Preserve the form and its draft; refreshing is an explicit recovery step.
      if(failure.status===409||failure.status===428){setDraftConflict(true);setFailedReadVersion(readVersion);}
      setError(failure.message || 'Não foi possível salvar a conferência.');
    } finally { running.current = false; if(valid()) setSaving(false); }
  };
  const locked = loading || saving || !data || data.status === 'closed';
  const mark = (item: Item, status: Item['status'], itemNote = item.note) => mutate(`/items/${encodeURIComponent(item.id)}`, 'PATCH', { status, note: itemNote, expected_source_token: editingItem?.id===item.id ? editingItem.source_token : item.source_token });
  const saveBalances = () => {
    if (!balanceEditing) return;
    try { return mutate(`/balances/${encodeURIComponent(balanceEditing.id)}`, 'PUT', { opening: decimal(opening), closing: decimal(closing) }); }
    catch (failure: any) { setError(failure.message); }
  };
  const close = () => {const valid=captureScope();Alert.alert('Concluir o checklist do mês?', 'Você declara ter comparado todos os dados com extratos e faturas. As marcações ficarão salvas; novos dados pedirão revisão. Essa ação não altera lançamentos nem faz pagamentos.', [
    { text: 'Cancelar', style: 'cancel' }, { text: 'Concluir', onPress: () => valid() ? mutate('/close', 'POST', {}) : undefined },
  ]);};
  const beginBalances = (group: Group) => {
    setEditingItem(null); setCustomGroup(null); setBalanceEditing(group);
    setDraftRevision(data?.revision??null);setDraftConflict(false);
    setOpening(group.balance?.opening?.replace('.', ',') || ''); setClosing(group.balance?.closing?.replace('.', ',') || '');
  };
  const groups = data?.groups || [];
  return <View>
    <Action label="‹ Voltar para Mais" onPress={back} disabled={saving} />
    <Text style={s.heading}>Fechamento do mês</Text><Text style={s.help}>Reúna, confira e marque os dados de todas as contas e cartões. O progresso fica salvo por mês.</Text>
    <View style={s.monthRow}><Action label="‹ Mês anterior" disabled={loading || saving} onPress={() => selectMonth(shiftMonth(month, -1))} /><Action label="Próximo mês ›" disabled={loading || saving} onPress={() => selectMonth(shiftMonth(month, 1))} /></View>
    <View style={s.monthRow}><TextInput accessibilityLabel="Mês do fechamento (AAAA-MM)" editable={!loading && !saving} value={monthInput} onChangeText={setMonthInput} onSubmitEditing={() => selectMonth(monthInput)} maxLength={7} placeholder="AAAA-MM" placeholderTextColor="#92A4A2" style={[s.input, s.monthInput]} /><Action label="Abrir mês" disabled={loading || saving} onPress={() => selectMonth(monthInput)} /></View>
    {loading && <ActivityIndicator color="#49C5AA" style={s.spinner} />}
    {!!error && <View style={s.errorBox}><Text style={s.error}>{error}</Text><Action label="Atualizar checklist" disabled={loading || saving} onPress={() => load()} /></View>}
    {draftConflict && data && <View style={s.errorBox}><Text style={s.error}>Seu rascunho usa uma versão anterior. Atualize o checklist e compare os dados atuais antes de reaplicá-lo.</Text>
      <Action label="Conferi o checklist atual e quero reaplicar o rascunho" disabled={loading||saving||readVersion===failedReadVersion} onPress={()=>{
        if(editingItem){const fresh=data.groups.flatMap(group=>group.items).find(item=>item.id===editingItem.id);if(!fresh){setError('O item não existe mais. Sua observação foi preservada.');return;}setEditingItem(fresh);}
        if(balanceEditing){const fresh=data.groups.find(group=>group.id===balanceEditing.id);if(!fresh){setError('A conta não existe mais neste mês. Seus saldos foram preservados.');return;}setBalanceEditing(fresh);}
        setDraftRevision(data.revision);setDraftConflict(false);setError('');
      }}/></View>}
    {data && <>
      <View style={s.summary}>
        <Text style={s.title}>{dateLabel(data.start)} a {dateLabel(data.end)}</Text>
        <Text style={s.progress}>{data.completed} de {data.total} itens · {data.progress}%</Text>
        <View accessibilityRole="progressbar" aria-valuemin={0} aria-valuemax={data.total} aria-valuenow={data.completed} style={s.progressTrack}><View style={[s.progressFill, { width: `${data.progress}%` }]} /></View>
        <Text style={s.help}>{data.status === 'closed' ? data.source_changed ? 'Dados mudaram após a conclusão. Reabra para conferir os itens afetados.' : 'Checklist concluído por sua conferência.' : 'Em conferência'}{!data.month_ended ? ' · Mês ainda em andamento.' : ''}</Text>
        {data.source_changed && data.last_close && <Text style={s.help}>Na conclusão anterior: receitas {money(Number(data.last_close.totals.income))} · gastos {money(Number(data.last_close.totals.expenses))}.</Text>}
        <Text style={s.help}>Receitas e gastos seguem a competência declarada; sem competência, usam a data do movimento em Brasília. Os saldos e fluxos das contas abaixo seguem a data efetiva do extrato.</Text>
        <View style={s.totalRow}><Text style={s.label}>Receitas cadastradas</Text><View style={s.totalValue}><SingleLineValue style={s.money}>{money(Number(data.totals.income))}</SingleLineValue></View></View>
        <View style={s.totalRow}><Text style={s.label}>Gastos cadastrados</Text><View style={s.totalValue}><SingleLineValue style={s.money}>{money(Number(data.totals.expenses))}</SingleLineValue></View></View>
        <Text style={s.help}>{data.attestation}</Text>
        <View style={s.actions}><Action label={onlyPending ? 'Mostrar todos os itens' : 'Somente pendentes'} onPress={() => setOnlyPending(!onlyPending)} disabled={saving} /><Action label="Atualizar dados" onPress={() => load()} disabled={loading || saving} /></View>
      </View>
      {groups.map(group => {
        const visible = onlyPending ? group.items.filter(item => !item.completed) : group.items;
        return <View key={group.id} style={s.group}>
          <Pressable accessibilityRole="button" accessibilityLabel={`Checklist de ${group.title}`} aria-expanded={expanded === group.id} onPress={() => { if (!saving) { resetForms(); setExpanded(expanded === group.id ? '' : group.id); } }} style={s.groupHeader}>
            <Text style={s.kind}>{group.kind === 'account' ? 'CONTA' : group.kind === 'card' ? 'CARTÃO' : 'MÊS'}</Text><Text style={s.title}>{group.title}</Text><Text style={s.link}>{group.completed}/{group.total} conferidos · {expanded === group.id ? 'Recolher ⌃' : 'Abrir ›'}</Text>
          </Pressable>
          {expanded === group.id && <View style={s.groupBody}>
            <Text style={s.help}>{group.detail}</Text>
            {group.balance && <View style={s.balanceBox}>
              <Text style={s.title}>Conciliação dos saldos</Text>
              <Text style={s.help}>Abertura: {group.balance.opening === null ? 'a informar' : money(Number(group.balance.opening))}{'\n'}Movimentos cadastrados: {money(Number(group.balance.flow))}{'\n'}Encerramento: {group.balance.closing === null ? 'a informar' : money(Number(group.balance.closing))}</Text>
              {group.balance.difference !== null && <Text style={Number(group.balance.difference) ? s.error : s.link}>Diferença: {money(Number(group.balance.difference))}</Text>}
              {!group.balance.cash_account && <Text style={s.help}>Posições de investimento podem variar por valorização. Explique a diferença nas observações do item de saldos.</Text>}
              <Action label="Informar saldos" accessibilityLabel={`Informar saldos de ${group.title}`} disabled={locked} onPress={() => beginBalances(group)} />
              {balanceEditing?.id === group.id && <View>
                <Text style={s.label}>Saldo antes de {dateLabel(data.start)}</Text><TextInput accessibilityLabel="Saldo de abertura do mês" editable={!locked} keyboardType="decimal-pad" value={opening} onChangeText={setOpening} style={s.input} />
                <Text style={s.label}>Saldo ao final de {dateLabel(data.end)}</Text><TextInput accessibilityLabel="Saldo de encerramento do mês" editable={!locked} keyboardType="decimal-pad" value={closing} onChangeText={setClosing} style={s.input} />
                <View style={s.actions}><Action label="Salvar saldos do mês" primary disabled={locked||draftConflict} onPress={saveBalances} /><Action label="Cancelar saldos" disabled={saving} onPress={back} /></View>
                <DiscardDraftAction disabled={locked} owner={`${month}:${group.id}:balances`} label="Excluir rascunho dos saldos" onDiscard={resetForms} />
              </View>}
            </View>}
            {!visible.length && <Text style={s.help}>Nenhuma pendência nesta seção.</Text>}
            {visible.map(item => <View key={item.id} style={s.item}>
              <Pressable accessibilityRole="checkbox" accessibilityLabel={item.title} aria-checked={item.completed} aria-disabled={locked} disabled={locked} onPress={() => mark(item, item.completed ? 'pending' : 'done')} style={s.checkRow}>
                <View style={[s.checkbox, item.completed && s.checked]}><Text style={s.checkmark}>{item.completed ? '✓' : ''}</Text></View><Text style={s.itemTitle}>{item.title}</Text>
              </Pressable>
              <Text style={s.help}>{item.help}</Text>
              {item.needs_review && <Text style={s.error}>Dados alterados: conferir novamente.</Text>}
              {item.status === 'not_applicable' && <Text style={s.link}>Não se aplica · {item.note}</Text>}
              {item.status !== 'not_applicable' && !!item.note && <Text style={s.help}>Observações: {item.note}</Text>}
              {item.blockers.map(blocker => <Text key={blocker} style={s.error}>{blocker}</Text>)}
              <Action label="Observações / Não se aplica" accessibilityLabel={`Observações de ${item.title}`} disabled={locked} onPress={() => { setBalanceEditing(null); setCustomGroup(null); setEditingItem(item); setNote(item.note); setDraftRevision(data.revision);setDraftConflict(false); }} />
              {editingItem?.id === item.id && <View style={s.noteBox}>
                <Text style={s.label}>Fontes, comprovantes, exceções ou motivo de não aplicação</Text><TextInput accessibilityLabel="Observação da conferência" editable={!locked} multiline maxLength={2000} value={note} onChangeText={setNote} placeholder="Referência do extrato/fatura e o que foi conferido" placeholderTextColor="#92A4A2" style={s.input} />
                <View style={s.actions}><Action label="Salvar observação" disabled={locked||draftConflict} onPress={() => mark(item, item.needs_review ? 'pending' : item.status, note)} />{item.can_not_apply && <Action label="Não se aplica" disabled={locked || draftConflict || note.trim().length < 5} onPress={() => mark(item, 'not_applicable', note)} />}<Action label="Cancelar observação" disabled={saving} onPress={back} /></View>
                <DiscardDraftAction disabled={locked} owner={`${month}:${item.id}:note`} label="Excluir rascunho da observação" onDiscard={resetForms} />
              </View>}
            </View>)}
            <Action label="Adicionar pendência" accessibilityLabel={`Adicionar pendência em ${group.title}`} disabled={locked} onPress={() => { setEditingItem(null); setBalanceEditing(null); setCustomGroup(group.id); setCustomTitle(''); setCustomId(newId());setDraftRevision(data.revision);setDraftConflict(false); }} />
            {customGroup === group.id && <View style={s.noteBox}><TextInput accessibilityLabel="Nova pendência do fechamento" editable={!locked} value={customTitle} onChangeText={setCustomTitle} maxLength={180} placeholder="O que falta reunir ou conferir?" placeholderTextColor="#92A4A2" style={s.input} /><View style={s.actions}><Action label="Adicionar pendência" primary disabled={locked || draftConflict || customTitle.trim().length < 3} onPress={() => mutate('/custom', 'POST', { request_id: customId, group_id: group.id, title: customTitle.trim() })} /><Action label="Cancelar pendência" disabled={saving} onPress={back} /></View></View>}
            {customGroup === group.id && <DiscardDraftAction disabled={locked} owner={`${month}:${customId}`} label="Excluir rascunho da pendência" onDiscard={resetForms} />}
            <View style={s.actions}>{group.kind === 'account' && <Action label="Abrir contas" disabled={saving} onPress={() => onNavigate('Contas')} />}{group.kind === 'card' && <Action label="Abrir cartões" disabled={saving} onPress={() => onNavigate('Cartões')} />}{group.kind === 'general' && <><Action label="Abrir extrato" disabled={saving} onPress={() => onNavigate('Movimentações')} /><Action label="Abrir revisão" disabled={saving} onPress={() => onNavigate('Revisão')} /><Action label="Abrir compromissos" disabled={saving} onPress={() => onNavigate('Compromissos')} /></>}</View>
          </View>}
        </View>;
      })}
      <View style={s.finish}><Text style={s.help}>Cada item precisa estar conferido ou ter uma justificativa de não aplicação. Diferenças de saldo e pendências detectadas precisam ser resolvidas.</Text>{data.status === 'closed' ? <Action label="Reabrir checklist do mês" primary disabled={loading || saving} onPress={() => mutate('/reopen', 'POST', {})} /> : <Action label="Concluir checklist do mês" primary disabled={loading || saving || !data.can_close} onPress={close} />}</View>
    </>}
  </View>;
}

const s = StyleSheet.create({
  heading: { color: '#F4F7F5', fontSize: 22, fontWeight: '800', marginTop: 10 }, title: { color: '#E7EFEC', fontSize: 16, fontWeight: '700', marginVertical: 5 },
  help: { color: '#92A4A2', fontSize: 13, lineHeight: 20, marginVertical: 6 }, label: { color: '#B9CAC6', fontSize: 13, marginVertical: 7, flexShrink: 1 },
  action: { minHeight: 44, paddingVertical: 12, paddingHorizontal: 4, justifyContent: 'center' }, link: { color: '#49C5AA', fontSize: 13, fontWeight: '700', lineHeight: 20 },
  primary: { backgroundColor: '#49C5AA', paddingHorizontal: 14, borderRadius: 10 }, primaryText: { color: '#061412', fontWeight: '800', fontSize: 14 }, disabled: { opacity: 0.5 },
  monthRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 14 }, monthInput: { flex: 1, minWidth: 100 },
  input: { borderColor: '#315054', borderWidth: 1, borderRadius: 10, backgroundColor: '#102023', color: '#F4F7F5', fontSize: 16, padding: 12, minHeight: 46 },
  summary: { backgroundColor: '#142326', borderRadius: 14, padding: 14, marginVertical: 14 }, progress: { color: '#49C5AA', fontWeight: '800', fontSize: 18, marginVertical: 6 },
  progressTrack: { height: 6, backgroundColor: '#263D40', borderRadius: 3, marginVertical: 7 }, progressFill: { height: 6, backgroundColor: '#49C5AA', borderRadius: 3 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, alignItems: 'center' }, group: { borderColor: '#263D40', borderWidth: 1, borderRadius: 14, marginBottom: 12, overflow: 'hidden' },
  groupHeader: { backgroundColor: '#142326', padding: 14, minHeight: 88 }, groupBody: { padding: 14 }, kind: { color: '#92A4A2', fontSize: 10, fontWeight: '700', letterSpacing: 1.4 },
  balanceBox: { padding: 12, borderRadius: 12, backgroundColor: '#142326', marginVertical: 10 }, item: { paddingVertical: 14, borderBottomWidth: 1, borderColor: '#263D40' },
  checkRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, minHeight: 44 }, checkbox: { width: 24, height: 24, borderWidth: 2, borderColor: '#49C5AA', borderRadius: 5, justifyContent: 'center', alignItems: 'center', marginTop: 1 },
  checked: { backgroundColor: '#49C5AA' }, checkmark: { color: '#061412', fontSize: 17, fontWeight: '800' }, itemTitle: { color: '#E7EFEC', fontSize: 15, lineHeight: 22, fontWeight: '600', flex: 1, minWidth: 0 },
  noteBox: { backgroundColor: '#142326', padding: 12, borderRadius: 10, marginVertical: 8 }, error: { color: '#FF8C83', fontSize: 13, lineHeight: 20 }, errorBox: { backgroundColor: '#261F20', padding: 12, borderRadius: 10, marginVertical: 10 },
  spinner: { margin: 20 }, finish: { marginVertical: 14 }, totalRow: { flexDirection: 'row', gap: 12, alignItems: 'center', justifyContent: 'space-between' }, totalValue: { flex: 1, minWidth: 0 }, money: { color: '#F4F7F5', fontWeight: '700', fontSize: 15, width: '100%', textAlign: 'right' },
});
