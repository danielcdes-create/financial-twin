import {Alert} from '../utils/appAlert';
import {creationId} from '../utils/commitmentCreate';
import {financialDateLabel} from '../utils/classification';
import StatementSearch, {paymentDifference} from './StatementSearch';
import React, {useState} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';

type Api = (path: string, init?: RequestInit) => Promise<any>;

const amountOf = (item: any) => typeof item?.valor === 'number' ? item.valor : Number(item?.valor?.$numberDecimal || item?.valor || 0);
const titleOf = (item: any) => item?.vendedor || item?.fornecedor || item?.merchant_name || item?.merchant_raw || item?.estabelecimento_original || item?.descricao_original || 'Movimentação';

export default function StatementCommitmentLink({item, api, money, onLinked, onUnlinked}: {
  item: any; api: Api; money: (value: number) => string; onLinked: (commitment: {id: string; name: string; kind?: string}) => void; onUnlinked?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [commitments, setCommitments] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [swapCommitment, setSwapCommitment] = useState<any>(null);
  const loadCommitment = async () => {
    const result = await api('/v1/commitments');
    const found = (result.items || []).find((row: any) => row.commitment_id === item.commitment?.id);
    if (!found) throw new Error('O compromisso não está mais na lista. Atualize o extrato.');
    return found;
  };
  const sourceOf = () => item.movement_source === 'capture' || (!item.id && item.provisional_id) ? {provisional_id: item.provisional_id} : {transaction_id: item.id};
  const unlink = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const commitment = await loadCommitment();
      const result = await api(`/v1/commitments/${commitment.commitment_id}/payment-unlink`, {method: 'POST', body: JSON.stringify({request_id: creationId(), expected_revision: commitment.revision || 1, ...sourceOf()})});
      onUnlinked?.();
      Alert.alert(result?.warning ? 'Agenda mantida' : 'Vínculo removido', result?.warning || 'A agenda voltou ao estado anterior a este vínculo. O lançamento continua no extrato.');
    } catch (error: any) {
      Alert.alert('Não foi possível desvincular', error.message);
    } finally {setSaving(false);}
  };
  const swap = async (transaction: any) => {
    if (saving || !swapCommitment || !transaction.source_token) return;
    setSaving(true);
    try {
      await api(`/v1/commitments/${swapCommitment.commitment_id}/payment-swap`, {method: 'POST', body: JSON.stringify({
        request_id: creationId(), expected_revision: swapCommitment.revision || 1, expected_source_token: transaction.source_token, ...sourceOf(),
        ...(transaction.source_kind === 'confirmed_capture' ? {replacement_provisional_id: transaction.provisional_id} : {replacement_transaction_id: transaction.id}),
      })});
      setSwapCommitment(null);
      onUnlinked?.();
      Alert.alert('Lançamento trocado', 'A agenda não mudou. O vínculo ficou no lançamento escolhido.');
    } catch (error: any) {
      Alert.alert('Não foi possível trocar', error.message);
    } finally {setSaving(false);}
  };
  if (!item?.commitment && amountOf(item) >= 0) return null;
  if (item?.commitment) return <View>
    <Text style={s.help}>Vinculado a {item.commitment.name}</Text>
    {swapCommitment && <StatementSearch api={api} commitment={swapCommitment} suggestionIds={[String(item.id || item.provisional_id || '')]} money={money} disabled={saving} onSelect={swap}/>}
    {!swapCommitment && <Pressable accessibilityRole="button" accessibilityLabel="Trocar lançamento" disabled={saving} onPress={() => {void loadCommitment().then(setSwapCommitment).catch((error: any) => Alert.alert('Não foi possível carregar o compromisso', error.message));}} style={s.action}><Text style={s.actionText}>Trocar lançamento</Text></Pressable>}
    {!swapCommitment && <Pressable accessibilityRole="button" accessibilityLabel="Desvincular pagamento" disabled={saving} onPress={() => Alert.alert('Desvincular pagamento', 'O lançamento continua no extrato. A agenda só volta se este for o vínculo mais recente que a moveu.', [{text: 'Cancelar', style: 'cancel'}, {text: 'Desvincular', onPress: () => {void unlink();}}])} style={s.row}><Text style={s.name}>Desvincular</Text></Pressable>}
  </View>;
  const load = async () => {
    setOpen(true);
    setLoading(true);
    try {
      const result = await api('/v1/commitments');
      setCommitments((result.items || []).filter((row: any) => row.status === 'ativo' || row.status === 'pausado'));
    } catch (error: any) {
      Alert.alert('Não foi possível carregar compromissos', error.message);
    } finally {
      setLoading(false);
    }
  };
  const link = (commitment: any) => {
    if (saving || !item.source_token) {
      if (!item.source_token) Alert.alert('Recarregue a movimentação', 'Abra o lançamento de novo antes de vincular.');
      return;
    }
    const captured = item.movement_source === 'capture' || (!item.id && item.provisional_id);
    const values = {
      ...(captured ? {provisional_id: item.provisional_id} : {transaction_id: item.id}),
      expected_revision: commitment.revision || 1,
      expected_source_token: item.source_token,
      request_id: creationId(),
    };
    const difference = paymentDifference(Number(commitment.amount), amountOf(item), money);
    Alert.alert('Vincular a compromisso', `${commitment.name} · ${money(Number(commitment.amount || 0))} por cobrança\n${titleOf(item)} · ${money(Math.abs(amountOf(item)))} em ${financialDateLabel(String(item.data_evento || ''))}\n${difference}`, [
      {text: 'Cancelar', style: 'cancel'},
      {text: 'Vincular', onPress: () => {void (async () => {
        setSaving(true);
        try {
          await api(`/v1/commitments/${commitment.commitment_id}/payment`, {method: 'POST', body: JSON.stringify(values)});
          onLinked({id: commitment.commitment_id, name: commitment.name, kind: commitment.kind});
          setOpen(false);
          Alert.alert('Pagamento vinculado', 'O vínculo foi registrado. O lançamento continua no extrato.');
        } catch (error: any) {
          Alert.alert('Não foi possível vincular', error.message);
        } finally {
          setSaving(false);
        }
      })();}},
    ]);
  };
  const needle = query.trim().toLocaleLowerCase('pt-BR');
  const visible = commitments.filter(row => !needle || String(row.name || '').toLocaleLowerCase('pt-BR').includes(needle));
  return <View>
    {!open && <Pressable accessibilityRole="button" accessibilityLabel="Vincular a compromisso" disabled={saving} onPress={() => {void load();}} style={s.action}><Text style={s.actionText}>Vincular a compromisso</Text></Pressable>}
    {open && <View>
      <Text style={s.help}>{loading ? 'Carregando compromissos...' : 'Escolha o compromisso. A confirmação mostra a diferença de valor. O lançamento não sai do extrato.'}</Text>
      <TextInput accessibilityLabel="Nome do compromisso" value={query} onChangeText={setQuery} placeholder="Nome do compromisso" placeholderTextColor="#92A4A2" style={s.input}/>
      {!loading && !visible.length && <Text style={s.help}>Nenhum compromisso ativo com esse nome.</Text>}
      {visible.map(row => <Pressable accessibilityRole="button" accessibilityLabel={`Vincular a ${row.name}`} disabled={saving} key={row.commitment_id} onPress={() => link(row)} style={s.row}><Text style={s.name}>{row.name}</Text><Text style={s.meta}>{money(Number(row.amount || 0))} · {financialDateLabel(String(row.next_due_date || ''))}</Text></Pressable>)}
      <Pressable accessibilityRole="button" accessibilityLabel="Fechar vínculo do extrato" onPress={() => setOpen(false)}><Text style={s.cancel}>Fechar</Text></Pressable>
    </View>}
  </View>;
}

const s = StyleSheet.create({
  action: {backgroundColor: '#49C5AA', borderRadius: 10, minHeight: 48, alignItems: 'center', justifyContent: 'center', marginVertical: 8},
  actionText: {color: '#061412', fontSize: 14, fontWeight: '800'},
  help: {color: '#92A4A2', fontSize: 12, lineHeight: 18, marginBottom: 8},
  input: {backgroundColor: '#18292C', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11, color: '#F5F8F6', fontSize: 15, marginBottom: 8},
  row: {paddingVertical: 10, borderTopWidth: 1, borderColor: '#315054'},
  name: {color: '#F4F7F5', fontSize: 15, fontWeight: '700'},
  meta: {color: '#92A4A2', fontSize: 11, marginTop: 4},
  cancel: {color: '#C5D8D4', textAlign: 'center', padding: 10},
});
