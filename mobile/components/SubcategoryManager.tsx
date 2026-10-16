import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import DiscardDraftAction from './DiscardDraftAction';
import {ActivityIndicator, StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import { ActivityAlert as Alert } from '../utils/appAlert';
import { isAbort } from '../utils/apiClient';
import { rankedOptions } from '../utils/classification';
import {protectUnsavedBack,useLocalBack} from '../utils/localBack';

type Subcategory = { name: string; usage_count: number; token: string };
type Props = {
  category: string;
  api: (path: string, init?: RequestInit) => Promise<any>;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
  onUpdated: () => Promise<void>;
};
const path = '/v1/categories/subcategories';

export default function SubcategoryManager({ category, api, disabled, onBusyChange, onUpdated }: Props) {
  const [items, setItems] = useState<Subcategory[]>([]);
  const [newName, setNewName] = useState('');
  const [editing, setEditing] = useState<Subcategory | null>(null);
  const [editName, setEditName] = useState('');
  const [deleting, setDeleting] = useState<Subcategory | null>(null);
  const [replacement, setReplacement] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const busy = useRef(false);
  const loadGeneration = useRef(0);
  const lifecycle = useRef(0);
  useLocalBack(()=>protectUnsavedBack(Boolean(newName||replacement||editing&&editName!==editing.name),()=>{setEditing(null);setDeleting(null);}),Boolean(newName||editing||deleting),30);
  useLayoutEffect(()=>{const owner=lifecycle;owner.current++;return()=>{owner.current++;}},[category]);
  const guarded=(callback:()=>Promise<void>)=>{const owner=lifecycle.current;return()=>owner===lifecycle.current?callback():undefined};
  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError('');
    try {
      const result = await api(`${path}?${new URLSearchParams({ category })}`);
      if (generation !== loadGeneration.current) return;
      const details: Subcategory[] = result.details || [];
      setItems(details);
      // Refresh optimistic tokens after a conflict without losing typed names.
      setEditing(current => current ? details.find(item => item.name === current.name) || null : null);
      setDeleting(current => current ? details.find(item => item.name === current.name) || null : null);
    } catch (failure: any) {
      if (generation === loadGeneration.current) setError(isAbort(failure) ? 'Atualização interrompida. Tente atualizar a lista.' : failure.message || 'Não foi possível carregar as subcategorias.');
    } finally { if (generation === loadGeneration.current) setLoading(false); }
  }, [api, category]);
  useEffect(() => {
    const loads = loadGeneration;
    const task = setTimeout(() => load(), 0);
    return () => { clearTimeout(task); loads.current++; };
  }, [load]);

  const mutate = async (init: RequestInit, query = '') => {
    if (busy.current || disabled) return;
    busy.current = true;
    onBusyChange(true);
    const owner = lifecycle.current;
    let acknowledged = false;
    try {
      await api(path + query, init);
      if (owner !== lifecycle.current) return;
      acknowledged = true;
      setNewName('');
      setEditing(null);
      setDeleting(null);
      setReplacement('');
      Alert.alert('Subcategoria salva', 'A alteração foi registrada.');
      // Local tokens are required for the next edit. Aggregate refreshes can
      // finish separately and must not hold the acknowledged mutation gate.
      void onUpdated().catch(() => {if (owner === lifecycle.current) setError('A alteração foi salva. Não foi possível atualizar os totais. Tente atualizar a lista.');});
      await load();
    } catch (failure: any) {
      if (owner !== lifecycle.current) return;
      Alert.alert(acknowledged ? 'Subcategoria salva' : isAbort(failure) ? 'Alteração interrompida' : 'Não foi possível salvar a subcategoria', acknowledged ? `A gravação foi confirmada. Não foi possível atualizar a lista: ${failure.message}` : isAbort(failure) ? 'A sessão foi suspensa. Confira a lista antes de repetir a alteração.' : failure.message);
      if (isAbort(failure)) await load();
      if (!acknowledged && (failure.status === 409 || failure.status === 428)) await load();
    } finally {
      busy.current = false;
      if (owner === lifecycle.current) onBusyChange(false);
    }
  };
  const create = () => {
    const name = newName.trim();
    if (!name) { Alert.alert('Informe o nome', 'Digite o nome da nova subcategoria.'); return; }
    return mutate({ method: 'POST', body: JSON.stringify({ category, name }) });
  };
  const rename = () => {
    if (!editing) return;
    const new_name = editName.trim();
    if (!new_name) { Alert.alert('Informe o nome', 'Digite o novo nome da subcategoria.'); return; }
    if (new_name === editing.name) { setEditing(null); return; }
    Alert.alert('Renomear subcategoria?',
      `“${editing.name}” passará a se chamar “${new_name}” em ${category}. ${editing.usage_count} registro(s) serão atualizados.`,
      [{ text: 'Cancelar', style: 'cancel' }, { text: 'Renomear', onPress: guarded(() => mutate({ method: 'PATCH', body: JSON.stringify({ category, name: editing.name, new_name, expected_token: editing.token }) })) }]);
  };
  const remove = (item: Subcategory, substitute?: string) => {
    const query = new URLSearchParams({ category, name: item.name, expected_token: item.token });
    if (substitute) query.set('replacement', substitute);
    return mutate({ method: 'DELETE' }, `?${query}`);
  };
  const askDelete = (item: Subcategory) => {
    setEditing(null);
    if (item.usage_count) { setDeleting(item); setReplacement(''); return; }
    Alert.alert('Excluir subcategoria?', `“${item.name}” em ${category} não possui registros vinculados.`,
      [{ text: 'Cancelar', style: 'cancel' }, { text: 'Excluir', style: 'destructive', onPress: guarded(() => remove(item)) }]);
  };
  const confirmReplacement = () => {
    if (!deleting) return;
    const substitute = items.find(item => item.name.toLocaleLowerCase('pt-BR') === replacement.trim().toLocaleLowerCase('pt-BR'));
    if (!substitute || substitute.name === deleting.name) {
      Alert.alert('Escolha uma substituta', 'Selecione outra subcategoria desta categoria nas sugestões.'); return;
    }
    Alert.alert('Substituir e excluir?', `${deleting.usage_count} registro(s) passarão de “${deleting.name}” para “${substitute.name}” em ${category}.`,
      [{ text: 'Cancelar', style: 'cancel' }, { text: 'Confirmar', style: 'destructive', onPress: guarded(() => remove(deleting, substitute.name)) }]);
  };
  const suggestions = rankedOptions(replacement, items.filter(item => item.name !== deleting?.name).map(item => item.name), 8);
  const locked = disabled || loading || !!error;

  return <View style={s.panel}>
    <Text style={s.title}>Subcategorias de {category}</Text>
    <Text style={s.help}>Crie, renomeie ou exclua. As alterações se aplicam somente a esta categoria.</Text>
    <View style={s.createRow}>
      <TextInput accessibilityLabel={`Nova subcategoria de ${category}`} editable={!locked} maxLength={120} value={newName} onChangeText={setNewName} onSubmitEditing={create} placeholder="Nova subcategoria" placeholderTextColor="#92A4A2" style={s.input} />
      <Pressable accessibilityRole="button" disabled={locked} onPress={create} style={[s.add, locked && s.disabled]}><Text style={s.addText}>Criar</Text></Pressable>
    </View>
    {!!newName && <DiscardDraftAction disabled={disabled} owner={category} label="Excluir rascunho da subcategoria" onDiscard={()=>setNewName('')} />}
    {loading && <ActivityIndicator color="#4FD1B5" style={s.spinner} />}
    {!!error && <View><Text style={s.error}>{error}</Text><Pressable accessibilityRole="button" onPress={() => load()} style={s.action}><Text style={s.editText}>Tentar novamente</Text></Pressable></View>}
    {!loading && !error && !items.length && <Text style={s.help}>Nenhuma subcategoria cadastrada.</Text>}
    {!error && items.map(item => <View key={item.name} style={s.row}>
      <View style={s.rowText}><Text style={s.name}>{item.name}</Text><Text style={s.meta}>{item.usage_count} registro{item.usage_count === 1 ? '' : 's'}</Text></View>
      <View style={s.rowActions}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Editar subcategoria ${item.name}`} disabled={locked} onPress={() => { setDeleting(null); setEditing(item); setEditName(item.name); }} style={s.action}><Text style={s.editText}>Editar</Text></Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel={`Excluir subcategoria ${item.name}`} disabled={locked} onPress={() => askDelete(item)} style={s.action}><Text style={s.deleteText}>Excluir</Text></Pressable>
      </View>
    </View>)}
    {editing && <View style={s.card}>
      <Text style={s.name}>Renomear “{editing.name}”</Text>
      <TextInput accessibilityLabel="Novo nome da subcategoria" editable={!locked} maxLength={120} value={editName} onChangeText={setEditName} onSubmitEditing={rename} style={s.input} />
      <View style={s.actions}><Pressable accessibilityRole="button" disabled={disabled} onPress={() => {setEditing(null); setEditName('');}} style={s.cancel}><Text style={s.cancelText}>Cancelar</Text></Pressable><Pressable accessibilityRole="button" disabled={locked} onPress={rename} style={[s.add, locked && s.disabled]}><Text style={s.addText}>Salvar</Text></Pressable></View>
      <DiscardDraftAction disabled={disabled} owner={`${category}:${editing.name}`} label="Excluir rascunho da renomeação" onDiscard={()=>{setEditing(null);setEditName('');}} />
    </View>}
    {deleting && <View style={s.card}>
      <Text style={s.name}>Excluir “{deleting.name}”</Text>
      <Text style={s.help}>{deleting.usage_count} registro(s) precisam de uma subcategoria substituta de {category}.</Text>
      <TextInput accessibilityLabel="Buscar subcategoria substituta" editable={!locked} maxLength={120} value={replacement} onChangeText={setReplacement} placeholder="Buscar substituta" placeholderTextColor="#92A4A2" style={s.input} />
      <View style={s.chips}>{suggestions.map(name => <Pressable accessibilityRole="button" disabled={locked} key={name} onPress={() => setReplacement(name)} style={[s.chip, replacement === name && s.chipActive]}><Text style={[s.chipText, replacement === name && s.addText]}>{name}</Text></Pressable>)}</View>
      {items.length < 2 && <Text style={s.help}>Crie outra subcategoria acima para receber estes registros.</Text>}
      <View style={s.actions}><Pressable accessibilityRole="button" disabled={disabled} onPress={() => {setDeleting(null); setReplacement('');}} style={s.cancel}><Text style={s.cancelText}>Cancelar</Text></Pressable><Pressable accessibilityRole="button" disabled={locked || items.length < 2} onPress={confirmReplacement} style={[s.confirm, (locked || items.length < 2) && s.disabled]}><Text style={s.confirmText}>Substituir e excluir</Text></Pressable></View>
      {!!replacement && <DiscardDraftAction disabled={disabled} owner={`${category}:delete:${deleting.name}`} label="Excluir rascunho da substituição" onDiscard={()=>{setDeleting(null);setReplacement('');}} />}
    </View>}
  </View>;
}

const s = StyleSheet.create({
  panel: { backgroundColor: '#102023', borderRadius: 12, padding: 12, marginBottom: 12 },
  title: { fontSize: 14, fontWeight: '700', color: '#F4F7F5' },
  help: { fontSize: 12, lineHeight: 18, color: '#92A4A2', marginTop: 6, marginBottom: 12 },
  createRow: { flexDirection: 'row', gap: 8, alignItems: 'center', marginBottom: 6 },
  input: { flex: 1, minWidth: 0, backgroundColor: '#142326', borderRadius: 10, borderWidth: 1, borderColor: '#263D40', paddingHorizontal: 10, paddingVertical: 12, color: '#F5F8F6', fontSize: 14 },
  add: { backgroundColor: '#49C5AA', borderRadius: 10, paddingHorizontal: 14, paddingVertical: 14, justifyContent: 'center' },
  addText: { color: '#061412', fontWeight: '800' },
  spinner: { marginVertical: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 10, borderBottomWidth: 1, borderColor: '#213235' },
  rowText: { flex: 1, minWidth: 0 },
  rowActions: { alignItems: 'flex-end' },
  name: { color: '#E7EFEC', fontSize: 14, fontWeight: '700' },
  meta: { color: '#92A4A2', fontSize: 11, marginTop: 5 },
  action: { paddingHorizontal: 10, minHeight: 44, justifyContent: 'center' },
  editText: { color: '#49C5AA', fontSize: 12, fontWeight: '700' },
  deleteText: { color: '#FF8C83', fontSize: 12, fontWeight: '700' },
  error: { color: '#FF8C83', fontSize: 12 },
  card: { backgroundColor: '#183034', borderWidth: 1, borderColor: '#315054', borderRadius: 12, padding: 10, gap: 8, marginTop: 12 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  cancel: { flexGrow: 1, borderWidth: 1, borderColor: '#315054', borderRadius: 10, padding: 11, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  cancelText: { color: '#C5D8D4', fontWeight: '700' },
  confirm: { flexGrow: 2, backgroundColor: '#E97970', borderRadius: 10, padding: 11, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  confirmText: { color: '#1B0807', fontWeight: '800' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: { maxWidth: '100%', backgroundColor: '#17282B', borderRadius: 12, paddingHorizontal: 10, paddingVertical: 12 },
  chipActive: { backgroundColor: '#49C5AA' },
  chipText: { color: '#AFC1BF', fontSize: 12 },
  disabled: { opacity: 0.45 },
});