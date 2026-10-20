import React, { useCallback, useEffect, useRef, useState } from 'react';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { Alert } from '../utils/appAlert';
import SingleLineValue from './SingleLineValue';
import {rebaseDraft} from '../utils/draftConflict';
import {useAsyncScope} from '../utils/useAsyncScope';
import DiscardDraftAction from './DiscardDraftAction';
import {parseMoneyInput} from '../utils/moneyInput';
import {maskCivilDate, parseCivilDate} from '../utils/formMask';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Card = { id: string; instituicao: string; apelido: string; cartao_final: string; bill_count: number; deleted: boolean; token: string; validade?: string | null; dia_vencimento?: number | null; dia_fechamento?: number | null; melhor_dia_compra?: number | null; observacoes?: string | null };
type Bill = { id: string; competencia: string; data_vencimento: string; valor_total: string; valor_pago: string; saldo_aberto: string; status: string; observacoes: string; deleted: boolean; token: string };
type Props = { api: (path: string, init?: RequestInit) => Promise<any>; cards: Card[]; money: (value: number) => string; onUpdated: () => Promise<void> };
const title = (card: Card) => card.apelido || card.instituicao;
const dateLabel = (value: string) => value.split('-').reverse().join('/');
const monthLabel = (value: string) => value.split('-').reverse().join('/');
const statusLabel: Record<string, string> = { aberta: 'Aberta', fechada: 'Fechada', paga: 'Paga', parcialmente_paga: 'Paga em parte', vencida: 'Vencida' };
const cardFields = (card: Card) => ({instituicao: card.instituicao, apelido: card.apelido, cartao_final: card.cartao_final || '', validade: card.validade ? monthLabel(card.validade) : '', dia_vencimento: String(card.dia_vencimento ?? ''), dia_fechamento: String(card.dia_fechamento ?? ''), melhor_dia_compra: String(card.melhor_dia_compra ?? ''), observacoes: card.observacoes || ''});
const billFields = (bill: Bill) => ({competencia: bill.competencia, data_vencimento: bill.data_vencimento, valor_total: Number(bill.valor_total).toFixed(2).replace('.', ','), observacoes: bill.observacoes});
const fieldLabels: Record<string, string> = {instituicao: 'Instituição', apelido: 'Nome do cartão', cartao_final: 'Final do cartão', validade: 'Validade', dia_vencimento: 'Dia de vencimento', dia_fechamento: 'Dia de fechamento', melhor_dia_compra: 'Melhor dia para compras', competencia: 'Competência', data_vencimento: 'Vencimento', valor_total: 'Total da fatura', observacoes: 'Observações'};

function CardDetails({card}: {card: Card}) {
  return <View>
    <Text style={s.help}>Validade: {card.validade ? monthLabel(card.validade) : 'não informada'}</Text>
    <Text style={s.help}>Vencimento: {card.dia_vencimento ? `dia ${card.dia_vencimento}` : 'não informado'} · Fechamento: {card.dia_fechamento ? `dia ${card.dia_fechamento}` : 'não informado'}</Text>
    <Text style={s.help}>Melhor dia para compras: {card.melhor_dia_compra ? `dia ${card.melhor_dia_compra}` : 'não informado'}</Text>
    {!!card.observacoes && <Text style={s.help}>{card.observacoes}</Text>}
  </View>;
}

function Field({ label, value, onChange, disabled, numeric = false, limit = 100 }: { label: string; value: string; onChange: (value: string) => void; disabled: boolean; numeric?: boolean; limit?: number }) {
  return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} editable={!disabled} maxLength={limit} value={value} onChangeText={onChange} keyboardType={numeric ? 'decimal-pad' : 'default'} style={s.input} /></View>;
}

function Action({ label, onPress, disabled, danger = false, accessibilityLabel }: { label: string; onPress: () => void; disabled: boolean; danger?: boolean; accessibilityLabel?: string }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel || label} disabled={disabled} onPress={onPress} style={[s.action, disabled && s.disabled]}><Text style={danger ? s.danger : s.link}>{label}</Text></Pressable>;
}

export default function CardManager({ api, cards, money, onUpdated }: Props) {
  const [catalog, setCatalog] = useState<Card[]>(cards);
  const [selected, setSelected] = useState<Card | null>(null);
  const [bills, setBills] = useState<Bill[]>([]);
  const [showDeleted, setShowDeleted] = useState(false);
  const [loading, setLoading] = useState(false), [saving, setSaving] = useState(false), [error, setError] = useState('');
  const [editingCard, setEditingCard] = useState<Card | null>(null), [editingBill, setEditingBill] = useState<Bill | null>(null), [creatingCard, setCreatingCard] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [comparisonBase,setComparisonBase]=useState<Record<string,string>>({});
  const [conflict, setConflict] = useState(false), [differences, setDifferences] = useState<string[]>([]);
  const generation = useRef(0), running = useRef(false), loadController = useRef<AbortController | null>(null);
  const selectedId = selected?.id;
  const queryKey = `${selectedId || 'catalog'}:${showDeleted}`;
  const captureScope = useAsyncScope(queryKey);
  const [resultKey, setResultKey] = useState('catalog:false');
  const load = useCallback(async () => {
    loadController.current?.abort();
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    loadController.current = controller;
    const current = ++generation.current;
    setLoading(true); setError('');
    try {
      const init = controller ? { signal: controller.signal } : undefined;
      const response = selectedId
        ? await api(`/v1/card-catalog/${selectedId}/bills?include_deleted=${showDeleted}`, init)
        : await api(`/v1/card-catalog?include_deleted=${showDeleted}`, init);
      if (current !== generation.current) return;
      if (selectedId) { setSelected(old => ({ ...old, ...response.card })); setBills(response.items || []); }
      else setCatalog(response.items || []);
      setResultKey(`${selectedId || 'catalog'}:${showDeleted}`);
    } catch (failure: any) { if (current === generation.current) setError(failure.message || 'Não foi possível carregar os cartões.'); }
    finally { if (loadController.current === controller) loadController.current = null; if (current === generation.current) setLoading(false); }
  }, [api, selectedId, showDeleted]);
  useEffect(() => { const counter = generation; const timer = setTimeout(() => load(), 0); return () => { clearTimeout(timer); counter.current++; loadController.current?.abort(); loadController.current = null; }; }, [load, cards]);
  const update = (key: string, value: string) => setForm(current => ({ ...current, [key]: value }));
  const blankCardFields = () => ({instituicao: '', apelido: '', cartao_final: '', validade: '', dia_vencimento: '', dia_fechamento: '', melhor_dia_compra: '', observacoes: ''});
  const closeForm = () => { setCreatingCard(false); setEditingCard(null); setEditingBill(null); setForm({}); setComparisonBase({}); setConflict(false); setDifferences([]); };
  const formDirty=Boolean(creatingCard||editingCard||editingBill)&&JSON.stringify(form)!==JSON.stringify(creatingCard?blankCardFields():editingCard?cardFields(editingCard):billFields(editingBill!));
  const back=()=>protectUnsavedBack(formDirty,()=>{if(creatingCard||editingCard||editingBill)closeForm();else {setSelected(null);setBills([]);}});
  useLocalBack(back,Boolean(creatingCard||editingCard||editingBill||selected),20);
  const mutate = async (path: string, method: string, body: Record<string, unknown>, confirmationValid = captureScope()) => {
    if (running.current || !confirmationValid()) return;
    running.current = true; setSaving(true); setError('');
    const valid = captureScope();
    let saved = false;
    try {
      if (!valid() || !confirmationValid()) return;
      await api(path, { method, body: JSON.stringify(body) }); saved = true;
      if (!valid()) return;
      closeForm();
      Alert.alert('Alteração salva', 'O cartão ou a fatura foi atualizado.');
      void Promise.resolve().then(onUpdated).catch(failure => {if (valid()) setError(`Alteração salva. Não foi possível atualizar o resumo: ${failure.message}`);});
      await load();
    } catch (failure: any) {
      if (!valid()) return;
      if (!saved && [409, 428].includes(failure.status) && (editingCard || editingBill)) {setConflict(true);setDifferences([]);}
      setError(saved ? `Alteração salva. Não foi possível atualizar a lista: ${failure.message}` : failure.message);
    } finally { running.current = false; if (valid()) setSaving(false); }
  };
  const loadCurrent = async () => {
    if (running.current || (!editingCard && !editingBill)) return;
    const valid = captureScope(); running.current = true;setSaving(true);setError('');
    try {
      const response = editingCard ? await api('/v1/card-catalog?include_deleted=true') : await api(`/v1/card-catalog/${selectedId}/bills?include_deleted=true`);
      if (!valid()) return;
      const old = (editingCard || editingBill)!;
      const fresh = response.items.find((item: Card | Bill) => item.id === old.id);
      if (!fresh || fresh.deleted) throw new Error('Este cadastro saiu da lista. Seu rascunho foi preservado; confira os excluídos antes de continuar.');
      const values = editingCard ? cardFields : billFields;
      const rebased = rebaseDraft(comparisonBase, values(fresh), form);
      setForm(rebased.draft);setDifferences(rebased.conflicts);setConflict(rebased.conflicts.length > 0);
      setComparisonBase({...values(fresh),...Object.fromEntries(rebased.conflicts.map(field=>[field,comparisonBase[field]]))});
      if (editingCard) setEditingCard(fresh);else setEditingBill(fresh);
    } catch (failure: any) {if (valid()) setError(failure.message);}
    finally {running.current = false;if (valid()) setSaving(false);}
  };
  const startCreateCard = () => {
    setConflict(false);setDifferences([]);setError('');
    setEditingBill(null); setEditingCard(null); setCreatingCard(true);
    const blank = blankCardFields();
    setComparisonBase(blank);
    setForm(blank);
  };
  const editCard = (card: Card) => {
    setConflict(false);setDifferences([]);setCreatingCard(false);
    setEditingBill(null); setEditingCard(card);
    setComparisonBase(cardFields(card));
    setForm(cardFields(card));
  };
  const editBill = (bill: Bill) => {
    setConflict(false);setDifferences([]);
    setEditingCard(null); setEditingBill(bill);
    setComparisonBase(billFields(bill));
    setForm({ competencia: bill.competencia, data_vencimento: bill.data_vencimento,
      valor_total: Number(bill.valor_total).toFixed(2).replace('.', ','), observacoes: bill.observacoes });
  };
  const save = () => {
    if (conflict) return;
    if (!creatingCard && !formDirty) { closeForm(); return; }
    if (creatingCard || editingCard) {
      if (!form.instituicao.trim() || (form.cartao_final && !/^\d{4}$/.test(form.cartao_final))) {
        setError('Informe a instituição e, se preencher o final do cartão, use quatro dígitos.'); return;
      }
      const validity = form.validade.trim();
      const dayFields = ['dia_vencimento', 'dia_fechamento', 'melhor_dia_compra'];
      if ((validity && !/^(0[1-9]|1[0-2])\/[1-9]\d{3}$/.test(validity)) || dayFields.some(key => form[key].trim() && !/^(?:[1-9]|[12]\d|3[01])$/.test(form[key].trim()))) {
        setError('Use validade MM/AAAA e dias de 1 a 31. Deixe em branco o que não souber.'); return;
      }
      const payload = { ...form, validade: validity ? validity.split('/').reverse().join('-') : null,
        ...Object.fromEntries(dayFields.map(key => [key, form[key].trim() ? Number(form[key]) : null])), observacoes: form.observacoes.trim() || null };
      if (creatingCard) return mutate('/v1/card-catalog', 'POST', payload);
      return mutate(`/v1/card-catalog/${editingCard!.id}`, 'PATCH', { ...payload, expected_token: editingCard!.token });
    } else if (editingBill) {
      const total = parseMoneyInput(form.valor_total);
      const rawComp = form.competencia.trim();
      const competencia = /^\d{4}-(0[1-9]|1[0-2])$/.test(rawComp) ? rawComp : (/^(0[1-9]|1[0-2])\/\d{4}$/.test(rawComp) ? rawComp.split('/').reverse().join('-') : '');
      const rawDue = form.data_vencimento.trim();
      const data_vencimento = /^\d{4}-\d{2}-\d{2}$/.test(rawDue) ? rawDue : parseCivilDate(rawDue);
      if (total === null || !competencia || !data_vencimento) {
        setError('Use competência AAAA-MM (ou MM/AAAA), vencimento dd/mm/aaaa (ou AAAA-MM-DD) e valor com até duas casas decimais.'); return;
      }
      const valid = captureScope();
      Alert.alert('Salvar alterações da fatura?', 'O valor em aberto e o vencimento do compromisso serão atualizados. As compras e os pagamentos registrados serão preservados.', [
        { text: 'Cancelar', style: 'cancel' }, { text: 'Salvar', onPress: () => mutate(`/v1/card-bills/${editingBill.id}`, 'PATCH', { ...form, competencia, data_vencimento, valor_total: total, expected_token: editingBill.token }, valid) },
      ]);
    }
  };
  const remove = (item: Card | Bill, kind: 'card' | 'bill') => {
    const name = kind === 'card' ? title(item as Card) : `fatura ${monthLabel((item as Bill).competencia)}`;
    const path = kind === 'card' ? `/v1/card-catalog/${item.id}` : `/v1/card-bills/${item.id}`;
    const valid = captureScope();
    Alert.alert(`Excluir ${kind === 'card' ? 'cartão' : 'fatura'}?`, `${name} sairá da lista. Compras, pagamentos e dívidas em aberto continuarão no histórico. Você pode restaurar em “Mostrar excluídos”.`, [
      { text: 'Cancelar', style: 'cancel' }, { text: 'Excluir', style: 'destructive', onPress: () => mutate(path, 'DELETE', { expected_token: item.token }, valid) },
    ]);
  };
  const restore = (item: Card | Bill, kind: 'card' | 'bill') => mutate(`${kind === 'card' ? '/v1/card-catalog' : '/v1/card-bills'}/${item.id}/restore`, 'POST', { expected_token: item.token });
  const locked = loading || saving;
  const visibleCatalog = resultKey === queryKey ? catalog : [];
  const visibleBills = resultKey === queryKey ? bills : [];

  return <View>
    {selected ? <View>
      <Action label="‹ Trocar cartão" disabled={locked} onPress={()=>protectUnsavedBack(formDirty,()=>{closeForm();setSelected(null);setBills([]);})} />
      <Text style={s.heading}>{title(selected)}{selected.cartao_final ? ` · final ${selected.cartao_final}` : ''}</Text>
      <Text style={s.help}>{selected.instituicao}{selected.deleted ? ' · Excluído da lista' : ''}</Text>
      <CardDetails card={selected} />
      <View style={s.actions}>{selected.deleted ? <Action label="Restaurar cartão" disabled={locked} onPress={() => restore(selected, 'card')} /> : <><Action label="Editar cartão" disabled={locked} onPress={() => editCard(selected)} /><Action label="Excluir cartão" disabled={locked} danger onPress={() => remove(selected, 'card')} /></>}</View>
    </View> : <><Text style={s.heading}>Escolha um cartão</Text><Text style={s.help}>Selecione o cartão para ver e gerenciar suas faturas.</Text></>}
    <View style={s.actions}>
      {!selected && <Action label="+ Adicionar cartão" disabled={locked} onPress={startCreateCard} />}
      <Action label={showDeleted ? 'Ocultar excluídos' : 'Mostrar excluídos'} disabled={locked} onPress={() => { closeForm(); setShowDeleted(!showDeleted); }} />
    </View>
    {(creatingCard || editingCard || editingBill) && <View style={s.form}>
      <Text style={s.formTitle}>{creatingCard ? 'Novo cartão' : editingCard ? 'Editar cartão' : `Editar fatura ${monthLabel(editingBill!.competencia)}`}</Text>
      {conflict && <View><Text style={s.help}>O cadastro mudou. Seu texto foi preservado. Carregue a versão atual e confira antes de salvar.</Text>
        {differences.map(field => <Text key={field} style={s.help}>{fieldLabels[field] || field}: origem “{comparisonBase[field] || ''}”; atual “{((editingCard ? cardFields(editingCard) : billFields(editingBill!)) as Record<string, string>)[field] || ''}”; rascunho “{form[field]}”.</Text>)}
        <Action label="Carregar cadastro atual e conferir" disabled={locked} onPress={loadCurrent}/>
        {!!differences.length && <Action label="Conferi as diferenças e quero reaplicar meus campos" disabled={locked} onPress={() => {setComparisonBase(editingCard?cardFields(editingCard):billFields(editingBill!));setConflict(false);setDifferences([]);}}/>}
      </View>}
      {(creatingCard || editingCard) ? <>
        <Field label="Instituição" value={form.instituicao} onChange={v => update('instituicao', v)} disabled={locked} />
        <Field label="Nome do cartão" value={form.apelido} onChange={v => update('apelido', v)} disabled={locked} limit={80} />
        <Field label="Final do cartão (4 dígitos, opcional)" value={form.cartao_final} onChange={v => update('cartao_final', v)} disabled={locked} numeric limit={4} />
        <Field label="Validade do cartão (MM/AAAA)" value={form.validade} onChange={v => update('validade', v)} disabled={locked} limit={7} />
        <Field label="Dia de vencimento da fatura (1 a 31)" value={form.dia_vencimento} onChange={v => update('dia_vencimento', v)} disabled={locked} numeric limit={2} />
        <Field label="Dia de fechamento da fatura (1 a 31)" value={form.dia_fechamento} onChange={v => update('dia_fechamento', v)} disabled={locked} numeric limit={2} />
        <Field label="Melhor dia para compras (1 a 31)" value={form.melhor_dia_compra} onChange={v => update('melhor_dia_compra', v)} disabled={locked} numeric limit={2} />
        <Field label="Observações do cartão" value={form.observacoes} onChange={v => update('observacoes', v)} disabled={locked} limit={1000} />
        <Text style={s.help}>Todos estes dados são opcionais. Confirme os dias no banco, pois podem variar. O melhor dia é informado por você; a validade é diferente do vencimento da fatura. Faturas e compromissos já cadastrados mantêm suas datas.</Text>
      </> : <>
        <Field label="Competência (AAAA-MM)" value={form.competencia} onChange={v => update('competencia', v)} disabled={locked} limit={7} />
        <Field label="Vencimento (AAAA-MM-DD)" value={form.data_vencimento} onChange={v => update('data_vencimento', /^\d{4}-/.test(v) ? v : maskCivilDate(v))} disabled={locked} limit={10} />
        <Field label="Total da fatura (R$)" value={form.valor_total} onChange={v => update('valor_total', v)} disabled={locked} numeric limit={16} />
        <Field label="Observações" value={form.observacoes} onChange={v => update('observacoes', v)} disabled={locked} limit={1000} />
        <Text style={s.help}>Pago: {money(Number(editingBill!.valor_pago))}. O status acompanha os pagamentos registrados; alterar o total corrige o saldo devido.</Text>
      </>}
      <View style={s.actions}><Action label="Cancelar edição" disabled={locked} onPress={back} /><Pressable accessibilityRole="button" disabled={locked || conflict} onPress={save} style={[s.save, (locked || conflict) && s.disabled]}><Text style={s.saveText}>Salvar alterações</Text></Pressable></View>
      <DiscardDraftAction disabled={locked} owner={creatingCard ? 'new-card' : editingCard?.id || editingBill?.id} label={`Excluir rascunho ${(creatingCard || editingCard) ? 'do cartão' : 'da fatura'}`} onDiscard={()=>{closeForm();setError('');}} />
    </View>}
    {!!error && <View style={s.errorBox}><Text style={s.danger}>{error}</Text><Action label="Atualizar lista" disabled={locked} onPress={() => load()} /></View>}
    {loading && <ActivityIndicator color="#49C5AA" style={s.spinner} />}
    {!loading && resultKey === queryKey && !selected && !visibleCatalog.length && !error && <Text style={s.help}>Nenhum cartão disponível{showDeleted ? '.' : '. Consulte “Mostrar excluídos”.'}</Text>}
    {!selected && visibleCatalog.map(card => <View key={card.id} style={s.card}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Ver faturas de ${title(card)}`} disabled={locked} onPress={() => { closeForm(); setSelected(card); setBills([]); }} style={s.cardButton}>
        <Text style={s.name}>{title(card)}{card.cartao_final ? ` · ${card.cartao_final}` : ''}</Text>
        <Text style={s.help}>{card.instituicao} · {card.bill_count} fatura(s){card.deleted ? ' · Excluído' : ''}</Text><Text style={s.link}>Ver faturas ›</Text>
        <CardDetails card={card} />
      </Pressable>
      <View style={s.actions}>{card.deleted ? <Action label="Restaurar" disabled={locked} accessibilityLabel={`Restaurar cartão ${title(card)}`} onPress={() => restore(card, 'card')} /> : <><Action label="Editar" disabled={locked} accessibilityLabel={`Editar cartão ${title(card)}`} onPress={() => editCard(card)} /><Action label="Excluir" disabled={locked} danger accessibilityLabel={`Excluir cartão ${title(card)}`} onPress={() => remove(card, 'card')} /></>}</View>
    </View>)}
    {selected && <><Text style={s.heading}>Faturas</Text>
      {!loading && resultKey === queryKey && !visibleBills.length && !error && <Text style={s.help}>Nenhuma fatura{showDeleted ? '' : ' disponível'} neste cartão.</Text>}
      {visibleBills.map(bill => <View key={bill.id} style={s.bill}>
        <View style={s.billTop}><Text style={s.name}>{monthLabel(bill.competencia)}</Text><View style={s.amountBox}><SingleLineValue style={s.amount}>{money(Number(bill.valor_total))}</SingleLineValue></View></View>
        <Text style={s.help}>{statusLabel[bill.status] || bill.status} · vence {dateLabel(bill.data_vencimento)}{bill.deleted ? ' · Excluída' : ''}</Text>
        <Text style={s.help}>Pago: {money(Number(bill.valor_pago))} · Em aberto: {money(Number(bill.saldo_aberto))}</Text>
        {!!bill.observacoes && <Text style={s.help}>{bill.observacoes}</Text>}
        <View style={s.actions}>{bill.deleted ? <Action label="Restaurar fatura" disabled={locked} accessibilityLabel={`Restaurar fatura ${bill.competencia}`} onPress={() => restore(bill, 'bill')} /> : !selected.deleted && <><Action label="Editar" disabled={locked} accessibilityLabel={`Editar fatura ${bill.competencia}`} onPress={() => editBill(bill)} /><Action label="Excluir" disabled={locked} danger accessibilityLabel={`Excluir fatura ${bill.competencia}`} onPress={() => remove(bill, 'bill')} /></>}</View>
      </View>)}
    </>}
  </View>;
}

const s = StyleSheet.create({
  heading: { fontSize: 20, fontWeight: '700', color: '#F4F7F5', marginTop: 12 },
  help: { color: '#92A4A2', fontSize: 13, lineHeight: 20, marginTop: 5, marginBottom: 6 },
  name: { fontSize: 16, fontWeight: '700', color: '#E7EFEC', flexShrink: 1 },
  card: { borderWidth: 1, borderColor: '#263D40', backgroundColor: '#142326', borderRadius: 14, marginTop: 12, padding: 14 },
  cardButton: { paddingVertical: 6, minHeight: 76 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, alignItems: 'center' },
  action: { minHeight: 44, paddingVertical: 12, paddingHorizontal: 4, justifyContent: 'center' },
  link: { color: '#49C5AA', fontWeight: '700', fontSize: 14 }, danger: { color: '#FF8C83', fontSize: 14, lineHeight: 20 },
  form: { backgroundColor: '#183034', borderRadius: 14, padding: 14, marginVertical: 12 },
  formTitle: { color: '#F4F7F5', fontSize: 18, fontWeight: '700' },
  field: { marginVertical: 8 }, label: { color: '#B9CAC6', fontSize: 13, marginBottom: 6 },
  input: { backgroundColor: '#102023', borderWidth: 1, borderColor: '#315054', borderRadius: 10, color: '#F4F7F5', padding: 12, fontSize: 16, minHeight: 46 },
  save: { backgroundColor: '#49C5AA', borderRadius: 10, minHeight: 44, padding: 12 },
  saveText: { color: '#061412', fontWeight: '800' }, disabled: { opacity: 0.5 },
  errorBox: { backgroundColor: '#261F20', padding: 12, borderRadius: 10, marginVertical: 8 }, spinner: { margin: 20 },
  bill: { borderBottomWidth: 1, borderColor: '#263D40', paddingVertical: 16 },
  billTop: { flexDirection: 'row', justifyContent: 'space-between', gap: 12, alignItems: 'center' },
  amountBox: { flex: 1, minWidth: 0 },
  amount: { color: '#F4F7F5', fontSize: 17, fontWeight: '700', width: '100%', textAlign: 'right' },
});
