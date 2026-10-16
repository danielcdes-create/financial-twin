import React, { useState } from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable, TextInput} from './InteractionControls';
import ControlledChoice from './ControlledChoice';
import {AccountProfile, accountLabel, selectableAccount} from '../utils/accountProfile';
import VoiceCapture from './VoiceCapture';
import StatementBatchReview from './StatementBatchReview';
import type {CaptureFileFormat} from '../utils/captureSelection';
import PdfPasswordSettings from './PdfPasswordSettings';
import DiscardDraftAction from './DiscardDraftAction';

type Props = {
  api: React.ComponentProps<typeof VoiceCapture>['api'];
  upload: React.ComponentProps<typeof VoiceCapture>['upload'];
  kind: string; onKind: (value: string) => void;
  merchant: string; onMerchant: (value: string) => void;
  amount: string; onAmount: (value: string) => void;
  date: string; onDate: (value: string) => void;
  origin: string; onOrigin: (value: string) => void;
  destination: string; onDestination: (value: string) => void;
  accounts: AccountProfile[];
  account: string; onAccount: (value: string) => void;
  payment: string; onPayment: (value: string) => void;
  installments?: string; onInstallments?: (value: string) => void;
  queueCount: number; rejectedCount: number; syncError: string; onSync: () => Promise<void>; onReview: () => void; onQueueSettings: () => void; onClearRejected: () => void;
  transcript: string;
  onMovementVoice: React.ComponentProps<typeof VoiceCapture>['onApply'];
  onEvidenceVoice: React.ComponentProps<typeof VoiceCapture>['onApply'];
  onSave: () => void;
  onDiscard: () => void | Promise<void>;
  onPhoto: () => void; onDocument: (format: CaptureFileFormat) => void; onStatement: () => void;
  onBatch: (format: CaptureFileFormat) => void; batchAccounts: any[]; batchRevision: number; onBatchUpdated: () => void | Promise<void>;
  onBatchResolved?: (batchId: string) => void;
  onPdfCpfChanged: () => Promise<void>;
  busy: boolean; savedRevision: number; message: string; error: string;
  notificationAvailable: boolean; notificationEnabled: boolean; onNotifications: () => void;
};

function Action({label, onPress, disabled, secondary = false}: {label: string; onPress: () => void; disabled?: boolean; secondary?: boolean}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{disabled: !!disabled}} aria-disabled={!!disabled}
    disabled={disabled} onPress={onPress} style={[s.action, secondary && s.secondary, disabled && s.disabled]}>
    <Text style={[s.actionText, secondary && s.secondaryText]}>{label}</Text>
  </Pressable>;
}

export default function CapturePanel(p: Props) {
  const [method, setMethod] = useState<'manual'|'voice'|'file'>('manual');
  const [target, setTarget] = useState<'movement'|'document'>('movement');
  const [fileTarget, setFileTarget] = useState<'document'|'batch'>('document');
  const [voiceBusy, setVoiceBusy] = useState(false);
  const locked = p.busy || voiceBusy;
  const displayAccount = (key: string) => accountLabel(p.accounts.find(item => item.key === key), key);
  const activeAccounts = p.accounts.filter(selectableAccount);
  const showForm = method === 'manual' || (method === 'voice' && target === 'movement' && !!p.transcript);
  const displayedDate = /^\d{4}-\d{2}-\d{2}$/.test(p.date) ? p.date.split('-').reverse().join('/') : p.date;
  const hasDraft = Boolean(p.merchant || p.amount || p.date || p.origin || p.destination || p.account || p.payment || p.installments || p.transcript || p.kind !== 'purchase');

  return <View style={s.page}>
    <View>
      <Text accessibilityRole="header" style={s.heading}>Como quer capturar?</Text>
      <Text style={s.note}>Escolha uma forma de registrar. Você confere os dados antes de confirmar.</Text>
    </View>
    <View style={s.methods}>
      {([{key: 'manual', label: 'Digitar'}, {key: 'voice', label: 'Voz'}, {key: 'file', label: 'Foto / arquivo'}] as const).map(item =>
        <Pressable key={item.key} accessibilityRole="button" accessibilityLabel={item.label}
          accessibilityState={{selected: method === item.key, disabled: locked}} aria-pressed={method === item.key} aria-disabled={locked} disabled={locked}
          onPress={() => setMethod(item.key)} style={[s.method, method === item.key && s.selected, locked && s.disabled]}>
          <Text style={[s.methodText, method === item.key && s.selectedText]}>{item.label}</Text>
        </Pressable>)}
    </View>

    {/* Keep one voice editor alive across method changes, preserving its clip and text. */}
    <View style={method === 'voice' ? s.voice : s.hidden} accessibilityElementsHidden={method !== 'voice'} importantForAccessibility={method === 'voice' ? 'auto' : 'no-hide-descendants'}>
      <Text style={s.label}>Usar a voz para</Text>
      <View style={s.targets}>
        {([{key: 'movement', label: 'Preencher movimentação'}, {key: 'document', label: 'Guardar como evidência'}] as const).map(item =>
          <Pressable key={item.key} accessibilityRole="button" accessibilityLabel={item.label}
            accessibilityState={{selected: target === item.key, disabled: locked}} aria-pressed={target === item.key} aria-disabled={locked} disabled={locked}
            onPress={() => setTarget(item.key)} style={[s.target, target === item.key && s.selected, locked && s.disabled]}>
            <Text style={[s.methodText, target === item.key && s.selectedText]}>{item.label}</Text>
          </Pressable>)}
      </View>
      <VoiceCapture draftKey="capture" resetRevision={p.savedRevision} api={p.api} upload={p.upload} context={target}
        onApply={target === 'movement' ? p.onMovementVoice : p.onEvidenceVoice}
        onEvidenceApply={p.onEvidenceVoice}
        onActivityChange={setVoiceBusy} disabled={p.busy} collapseAfterApply />
    </View>

    {!!p.error && <View accessibilityRole="alert" style={s.errorBox}><Text style={s.error}>{p.error}</Text></View>}
    {!!p.message && <View accessibilityLiveRegion="polite" style={s.messageBox}><Text style={s.message}>{p.message}</Text></View>}

    {(p.queueCount > 0 || p.rejectedCount > 0 || !!p.syncError) && <View style={s.form}>
      <Text style={s.section}>Envios pendentes</Text>
      <Text style={s.note}>{p.queueCount} captura(s) aguardando envio neste aparelho.</Text>
      {p.rejectedCount > 0 && <><Text style={s.error}>{p.rejectedCount} captura(s) precisam de atenção na fila local.</Text><Action label="Excluir itens com falha" onPress={p.onClearRejected} disabled={locked} secondary /><Action label="Conferir fila em Ajustes" onPress={p.onQueueSettings} disabled={locked} secondary /></>}
      {!!p.syncError && <Text accessibilityRole="alert" style={s.error}>{p.syncError}</Text>}
      <Action label="Tentar enviar agora" onPress={p.onSync} disabled={locked} secondary />
    </View>}
    {!!p.message && <Action label="Conferir em Revisão" onPress={p.onReview} disabled={locked} secondary />}

    {showForm && <View style={s.form}>
      <Text accessibilityRole="header" style={s.section}>{method === 'voice' ? 'Confira a movimentação' : 'Nova movimentação'}</Text>
      <ControlledChoice disabled={locked} label="Tipo de movimentação" value={p.kind} options={['purchase','income','own_transfer']}
        display={value => value === 'own_transfer' ? 'Transferência entre minhas contas' : value === 'income' ? 'Receita ou entrada' : 'Compra ou pagamento'}
        onChange={p.onKind} allowClear={false} />
      {p.kind === 'own_transfer' ? <>
        <ControlledChoice disabled={locked} label="Conta de origem" value={p.origin} options={activeAccounts.map(item => item.key)} display={displayAccount} onChange={key => {p.onOrigin(key); if (key === p.destination) p.onDestination('');}} allowClear={false} />
        <ControlledChoice disabled={locked} label="Conta de destino" value={p.destination} options={activeAccounts.filter(item => item.key !== p.origin).map(item => item.key)} display={displayAccount} onChange={p.onDestination} allowClear={false} />
        <Text style={s.note}>O valor sai da origem e entra no destino. A transferência não conta como gasto ou receita.</Text>
        {activeAccounts.length < 2 && <Text style={s.note}>Cadastre duas contas abertas para registrar uma transferência.</Text>}
      </> : <View style={s.field}>
        <Text style={s.label}>{p.kind === 'income' ? 'Origem da entrada' : 'Estabelecimento'}</Text>
        <TextInput accessibilityLabel={p.kind === 'income' ? 'Origem da entrada' : 'Estabelecimento'} value={p.merchant}
          onChangeText={p.onMerchant} editable={!locked} placeholder={p.kind === 'income' ? 'Ex.: Salário' : 'Ex.: Padaria ABC'} placeholderTextColor="#8BA3A0" style={s.input} />
      </View>}
      {p.kind !== 'own_transfer' && <>
        <ControlledChoice disabled={locked} label={p.kind === 'income' ? 'Conta que recebeu (opcional)' : 'Conta usada (opcional)'} value={p.account} options={activeAccounts.map(item => item.key)} display={displayAccount} onChange={p.onAccount} />
        <ControlledChoice disabled={locked} label="Forma de pagamento (opcional)" value={p.payment} options={['pix','cartao_credito','cartao_debito','dinheiro','boleto','transferencia']} display={value => ({pix:'Pix',cartao_credito:'Cartão de crédito',cartao_debito:'Cartão de débito',dinheiro:'Dinheiro',boleto:'Boleto',transferencia:'Transferência'}[value] || value)} onChange={p.onPayment} />
        <Text style={s.note}>Conta ou cartão específico pode ser conferido em Revisão.</Text>
      </>}
      <View style={s.field}>
        <Text style={s.label}>Valor (R$)</Text>
        <TextInput accessibilityLabel="Valor da movimentação" value={p.amount} onChangeText={p.onAmount} editable={!locked}
          keyboardType="decimal-pad" placeholder="0,00" placeholderTextColor="#8BA3A0" style={s.input} />
      </View>
      <View style={s.field}>
        <Text style={s.label}>Data (opcional)</Text>
        <TextInput accessibilityLabel="Data da movimentação" value={displayedDate} onChangeText={p.onDate} editable={!locked}
          keyboardType="numbers-and-punctuation" placeholder="DD/MM/AAAA" placeholderTextColor="#8BA3A0" maxLength={10} style={s.input} />
        <Text style={s.note}>Em branco, usa a data e a hora de agora.</Text>
      </View>
      {p.kind === 'purchase' && <View style={s.field}>
        <Text style={s.label}>Parcelas (opcional)</Text>
        <TextInput accessibilityLabel="Número de parcelas" value={p.installments || ''} editable={!locked}
          onChangeText={value => p.onInstallments?.(value.replace(/[^\d]/g, '').slice(0, 2))}
          keyboardType="number-pad" placeholder="À vista" placeholderTextColor="#8BA3A0" maxLength={2} style={s.input} />
        <Text style={s.note}>De 2 a 48, o valor acima é o total da compra. O extrato recebe só a primeira parcela, na data da compra. As outras entram quando forem cobradas. A conciliação fica para o vencimento da fatura. Em branco, a compra fica à vista.</Text>
      </View>}
      <Action label={p.busy ? 'Salvando…' : 'Salvar movimentação'} onPress={p.onSave} disabled={locked} />
      {hasDraft && <DiscardDraftAction disabled={locked} label="Excluir rascunho da movimentação" onDiscard={p.onDiscard} message="O preenchimento desta movimentação será apagado. Movimentações já salvas e capturas na fila continuam preservadas." />}
      <Text style={s.note}>Digitar guarda um rascunho neste aparelho. Toque em Salvar movimentação para enviar; depois confira e confirme em Revisão. A captura aparece no Extrato quando o servidor a recebe.</Text>
    </View>}

    {method === 'file' && <View style={s.form}>
      <Text style={s.label}>O que este arquivo contém?</Text>
      <View style={s.methods}>
        {([{key: 'document', label: 'Um comprovante'}, {key: 'batch', label: 'Fatura / extrato'}] as const).map(item =>
          <Pressable key={item.key} accessibilityRole="button" accessibilityState={{selected: fileTarget === item.key, disabled: locked}} aria-pressed={fileTarget === item.key} aria-disabled={locked}
            disabled={locked} onPress={() => setFileTarget(item.key)} style={[s.method, fileTarget === item.key && s.selected]}>
            <Text style={[s.methodText, fileTarget === item.key && s.selectedText]}>{item.label}</Text>
          </Pressable>)}
      </View>
      {fileTarget === 'document' ? <>
      <Text accessibilityRole="header" style={s.section}>Adicionar comprovante</Text>
      <Text style={s.note}>Fotografe ou escolha um comprovante. Os dados extraídos ficam disponíveis para revisão.</Text>
      <Action label="Fotografar comprovante" onPress={p.onPhoto} disabled={p.busy} />
      <Action label="Escolher foto ou print" onPress={() => p.onDocument('image')} disabled={p.busy} secondary />
      <Action label="Escolher PDF do comprovante" onPress={() => p.onDocument('pdf')} disabled={p.busy} secondary />
      <Text style={s.note}>A extração envia o arquivo ao servidor e à xAI quando houver conexão.</Text>
      </> : <>
      <View style={s.import}>
        <Text accessibilityRole="header" style={s.section}>Capturar fatura ou extrato</Text>
        <Text style={s.note}>Um print ou PDF pode conter vários dias. Confira cada linha antes de inserir; duplicidades são barradas.</Text>
        <Action label="Escolher print da fatura ou extrato" onPress={() => p.onBatch('image')} disabled={p.busy} />
        <Action label="Escolher PDF da fatura ou extrato" onPress={() => p.onBatch('pdf')} disabled={p.busy} secondary />
        <Text style={s.note}>Até 10 MiB por arquivo e 10 páginas por PDF. Se pedir senha, o app tenta o CPF informado abaixo.</Text>
        <Text style={s.note}>Também aceita CSV ou Excel exportado pelo banco, com a mesma conferência.</Text>
        <Action label="Escolher CSV ou Excel" onPress={p.onStatement} disabled={p.busy} secondary />
      </View>
      </>}
      {p.busy && <Text accessibilityLiveRegion="polite" style={s.note}>Preparando arquivo no aparelho…</Text>}
      <PdfPasswordSettings disabled={locked} onChanged={p.onPdfCpfChanged} />
    </View>}

    <StatementBatchReview api={p.api} visible={method === 'file'} revision={p.batchRevision}
      accounts={p.batchAccounts} profiles={p.accounts} onUpdated={p.onBatchUpdated} onResolved={p.onBatchResolved} />

    <View style={s.automatic}>
      <View style={s.automaticHeader}>
        <Text accessibilityRole="header" style={s.automaticTitle}>Captura de notificações</Text>
        <Text style={p.notificationEnabled ? s.enabled : s.note}>{!p.notificationAvailable ? 'Android' : p.notificationEnabled ? 'Ativa' : 'Inativa'}</Text>
      </View>
      <Text style={s.note}>{p.notificationAvailable ? 'Compras dos bancos selecionados entram na fila para análise.' : 'Disponível no aplicativo Android instalado.'}</Text>
      <Action label="Configurar notificações" onPress={p.onNotifications} disabled={!p.notificationAvailable || p.busy} secondary />
    </View>
  </View>;
}

const s = StyleSheet.create({
  page: {gap: 16}, heading: {fontSize: 21, fontWeight: '700', color: '#F4F7F5'},
  section: {fontSize: 18, fontWeight: '700', color: '#F4F7F5', marginBottom: 12},
  note: {fontSize: 12, lineHeight: 18, color: '#AFC1BF', marginTop: 5},
  methods: {flexDirection: 'row', gap: 8}, method: {flex: 1, minHeight: 48, justifyContent: 'center', alignItems: 'center', padding: 8, borderRadius: 12, borderWidth: 1, borderColor: '#315054', backgroundColor: '#142326'},
  methodText: {fontSize: 13, lineHeight: 18, textAlign: 'center', fontWeight: '600', color: '#D5E2DF'},
  selected: {backgroundColor: '#173C35', borderColor: '#49C5AA'}, selectedText: {color: '#6BE0C2'},
  targets: {gap: 8, marginTop: 8}, target: {minHeight: 44, justifyContent: 'center', borderRadius: 10, padding: 10, borderWidth: 1, borderColor: '#315054'},
  voice: {}, hidden: {display: 'none'}, form: {paddingTop: 2}, field: {marginBottom: 16},
  label: {fontSize: 13, fontWeight: '600', color: '#B6C8C4', marginBottom: 7},
  input: {minHeight: 48, backgroundColor: '#142326', borderWidth: 1, borderColor: '#315054', color: '#F4F7F5', padding: 13, borderRadius: 12, fontSize: 16},
  action: {minHeight: 48, padding: 13, borderRadius: 12, backgroundColor: '#49C5AA', alignItems: 'center', justifyContent: 'center', marginTop: 9},
  actionText: {fontSize: 14, fontWeight: '700', color: '#061412', textAlign: 'center'},
  secondary: {backgroundColor: '#142326', borderWidth: 1, borderColor: '#315054'}, secondaryText: {color: '#D5E2DF'}, disabled: {opacity: .5},
  import: {marginTop: 22, paddingTop: 18, borderTopWidth: 1, borderTopColor: '#263D40'},
  automatic: {padding: 14, backgroundColor: '#142326', borderRadius: 12, borderWidth: 1, borderColor: '#263D40', marginTop: 8},
  automaticHeader: {flexDirection: 'row', justifyContent: 'space-between', gap: 10}, automaticTitle: {fontSize: 14, fontWeight: '600', color: '#D5E2DF', flex: 1}, enabled: {fontSize: 12, color: '#6BE0C2'},
  errorBox: {padding: 13, backgroundColor: '#352421', borderRadius: 12}, error: {fontSize: 13, lineHeight: 19, color: '#FFAAA0'},
  messageBox: {padding: 13, backgroundColor: '#173C35', borderRadius: 12}, message: {fontSize: 13, lineHeight: 19, color: '#B8F1DF'},
});
