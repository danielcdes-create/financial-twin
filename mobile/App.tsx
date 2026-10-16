import { Alert } from "./utils/appAlert";
import SingleLineValue from "./components/SingleLineValue";
import { financialDateLabel } from "./utils/classification";
import FinancialLab from "./components/FinancialLab";
import PurchaseCheck from "./components/PurchaseCheck";
import CapturePanel from "./components/CapturePanel";
import {manualCapturePayload} from './utils/manualCapture';
import {useManualDraft} from './utils/manualDraft';
import { captureFileLimit, captureMimeType, capturePickerOptions, type CaptureFileFormat, type CaptureFileKind } from "./utils/captureSelection";
import {isPdfCapture} from "./utils/pdfCpf";
import {submitQueuedCapture} from "./utils/captureSubmission";
import {enqueueVoiceEvidence} from './utils/voiceEvidence';
import {deleteVoiceDraft, type VoiceEvidenceCommand} from "./utils/voiceDraft";
import type { PurchaseImage } from "./utils/purchaseImage";
import { persistQueuedFile } from "./utils/durableFiles";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import {reviewHasDifferences,reviewDecisionBody} from "./utils/reviewDecision";
import { useSQLiteContext } from "./utils/queueDb";
import NotificationCapture from "expo-notification-capture";
import {importNativeInbox, nativePendingCount} from './utils/nativeInbox';
import { router, usePathname } from "expo-router";
import {dispatchLocalBack,LocalBackScope,useLocalBack} from './utils/localBack';
import { useAuth } from "./components/AuthProvider";
import { API, api, authState } from "./utils/sessionClient";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {ActivityIndicator, BackHandler, FlatList, Platform, ScrollView, StyleSheet, Text, useWindowDimensions, View} from 'react-native';
import {Pressable, TextInput} from './components/InteractionControls';
import {
  SafeAreaView,
  useSafeAreaInsets,
} from "react-native-safe-area-context";
import RecordReview from "./components/RecordReview";
import PendingMutations from "./components/PendingMutations";
import DraftManager from "./components/DraftManager";
import {compactLayout} from "./utils/responsiveLayout";
import AppUpdate from "./components/AppUpdate";
import ProvisionalReview from "./components/ProvisionalReview";
import CategoryManager from "./components/CategoryManager";
import {classificationLine, movementPath, natureLabel, ownAccountTransfer, parcelamentoListMark, parcelamentoPurchaseSummary, paymentLabel, recordCategory, statementInstrument} from './utils/movementFilters';
import {civilMonthWindow} from './utils/commitmentPresentation';
import CardManager from "./components/CardManager";
import MonthClose from "./components/MonthClose";
import TransactionEditor from "./components/TransactionEditor";
import DeleteMovement from "./components/DeleteMovement";
import CommitmentManager from "./components/CommitmentManager";
import StatementCommitmentLink from "./components/StatementCommitmentLink";
import AccountPositions from "./components/AccountPositions";
import { accountLabel, selectableAccount } from "./utils/accountProfile";
import { ApiError } from "./utils/apiClient";
import { bindChartScroller, recordChartOffset } from "./utils/chartScroll";
import { LatestQueries } from "./utils/latestQueries";
import { QueueRow, sameQueueRow } from "./utils/syncQueue";
import {readQueueAcknowledgement, persistQueueAcknowledgement} from "./utils/queueAcknowledgements";
import {finalizeQueueRow} from "./utils/queueFinalization";
import {clearQueueFailure, persistQueueFailure} from "./utils/queueFailures";
import {useAsyncScope} from "./utils/useAsyncScope";
import { useQueueSync } from "./utils/useQueueSync";
import {canReviewAsStatement, queueFailureLabel, reconcileImportedFiles, requeueAsStatement} from "./utils/queueRecovery";
import { useForeground } from "./utils/useForeground";
import { withDevicePrompt } from "./utils/devicePrompt";

type Tab =
  | "Início"
  | "Movimentações"
  | "Detalhe"
  | "Capturar"
  | "Revisão"
  | "Decisões"
  | "Fechamento"
  | "Comprar"
  | "Mais"
  | "Contas"
  | "Cartões"
  | "Categorias"
  | "Compromissos"
  | "Rascunhos"
  | "Ajustes";
type DashboardPeriod = "month" | "3m" | "6m" | "12m" | "year" | "all";
const DASHBOARD_PERIODS: { key: DashboardPeriod; label: string }[] = [
  { key: "month", label: "Mês" },
  { key: "3m", label: "3 meses" },
  { key: "6m", label: "6 meses" },
  { key: "12m", label: "12 meses" },
  { key: "year", label: "Ano" },
  { key: "all", label: "Tudo" },
];
const VISIBLE_DATA_MAX_AGE_MS = 2 * 60 * 1000;
const newId = () =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 3) | 8).toString(16);
  });
const money = (v: number) =>
  new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v || 0);
const dashboardMoney = money;

function ParcelamentoPurchaseDetail({item}: {item: any}) {
  const summary = parcelamentoPurchaseSummary(item);
  if (!summary) return null;
  return (
    <View style={s.installmentReference} accessibilityRole="summary">
      <Text style={s.muted}>Compra parcelada</Text>
      <Text style={s.body}>{summary.count} parcelas · total {money(summary.total)}</Text>
      <Text style={s.muted}>Esta linha é a parcela 1 de {summary.count}.</Text>
    </View>
  );
}
const currentMonthWindow = civilMonthWindow;
const monthsSinceJanuary = (series: any[], year: number, month: number) => {
  const byMonth = new Map(series.map((item) => [item.month, item]));
  return Array.from({ length: month }, (_, index) => {
    const key = `${year}-${String(index + 1).padStart(2, "0")}`;
    return byMonth.get(key) || { month: key, income: 0, expenses: 0, net: 0 };
  });
};
const json = api.json;

class RetryController {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private delay = 5000;

  schedule(onRetry: () => void) {
    if (this.timer) return;
    const delay = this.delay;
    this.timer = setTimeout(() => {
      this.timer = null;
      onRetry();
    }, delay);
    this.delay = Math.min(delay * 2, 60000);
  }

  clear() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  reset() {
    this.clear();
    this.delay = 5000;
  }
}

class QueueSyncState {
  private session = "";
  private updated = false;

  setSession(value: string) {
    this.session = value;
  }

  belongsTo(value: string) {
    return this.session === value;
  }

  markUpdated() {
    this.updated = true;
  }

  consumeUpdated() {
    if (!this.updated) return false;
    this.updated = false;
    return true;
  }
}

export default function App({initialPurchaseImage,initialPurchaseError,navigationRoute='/'}:{initialPurchaseImage?:PurchaseImage|null;initialPurchaseError?:string;navigationRoute?:'/'|'/handle-share'}={}) {
  const insets = useSafeAreaInsets();
  const {width,fontScale}=useWindowDimensions();
  const compact=compactLayout(width,fontScale);
  const movementQuery = useRef("");
  const movementCategory = useRef("");
  const movementSpendingOnly = useRef(false);
  const movementAccount = useRef("");
  const movementCursor = useRef<string | null>(null);
  const movementDateRange = useRef<{ from: string; to: string } | null>(null);
  const dashboardPeriod = useRef<DashboardPeriod>("month");
  const [yearDashboard, setYearDashboard] = useState<any>(null);
  const [monthDashboard, setMonthDashboard] = useState<any>(null);
  const db = useSQLiteContext();
  const auth = useAuth();
  const captureScope = useAsyncScope(authState.sessionIdentity());
  const [tab, setTab] = useState<Tab>(initialPurchaseImage||initialPurchaseError?"Comprar":"Início");
  const route=usePathname();
  const activeRoute=route===navigationRoute;
  const localParent=tab==='Detalhe'?'Movimentações':['Decisões','Fechamento','Comprar','Rascunhos','Contas','Cartões','Categorias','Compromissos','Ajustes'].includes(tab)?'Mais':null;
  useLocalBack(()=>setTab(localParent as Tab),Boolean(localParent)&&activeRoute,0);
  useEffect(()=>{
    if(Platform.OS!=='android'||!activeRoute)return;
    const back=BackHandler.addEventListener('hardwareBackPress',dispatchLocalBack);
    return()=>back.remove();
  },[activeRoute]);
  const availability=useSyncExternalStore(api.subscribeAvailability,api.getAvailability,api.getAvailability);
  const online=availability==='connected';
  const [connectionFailed,setConnectionFailed]=useState(false);
  const [queueCount, setQueueCount] = useState(0);
  const [deadLetters, setDeadLetters] = useState<QueueRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [manualLoading, setManualLoading] = useState(false);
  const [dashboard, setDashboard] = useState<any>(null);
  const [dashboardPeriodState, setDashboardPeriodState] =
    useState<DashboardPeriod>("month");
  const [items, setItems] = useState<any[]>([]);
  const [reviews, setReviews] = useState<any[]>([]);
  const [reviewAcknowledgements,setReviewAcknowledgements]=useState<Record<string,boolean>>({});
  const captureMessageId=useRef("");
  const syncIdentity=useRef(0);
  const [reviewPage,setReviewPage]=useState<{total:number;next_cursor:string|null}>({total:0,next_cursor:null});
  const [reviewMoreBusy,setReviewMoreBusy]=useState(false);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountProfiles, setAccountProfiles] = useState<any[]>([]);
  const contentScroll = useRef<ScrollView>(null);
  const [cards, setCards] = useState<any[]>([]);
  const [selectedItem, setSelectedItem] = useState<any>(null);
  const [editingItem, setEditingItem] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);
  const [movementSearch, setMovementSearch] = useState("");
  const [movementAccountId, setMovementAccountId] = useState("");
  const [movementFilterLabel, setMovementFilterLabel] = useState("");
  const [hasMovementFilter, setHasMovementFilter] = useState(false);
  const [refreshErrors, setRefreshErrors] = useState<Record<string, string>>(
    {},
  );
  const [syncError, setSyncError] = useState("");
  const [nativeInboxError,setNativeInboxError]=useState('');
  const [captureMessage, setCaptureMessage] = useState("");
  const [deletionNotice, setDeletionNotice] = useState("");
  const [captureError, setCaptureError] = useState("");
  const manual = useManualDraft(db, setCaptureError);
  const {merchant,amount,kind:captureKind,transcript:voiceTranscript,date:captureDate,origin:transferOrigin,destination:transferDestination,account:captureAccount,payment:capturePayment}=manual.draft;
  const setMerchant=(value:string) => manual.change('merchant',value);
  const setAmount=(value:string) => manual.change('amount',value);
  const setCaptureKind=(value:'purchase'|'income'|'own_transfer') => manual.change('kind',value);
  const setVoiceTranscript=(value:string) => manual.change('transcript',value);
  const setCaptureDate=(value:string) => manual.change('date',value);
  const setTransferOrigin=(value:string) => manual.change('origin',value);
  const setTransferDestination=(value:string) => manual.change('destination',value);
  const [captureSaving, setCaptureSaving] = useState(false);
  const [captureSavedRevision, setCaptureSavedRevision] = useState(0);
  const captureSavingLock = useRef(false);
  const sessionReady = authState.unlocked();
  const [syncRevision, setSyncRevision] = useState(0);
  const [movementTotal, setMovementTotal] = useState(0);
  const [movementResultPath, setMovementResultPath] = useState('');
  const [requestedMovementPath, setRequestedMovementPath] = useState(() => movementPath({}));
  const [movementHasMore, setMovementHasMore] = useState(false);
  const [movementLoadingMore, setMovementLoadingMore] = useState(false);
  const movementAccountOptions = useMemo(
    () =>
      [...accounts
        .map((item) => ({
          id: String(item.id),
          label: `${item.tipo === "cartao_credito" ? "Cartão" : "Conta"} · ${accountLabel(accountProfiles.find(profile => profile.legacy_account_id === String(item.id)), item.instituicao || "Sem instituição")}${item.cartao_final ? ` ·•••• ${item.cartao_final}` : ""}`,
        })), ...accountProfiles.filter(item => !item.legacy_account_id).map(item => ({id: `profile:${item.key}`, label: `Conta · ${accountLabel(item)}`}))],
    [accounts, accountProfiles],
  );
  const ledgerAccountLabel = useCallback((item: any) => {
    const account = accounts.find((entry) => String(entry.id) === String(item?.account_id));
    if (account) {
      const profile = accountProfiles.find((candidate) => candidate.legacy_account_id === String(account.id));
      const name = accountLabel(profile, account.apelido || account.instituicao || "Conta");
      const kind = account.tipo === "cartao_credito" ? "Cartão" : "Conta";
      return `${kind} · ${name}${account.cartao_final ? ` ·••• ${account.cartao_final}` : ""}`;
    }
    const profile = accountProfiles.find((candidate) => candidate.key === item?.account_profile_id);
    return profile ? accountLabel(profile) : "";
  }, [accounts, accountProfiles]);
  const retryController = useMemo(() => new RetryController(), []);
  const alive = useRef(true);
  const queueUiVersion = useRef(0);
  useEffect(() => {const lifetime = alive; lifetime.current = true; return () => {lifetime.current = false; retryController.reset();};}, [retryController]);
  const syncState = useMemo(() => new QueueSyncState(), []);
  const [lastLoaded] = useState(
    () => new Map<string, { key: string; at: number; generation: number }>(),
  );
  const [loadedResources, setLoadedResources] = useState<Record<string, boolean>>({});
  const manualLoads = useRef(0);
  const handledSyncRevision = useRef(0);
  const visibleRefreshRef = useRef<(force?: boolean) => Promise<void>>(() => Promise.resolve());
  const foreground = useForeground();
  useEffect(()=>{api.configureMutationStore(db);},[db]);
  const [requests] = useState(
    () =>
      new LatestQueries((pending, errors, failed) => {
        setLoading(pending > 0);
        setRefreshErrors(errors);
        setConnectionFailed(failed);
      }),
  );
  const detailId = useRef("");
  const queue = useCallback(
    () => db.getAllAsync<QueueRow>("SELECT e.*, s.attempt_count, s.next_attempt_at, s.last_error FROM event_queue e LEFT JOIN queue_state s ON s.event_id=e.event_id WHERE COALESCE(s.quarantined,0)=0 ORDER BY e.id"),
    [db],
  );
  const quarantinedQueue = useCallback(
    () => db.getAllAsync<QueueRow>("SELECT e.*, s.attempt_count, s.next_attempt_at, s.last_error FROM event_queue e JOIN queue_state s ON s.event_id=e.event_id WHERE s.quarantined=1 ORDER BY e.id"),
    [db],
  );
  const lastImportCheck = useRef(0);
  const importCheckFlight = useRef<Promise<string[]> | null>(null);
  const ensureQueueSession = useCallback((session: number) => {
    const interrupted = () => {throw Object.assign(new Error('Sincronização interrompida. O item permanece preservado.'), {name: 'AbortError'});};
    if (!alive.current || !authState.unlocked()) return interrupted();
    const token = authState.getToken();
    if (authState.sessionIdentity() === session && authState.unlocked() && !token) return interrupted();
    if (!token || authState.sessionIdentity() !== session)
      throw new ApiError('A sessão mudou. O item permanece preservado.', 401);
  }, []);
  const reconcileLocalFiles = useCallback((force = false): Promise<string[]> => {
    if (!alive.current || !authState.getToken()) return Promise.resolve([]);
    if (importCheckFlight.current) return importCheckFlight.current;
    if (!force && Date.now() - lastImportCheck.current < 120000) return Promise.resolve([]);
    const valid = captureScope();
    importCheckFlight.current = (async () => {
      const rows = await quarantinedQueue();
      if (!rows.length) return [];
      lastImportCheck.current = Date.now();
      const session = authState.sessionIdentity();
      const removed = await reconcileImportedFiles(db, rows, api.upload, () => {
        ensureQueueSession(session);
      });
      const remaining = await quarantinedQueue();
      if (valid()) {
        setDeadLetters(remaining);
        if (removed.includes(captureMessageId.current)) {captureMessageId.current = ""; setCaptureMessage("");}
      }
      return removed;
    })().finally(() => {importCheckFlight.current = null;});
    return importCheckFlight.current;
  }, [db, quarantinedQueue, ensureQueueSession, captureScope]);
  const sync = useQueueSync({
      prepare: async () => {
        if (!alive.current || !authState.getToken() || !authState.unlocked()) {
          return false;
        }
        syncState.setSession(authState.getToken());
        syncIdentity.current=authState.sessionIdentity();
        // Ingest local evidence before any network probe, including offline.
        if(NotificationCapture.available){
          const snapshot=NotificationCapture.getInboxSnapshot();
          setNativeInboxError(snapshot.error);
          try{
            const active=await queue(), quarantined=await quarantinedQueue();
            ensureQueueSession(syncIdentity.current);
            if(snapshot.state!=='unavailable')setQueueCount(nativePendingCount(active,quarantined,snapshot.events));
            await importNativeInbox(db,snapshot,()=>ensureQueueSession(syncIdentity.current));
          }catch(error){
            if((error as {name?:string})?.name!=='AbortError'&&authState.unlocked()){
              setNativeInboxError(snapshot.error||'Não foi possível importar a fila local. As notificações continuam preservadas na origem; a contagem pode estar incompleta.');
              if(snapshot.state!=='unavailable')setQueueCount(current=>Math.max(current,snapshot.events.length));
            }
            throw error;
          }
        }
        if(!API)return false;
        const health = await json("/health");
        if (health.mongodb !== "connected") throw new ApiError("MongoDB indisponível. A fila tentará novamente.", 503);
        if (health.auth_required && !authState.getToken()) {
          return false;
        }
        if ((!syncState.belongsTo(authState.getToken()) || syncIdentity.current !== authState.sessionIdentity())) return false;
        await reconcileLocalFiles();
        return syncState.belongsTo(authState.getToken()) && syncIdentity.current === authState.sessionIdentity();
      },
      list: queue,
      getAcknowledgement: (row: QueueRow) => readQueueAcknowledgement(db, row),
      acknowledge: (row: QueueRow, result: any) => persistQueueAcknowledgement(db, row, result),
      send: async (row: QueueRow) => {
        ensureQueueSession(syncIdentity.current);
        try {
          if (row.kind === "event" || row.kind === "native_event")
            return await json("/v1/events", {
              method: "POST",
              body: row.payload,
            });
          return await submitQueuedCapture(row, api.upload, () => {
            ensureQueueSession(syncIdentity.current);
          });
        } catch (error) {
          if (
            error instanceof ApiError &&
            error.status === 401 &&
            syncState.belongsTo(authState.getToken())
          ) {
            authState.lock();
          }
          throw error;
        }
      },
      done: (row: QueueRow, result: any) => {
        ensureQueueSession(syncIdentity.current);
        if (row.kind !== "batch" && row.event_id===captureMessageId.current) setCaptureMessage(result.status === 'matched' ? 'Captura vinculada a uma movimentação existente. Confira no Extrato.' : 'Movimentação recebida. Confira os dados em Revisão antes de confirmar.');
        if (row.kind === "batch" && row.event_id===captureMessageId.current) {
          if (result.needs_review === true) setCaptureMessage("Lote salvo para revisão. Abra Foto / arquivo e confira as linhas antes de inserir.");
          else {captureMessageId.current = ""; setCaptureMessage("");}
        }
        if (row.kind === "native_event")
          NotificationCapture.markSynced(row.event_id);
        syncState.markUpdated();
      },
      remove: (row: QueueRow) => finalizeQueueRow(db, row, () => {
        ensureQueueSession(syncIdentity.current);
      }),
      recordFailure: (row: QueueRow, error: string, attempts: number, nextAttemptAt: string, quarantine: boolean) =>
        persistQueueFailure(db, row, error, attempts, nextAttemptAt, quarantine),
      changed: (count, error) => {
        if (!alive.current) return;
        const valid = captureScope(), version = ++queueUiVersion.current;
        if (count >= 0 && !NotificationCapture.available) setQueueCount(count);
        void Promise.all([quarantinedQueue(),queue()]).then(([rows,active]) => {
          if(!valid()||version!==queueUiVersion.current||!authState.unlocked())return;
          setDeadLetters(rows);
          if(NotificationCapture.available){const snapshot=NotificationCapture.getInboxSnapshot();setNativeInboxError(snapshot.error);if(snapshot.state!=='unavailable')setQueueCount(nativePendingCount(active,rows,snapshot.events));}
        }).catch(() => {});
        setSyncError(error);
        if (error) {
          retryController.schedule(() => {
            setSyncRevision((value) => value + 1);
            return sync();
          });
        } else {
          retryController.reset();
        }
        if (syncState.consumeUpdated()) {
          setSyncRevision((value) => value + 1);
        }
      },
  });
  const refresh = useCallback(
    (
      scope:
        | "all"
        | "finance"
        | "dashboard"
        | "dashboard-period"
        | "movements"
        | "reviews"
        | "account-catalog"
        | "accounts"
        | "cards" = "all",
      showLoading = false,
    ) => {
      if (!API) {
        return Promise.resolve();
      }
      const work: Promise<void>[] = [];
      const readGeneration = api.getReadGeneration();
      const run = (
        resource: string,
        path: string,
        publish: (value: any) => void,
      ) =>
        work.push(
          requests.run(
            resource,
            `${path}:${readGeneration}`,
            (signal) => json(path, { signal }),
            (value) => {
              publish(value);
              lastLoaded.set(resource, { key: path, at: Date.now(), generation: readGeneration });
              setLoadedResources((current) => ({ ...current, [resource]: true }));
            },
          ),
        );
      const synced = () => {
        setLastSyncedAt(new Date());
      };
      if (
        scope === "all" ||
        scope === "finance" ||
        scope === "dashboard" ||
        scope === "dashboard-period"
      ) {
        const window = currentMonthWindow();
        const path = `/v1/dashboard/overview?period=${dashboardPeriod.current}&year_start=${window.yearStart}&month_start=${window.monthStart}&month_end=${window.monthEnd}`;
        run("início", path, (value) => {
          setDashboard(value.selected);
          setYearDashboard(value.year);
          setMonthDashboard(value.month);
          synced();
        });
      }
      if (scope === "all" || scope === "finance" || scope === "movements") {
        // A page from the previous filter/snapshot must not append to a reset.
        requests.cancel("movimentações-página");
        const path = movementPath({search:movementQuery.current,category:movementCategory.current,
          account:movementAccount.current,range:movementDateRange.current,spendingOnly:movementSpendingOnly.current});
        setRequestedMovementPath(path);
        work.push(
          requests.run(
            "movimentações",
            `${path}:${readGeneration}`,
            (signal) => json(path, { signal }),
            (result) => {
              const merged = result.items || [];
              setItems(merged);
              setMovementResultPath(path);
              setMovementTotal(result.total || 0);
              setMovementHasMore(Boolean(result.has_more));
              movementCursor.current = result.next_cursor || null;
              setSelectedItem((current: any) => {
                if (!current) return current;
                const fresh = merged.find(
                  (value: any) =>
                    (value.id && value.id === current.id) ||
                    (value.provisional_id &&
                      value.provisional_id === current.provisional_id),
                );
                return fresh ? { ...current, ...fresh } : current;
              });
              synced();
              lastLoaded.set("movimentações", { key: path, at: Date.now(), generation: readGeneration });
              setLoadedResources((current) => ({ ...current, movimentações: true }));
            },
          ),
        );
      }
      if (scope === "all" || scope === "reviews") {
        requests.cancel("revisão-página");
        run("revisão", "/v1/reviews", (result) =>
          {setReviews(result.items || []);setReviewPage({total:result.total||0,next_cursor:result.next_cursor||null})},
        );
      }
      if (scope === "all" || scope === "accounts" || scope === "account-catalog" || (scope === "movements" && accounts.length === 0))
        run("contas", "/v1/accounts", (result) =>
          setAccounts(result.items || []),
        );
      if (scope === "all" || scope === "accounts")
        run("saldos", "/v1/account-positions", (result) =>
          setAccountProfiles(result.items || []),
        );
      if (scope === "all" || scope === "cards")
        run("cartões", "/v1/card-catalog", (result) => setCards(result.items || []));
      if (scope === "all") void sync();
      if (showLoading && work.length) {
        manualLoads.current += 1;
        setManualLoading(true);
      }
      return Promise.all(work).then(() => undefined).finally(() => {
        if (showLoading && work.length) {
          manualLoads.current -= 1;
          setManualLoading(manualLoads.current > 0);
        }
      });
    },
    [accounts.length, lastLoaded, requests, sync],
  );
  const refreshVisible = useCallback((force = false) => {
    const fresh = (resource: string, key: string) => {
      const loaded = lastLoaded.get(resource);
      return !force && loaded?.key === key &&
        loaded.generation === api.getReadGeneration() &&
        Date.now() - loaded.at < VISIBLE_DATA_MAX_AGE_MS;
    };
    const window = currentMonthWindow();
    const dashboardPath = `/v1/dashboard/overview?period=${dashboardPeriod.current}&year_start=${window.yearStart}&month_start=${window.monthStart}&month_end=${window.monthEnd}`;
    const movementsPath = movementPath({search:movementQuery.current,category:movementCategory.current,
      account:movementAccount.current,range:movementDateRange.current,spendingOnly:movementSpendingOnly.current});
    if (tab === "Início") return fresh("início", dashboardPath) ? Promise.resolve() : refresh("dashboard");
    if (tab === "Movimentações") return fresh("movimentações", movementsPath) ? Promise.resolve() : refresh("movements");
    if (tab === "Revisão") return fresh("revisão", "/v1/reviews") ? Promise.resolve() : refresh("reviews");
    if (tab === "Contas" || tab === "Compromissos" || tab === "Capturar" || tab === "Mais")
      return fresh("contas", "/v1/accounts") && fresh("saldos", "/v1/account-positions")
        ? Promise.resolve() : refresh("accounts");
    if (tab === "Cartões") return fresh("cartões", "/v1/card-catalog") ? Promise.resolve() : refresh("cards");
    return Promise.resolve();
  }, [lastLoaded, refresh, tab]);
  // The write response already acknowledges persistence. Aggregate screens can
  // refresh independently; LatestQueries keeps failures visible and rejects
  // responses belonging to an obsolete read generation.
  const refreshAfterMutation = useCallback((...scopes: Parameters<typeof refresh>[0][]) => {
    void Promise.all((scopes.length ? scopes : ["finance" as const]).map(scope => refresh(scope, true)))
      .catch(error => setRefreshErrors(current => ({...current, atualização: error.message || "Não foi possível atualizar os dados."})));
    return Promise.resolve();
  }, [refresh]);
  useEffect(() => {
    visibleRefreshRef.current = refreshVisible;
  }, [refreshVisible]);
  const retryNow = useCallback(() => {
    retryController.reset();
    setRefreshErrors({});
    setSyncError("");
    return Promise.all([requests.retryFailed(), refreshVisible(true), sync()]);
  }, [refreshVisible, retryController, sync, requests]);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (sessionReady && foreground) {
        const force = handledSyncRevision.current !== syncRevision;
        handledSyncRevision.current = syncRevision;
        void refreshVisible(force);
      }
    }, 0);
    return () => clearTimeout(timer);
  }, [sessionReady, foreground, refreshVisible, syncRevision]);
  useEffect(() => {
    if (!sessionReady || !foreground) return;
    void sync();
    const timer = setInterval(() => {
      void requests.retryFailed();
      void visibleRefreshRef.current(availability==='reconnecting');
      return sync();
    }, availability==='reconnecting'||connectionFailed ? 5000 : 120000);
    return () => clearInterval(timer);
  }, [sessionReady, foreground, sync,requests,availability,connectionFailed]);
  useEffect(() => {
    if (!foreground) requests.cancelAll();
    return () => requests.cancelAll();
  }, [foreground, requests]);
  useEffect(() => () => {
    retryController.clear();
  }, [retryController]);

  // Capture is acknowledged by durable local storage. Counting and transport
  // cannot unsave it or keep this action waiting on other queued files.
  const updateAfterLocalCapture = (valid: () => boolean) => {
    const version = ++queueUiVersion.current;
    void queue().then(rows => {
      if (valid() && version === queueUiVersion.current) setQueueCount(rows.length);
    }).catch(() => {
      if (valid() && version === queueUiVersion.current)
        setCaptureMessage(current => `${current} A contagem local será atualizada quando possível.`);
    });
    void sync().catch(error => {if (valid()) setSyncError(error.message || 'A sincronização será retomada.');});
  };
  const enqueueEvent = async (payload: any) => {
    const valid = captureScope();
    const event = await manual.prepare({
      event_id: newId(),
      source_type: "manual",
      source_provider: "mobile_manual",
      captured_at: new Date().toISOString(),
      raw_payload: { entry_method: voiceTranscript ? "voice_reviewed" : "mobile", ...(voiceTranscript ? { transcript: voiceTranscript } : {}) },
      parsed_payload: payload,
    });
    captureMessageId.current=event.event_id;
    await db.runAsync(
      "INSERT OR IGNORE INTO event_queue(event_id,kind,payload,created_at) VALUES(?,?,?,?)",
      event.event_id,
      "event",
      JSON.stringify(event),
      new Date().toISOString(),
    );
    if (!valid()) return;
    let cleanupFailed=false;
    try {await manual.clear();} catch {cleanupFailed=true;}
    if (!valid()) return;
    setCaptureSavedRevision(value => value + 1);
    setCaptureMessage(
      (online ? "Movimentação salva no aparelho. Enviando para revisão." : "Movimentação salva no aparelho. Será enviada quando a conexão voltar.") + (cleanupFailed ? ' O rascunho continua preservado; uma nova tentativa usa a mesma identificação.' : ''),
    );
    updateAfterLocalCapture(valid);
  };
  const runCaptureTask = async (task: () => Promise<void>) => {
    const valid = captureScope();
    if (!valid() || captureSavingLock.current) return;
    captureSavingLock.current = true;
    setCaptureSaving(true);setCaptureError("");setCaptureMessage("");
    try { await task(); }
    catch (error: any) { if (!valid() || error.name === 'AbortError') return;const message = error.message || "Não foi possível salvar. Seus campos continuam preenchidos para tentar novamente.";setCaptureError(message);if (tab !== "Capturar") Alert.alert("Não foi possível retomar o arquivo", message); }
    finally { captureSavingLock.current = false;if (valid()) setCaptureSaving(false); }
  };
  const retryPdfCaptures = async () => {
    const valid = captureScope();if (!valid()) return;
    const waiting = [...await queue(), ...await quarantinedQueue()];
    for (const row of waiting) {
      if (isPdfCapture(row) && /CPF|senha|protected|Could not read document/i.test(row.last_error || ''))
        await clearQueueFailure(db, row);
    }
    setDeadLetters(await quarantinedQueue());setQueueCount((await queue()).length);
    return sync();
  };
  const retryLocalItem = async (row: QueueRow, asStatement = false) => {
    const valid = captureScope();if (!valid()) return;
    if ((await reconcileLocalFiles(true)).includes(row.event_id)) return;
    if (!valid()) return;
    const current = (await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id')).find(item => item.event_id === row.event_id);
    if (!current) return;
    if (!sameQueueRow(current, row)) throw new Error('O item local mudou. Atualize a lista antes de tentar novamente.');
    if (asStatement) {
      await requeueAsStatement(db, row);
      captureMessageId.current = row.event_id;
      setCaptureMessage("Arquivo preservado. A extração preparará as linhas para revisão em Foto / arquivo.");
      setTab("Capturar");
    } else await clearQueueFailure(db, row);
    setDeadLetters(await quarantinedQueue());
    setQueueCount((await queue()).length);
    return sync();
  };
  const removeLocalItem = async (row: QueueRow) => {
    // The full row identity, rather than event_id alone, guards the delete.
    if (row.kind === 'native_event') {
      const rows = await db.getAllAsync<QueueRow>('SELECT * FROM event_queue ORDER BY id');
      if (!sameQueueRow(rows.find(item => item.event_id === row.event_id), row))
        throw new Error('O item local mudou. Foi preservado para conferência.');
      // Native commit may throw. Preserve the SQLite row until the native
      // source has durably retired it, so it cannot be imported again.
      NotificationCapture.markSynced(row.event_id);
    }
    await finalizeQueueRow(db, row, () => {});
  };
  const refreshLocalQueue = async (valid: () => boolean) => {
    const [remaining, active] = await Promise.all([quarantinedQueue(), queue()]);
    if (!valid()) return;
    setDeadLetters(remaining);
    setQueueCount(active.length);
  };
  const discardLocalItems = (rows: QueueRow[]) => {
    const valid = captureScope();if (!valid() || !rows.length) return;
    const many = rows.length > 1;
    Alert.alert(many ? `Excluir ${rows.length} itens com falha?` : 'Excluir este item da fila?', 'Ele sai só da fila deste aparelho. Lançamentos já salvos no extrato não mudam.', [
      {text: 'Cancelar', style: 'cancel'}, {text: 'Excluir', style: 'destructive', onPress: async () => {
        if (!valid()) return;
        const problems: string[] = [];
        for (const row of rows) {
          try { await removeLocalItem(row); }
          catch (error: any) {
            problems.push(error?.message || 'Não foi possível excluir este item.');
            if (!many) throw error;
          }
        }
        await refreshLocalQueue(valid).catch(() => {
          if (valid()) Alert.alert('Item excluído', 'A exclusão foi salva. A lista local será atualizada quando possível.');
        });
        if (many && problems.length && valid()) Alert.alert('Não foi possível excluir tudo', problems[0]);
      }},
    ]);
  };
  const discardLocalItem = (row: QueueRow) => discardLocalItems([row]);
  const saveCapture = () => runCaptureTask(async () => {
    if (!manual.ready) throw new Error("Aguarde a recuperação do rascunho antes de salvar.");
    if (captureKind !== 'own_transfer' && captureAccount && !accountProfiles.some(item => item.key === captureAccount && selectableAccount(item)))
      throw new Error('Selecione uma conta aberta para esta movimentação.');
    await enqueueEvent(manualCapturePayload(manual.draft, key => accountLabel(accountProfiles.find(item => item.key === key),key)));
  });
  const applyMovementVoice = (draft:any, transcript:string) => {
    setCaptureError("");
    if(draft.amount!=null)setAmount(String(draft.amount).replace('.', ','));
    if(["purchase","income","own_transfer"].includes(draft.kind))setCaptureKind(draft.kind);
    if(draft.kind!=="own_transfer"&&draft.name!=null)setMerchant(draft.name);
    if(draft.event_date!=null)setCaptureDate(draft.event_date);
    if(draft.installment_count!=null&&Number(draft.installment_count)>=2)manual.change('installments',String(draft.installment_count));
    setVoiceTranscript(transcript);
    setCaptureMessage([draft.ambiguity, draft.kind==="own_transfer"?`Escolha as contas de origem (${draft.origin_hint||"a confirmar"}) e destino (${draft.destination_hint||"a confirmar"}).`:null,"Confira os dados antes de salvar."].filter(Boolean).join(" "));
    if(draft.kind==="own_transfer"){setTransferOrigin("");setTransferDestination("");}
  };
  const saveVoiceEvidence = async (_draft:any, transcript:string, command?:VoiceEvidenceCommand) => {
    const valid = captureScope();if (!valid()) throw Object.assign(new Error('A captura foi interrompida. A evidência permanece preparada.'), {name:'AbortError'});
    if (!command || command.version !== 1 || command.text !== transcript.trim()) throw new Error('Prepare a identidade da evidência antes de enviá-la.');
    const fileId=command.event_id;
    await enqueueVoiceEvidence(db,command);
    if (!valid()) return;
    captureMessageId.current=fileId;
    setCaptureMessage("Texto da voz salvo como evidência. Revise os dados após a extração.");
    setCaptureError("");
    updateAfterLocalCapture(valid);
  };
  const pickDocument = async (kind: CaptureFileKind, format?: CaptureFileFormat) => {
    const valid = captureScope();if (!valid()) return;
    const result = await withDevicePrompt(() => DocumentPicker.getDocumentAsync(capturePickerOptions(kind, format, Platform.OS)));
    if (result.canceled) return;
    const f = result.assets[0];
    if (f.size != null && f.size > captureFileLimit(kind))
      throw new Error(`Escolha um arquivo de até ${captureFileLimit(kind) / (1024 * 1024)} MiB.`);
    const fileId=newId();
    let durableUri:string;
    try {durableUri=await persistQueuedFile(f.uri,fileId,captureFileLimit(kind))} catch(error:any){if(valid()) Alert.alert("Arquivo não salvo",error.message);return}
    await db.runAsync(
      "INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)",
      fileId,
      kind === "document" ? "document" : "batch",
      "{}",
      durableUri,
      f.name,
      captureMimeType(f.name, f.mimeType),
      new Date().toISOString(),
    );
    if (!valid()) return;
    captureMessageId.current=fileId;
    setCaptureMessage(
      kind === "document" ? "Arquivo salvo no dispositivo. Processamento em andamento." : "Fatura ou extrato salvo no aparelho. A extração preparará um lote para revisão quando houver conexão.",
    );
    updateAfterLocalCapture(valid);
  };
  const capturePhoto = async () => {
    const valid = captureScope();if (!valid()) return;
    const permission = await withDevicePrompt(() => ImagePicker.requestCameraPermissionsAsync());
    if (!valid()) return;
    if (!permission.granted) {
      Alert.alert(
        "Permissão necessária",
        "Ative a câmera para fotografar o comprovante.",
      );
      return;
    }
    const result = await withDevicePrompt(() => ImagePicker.launchCameraAsync({ quality: 0.8 }));
    if (result.canceled) return;
    const f = result.assets[0];
    const fileId=newId();
    let durableUri:string;
    try {durableUri=await persistQueuedFile(f.uri,fileId)} catch(error:any){if(valid()) Alert.alert("Arquivo não salvo",error.message);return}
    await db.runAsync(
      "INSERT OR IGNORE INTO event_queue(event_id,kind,payload,local_uri,filename,mime_type,created_at) VALUES(?,?,?,?,?,?,?)",
      fileId,
      "document",
      "{}",
      durableUri,
      `capture-${Date.now()}.jpg`,
      "image/jpeg",
      new Date().toISOString(),
    );
    if (!valid()) return;
    captureMessageId.current=fileId;
    setCaptureMessage(
      "Arquivo salvo no dispositivo. Processamento em andamento.",
    );
    updateAfterLocalCapture(valid);
  };
  const decide = async (id: string, action: string) => {
    const valid = captureScope();if (!valid()) return;
    let saved = false;
    try {
      await json(`/v1/reviews/${id}/decision`, {
        method: "POST",
        body: JSON.stringify(reviewDecisionBody(reviews.find(row=>row.review_id===id),action,!!reviewAcknowledgements[id])),
      });
      saved = true;
      if (!valid()) return;
      setReviews(current => current.filter(row => row.review_id !== id));
      setReviewAcknowledgements(current => {const next = {...current}; delete next[id]; return next;});
      Alert.alert("Decisão salva", "A decisão foi registrada.");
      await refreshAfterMutation("finance", "reviews");
    } catch (e: any) {
      if (!valid() || e.name === 'AbortError') return;
      Alert.alert(saved ? "Decisão salva" : "Não foi possível salvar", saved ? `A decisão foi registrada. A atualização da tela falhou: ${e.message}` : e.message);
    }
  };
  const loadMoreReviews = async () => {
    if (reviewMoreBusy || !reviewPage.next_cursor) return;
    setReviewMoreBusy(true);
    const path = `/v1/reviews?cursor=${encodeURIComponent(reviewPage.next_cursor)}`;
    try {
      await requests.run("revisão-página", `${path}:${api.getReadGeneration()}`, signal => json(path, {signal}), result => {
        setReviews(current => Array.from(new Map([...current, ...result.items].map(item => [item.review_id, item])).values()));
        setReviewPage({total: result.total, next_cursor: result.next_cursor});
      });
    } finally { setReviewMoreBusy(false); }
  };
  const openTransaction = async (item: any) => {
    requests.cancel("detalhe");
    setDetailLoading(false);
    setSelectedItem(item);
    setEditingItem(false);
    setTab("Detalhe");
    detailId.current = item.id || item.provisional_id || "";
    if (!item.id || item.provisional_id) return;
    setDetailLoading(true);
    await requests.run(
      "detalhe",
      `${item.id}:${api.getReadGeneration()}`,
      (signal) => json(`/v1/transactions/${item.id}`, { signal }),
      setSelectedItem,
    );
    if (detailId.current === item.id) setDetailLoading(false);
  };
  const transactionSaved = async (updated: any) => {
    setSelectedItem((current: any) => ({ ...current, ...updated }));
    setItems((current) =>
      current.map((value) =>
        value.id === updated.id ? { ...value, ...updated } : value,
      ),
    );
    setEditingItem(false);
    // result.item includes the current source token; another detail GET is
    // redundant and used to delay confirmation after the write completed.
    await refreshAfterMutation("finance", "reviews");
  };
  const movementDeleted = async () => {
    requests.cancel("detalhe");
    requests.cancel("movimentações");
    requests.cancel("movimentações-página");
    setItems(current => current.filter(item => selectedItem?.provisional_id
      ? item.provisional_id !== selectedItem.provisional_id
      : item.id !== selectedItem?.id));
    setMovementTotal(current => Math.max(0, current - 1));
    setSelectedItem(null);
    setEditingItem(false);
    setTab("Movimentações");
    setDeletionNotice("Registro excluído. Histórico preservado.");
    await refreshAfterMutation("finance", "reviews", "accounts", "cards");
  };
  const searchMovements = async (clear = false) => {
    const value = clear ? "" : movementSearch.trim();
    if (clear) {
      setMovementSearch("");
      movementCategory.current = "";
      movementSpendingOnly.current = false;
      movementAccount.current = "";
      setMovementAccountId("");
      movementDateRange.current = null;
      setMovementFilterLabel("");
    }
    movementQuery.current = value;
    setHasMovementFilter(Boolean(value || movementAccount.current));
    await refresh("movements", true);
  };
  const loadMoreMovements = async () => {
    if (movementLoadingMore || !movementHasMore || !movementCursor.current) return;
    setMovementLoadingMore(true);
    const path = movementPath({search:movementQuery.current,category:movementCategory.current,
      account:movementAccount.current,range:movementDateRange.current,spendingOnly:movementSpendingOnly.current,
      cursor:movementCursor.current});
    try {
      await requests.run(
        "movimentações-página",
        `${path}:${api.getReadGeneration()}`,
        (signal) => json(path, { signal }),
        (result) => {
          setItems((current) => {
            const known = new Set(
              current.map((item) => item.id || item.provisional_id),
            );
            return [
              ...current,
              ...(result.items || []).filter(
                (item: any) => !known.has(item.id || item.provisional_id),
              ),
            ];
          });
          setMovementTotal(result.total || 0);
          setMovementHasMore(Boolean(result.has_more));
          movementCursor.current = result.next_cursor || null;
        },
      );
    } finally {
      setMovementLoadingMore(false);
    }
  };
  const selectDashboardPeriod = async (period: DashboardPeriod) => {
    dashboardPeriod.current = period;
    setDashboardPeriodState(period);
    await refresh("dashboard-period", true);
  };
  const openCategory = async (category: string) => {
    movementQuery.current = "";
    setMovementSearch("");
    setHasMovementFilter(false);
    movementAccount.current = "";
    setMovementAccountId("");
    movementCategory.current = category;
    movementSpendingOnly.current = true;
    movementDateRange.current =
      monthDashboard?.period_start && monthDashboard?.period_end
        ? { from: monthDashboard.period_start, to: monthDashboard.period_end }
        : null;
    setMovementFilterLabel(
      `Gastos · ${category} · competência de ${monthDashboard?.period_start ? new Date(monthDashboard.period_start + "T12:00:00").toLocaleDateString("pt-BR") : "início"} a ${monthDashboard?.period_end ? new Date(monthDashboard.period_end + "T12:00:00").toLocaleDateString("pt-BR") : "agora"}`,
    );
    setTab("Movimentações");
    await refresh("movements", true);
  };
  const selectMovementAccount = async (accountId: string) => {
    movementAccount.current = accountId;
    setMovementAccountId(accountId);
    setHasMovementFilter(Boolean(movementQuery.current.trim() || accountId));
    await refresh("movements", true);
  };
  const monthWindow = currentMonthWindow();
  const monthLabel = new Date(
    monthWindow.year,
    monthWindow.month - 1,
    1,
  ).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
  const displayMonth =
    monthLabel.charAt(0).toLocaleUpperCase("pt-BR") + monthLabel.slice(1);
  const movementResultMatches = movementResultPath === requestedMovementPath;
  const visibleMovementItems = movementResultMatches ? items : [];
  const movementHeader = (
    <>
      {!!deletionNotice && <Text accessibilityRole="alert" style={s.muted}>{deletionNotice}</Text>}
      <View style={s.searchCard}>
        <TextInput
          accessibilityLabel="Buscar vendedor, descrição ou categoria"
          value={movementSearch}
          onChangeText={setMovementSearch}
          onSubmitEditing={() => searchMovements()}
          returnKeyType="search"
          placeholder="Buscar vendedor, descrição ou categoria"
          placeholderTextColor="#92A4A2"
          style={s.searchInput}
        />
        <View style={s.searchActions}>
          <Pressable accessibilityRole="button"
            onPress={() => searchMovements()}
            style={s.searchPrimary}
          >
            <Text style={s.searchPrimaryText}>Buscar</Text>
          </Pressable>
          {!!(hasMovementFilter || movementFilterLabel) && (
            <Pressable accessibilityRole="button"
              onPress={() => searchMovements(true)}
              style={s.searchClear}
            >
              <Text style={s.searchClearText}>Limpar</Text>
            </Pressable>
          )}
        </View>
      </View>
      <View style={s.accountFilterBox}>
        <Text style={s.filterLabel}>FILTRAR POR CONTA, BANCO OU CARTÃO</Text>
        <ScrollView horizontal keyboardShouldPersistTaps="handled" showsHorizontalScrollIndicator={false} contentContainerStyle={s.periodFilters}>
          <Pressable accessibilityRole="button"
            onPress={() => selectMovementAccount("")}
            style={[s.periodChip, !movementAccountId && s.periodChipActive]}
          >
            <Text style={[s.periodChipText, !movementAccountId && s.periodChipTextActive]}>Todos</Text>
          </Pressable>
          {movementAccountOptions.map((option) => (
            <Pressable accessibilityRole="button"
              key={option.id}
              onPress={() => selectMovementAccount(option.id)}
              style={[s.periodChip, movementAccountId === option.id && s.periodChipActive]}
            >
              <Text style={[s.periodChipText, movementAccountId === option.id && s.periodChipTextActive]}>
                {option.label}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
        {!movementAccountOptions.length && <Text style={s.muted}>Nenhuma conta ou cartão cadastrado para filtrar.</Text>}
      </View>
      {!!(movementFilterLabel || movementAccountId) && (
        <View style={s.activeFilter}>
          <View style={{ flex: 1 }}>
            <Text style={s.activeFilterLabel}>{movementFilterLabel ? "FILTROS ATIVOS" : "FILTRO DO EXTRATO"}</Text>
            <Text style={s.activeFilterText}>
              {[movementAccountOptions.find((option) => option.id === movementAccountId)?.label, movementFilterLabel]
                .filter(Boolean)
                .join(" · ")}
            </Text>
          </View>
          <Pressable accessibilityRole="button" onPress={() => searchMovements(true)}>
            <Text style={s.activeFilterClear}>Remover</Text>
          </Pressable>
        </View>
      )}
      <View style={s.syncRow}>
        <Text style={s.muted}>
          {movementResultMatches ? `${items.length} de ${movementTotal} · sincronizado ` : 'Atualizando filtro · última sincronização '}
          {lastSyncedAt
            ? lastSyncedAt.toLocaleTimeString("pt-BR", {
                hour: "2-digit",
                minute: "2-digit",
              })
            : "—"}
        </Text>
        <Pressable accessibilityRole="button"
          onPress={() => refresh("movements", true)}
          style={s.syncButton}
        >
          <Text style={s.syncButtonText}>Atualizar</Text>
        </Pressable>
      </View>
    </>
  );
  const movementRow = ({ item: x }: { item: any }) => (
    <Pressable accessibilityRole="button" onPress={() => openTransaction(x)} style={[s.listRow,{flexWrap:"wrap"},compact&&{flexDirection:"column",alignItems:"stretch"}]}>
      <View style={{ flex: 1, minWidth: 120, maxWidth:"100%" }}>
        <Text style={s.body}>
          {ownAccountTransfer(x)?`Transferência ${accountLabel(accountProfiles.find((item:any)=>item.key===x.transfer?.from_account_key), "?")} → ${accountLabel(accountProfiles.find((item:any)=>item.key===x.transfer?.to_account_key), "?")}`:x.vendedor ||
            x.fornecedor ||
            x.merchant_name ||
            x.estabelecimento_original ||
            x.merchant_raw ||
            x.descricao_original ||
            "Movimentação"}
        </Text>
        {(ownAccountTransfer(x) || !!(x.vendedor || x.fornecedor || x.merchant_name)) && (
          <Text style={s.originalText}>
            {x.estabelecimento_original ||
              x.merchant_raw ||
              x.descricao_original}
          </Text>
        )}
        <Text style={s.muted}>{classificationLine(x)}</Text>
        <Text style={s.muted}>
          {[ledgerAccountLabel(x), financialDateLabel(String(x.data_evento || ""))].filter(Boolean).join(" · ")}
          {x.commitment ? ` · ${x.commitment.name}` : ""}
          {parcelamentoListMark(x) ? ` · ${parcelamentoListMark(x)}` : ""}
          {x.installment_plan ? ` · parcela ${x.installment_plan.current_number}/${x.installment_plan.total}` : ""}
        </Text>
      </View>
      <SingleLineValue
        style={[
          s.body,
          x.valor < 0 || x.valor?.$numberDecimal?.startsWith("-")
            ? s.bad
            : s.good,
          compact&&{alignSelf:"flex-end"},
        ]}
      >
        {money(
          typeof x.valor === "number"
            ? x.valor
            : Number(x.valor?.$numberDecimal || 0),
        )}
      </SingleLineValue>
    </Pressable>
  );
  const reviewHeader = (
    <>
      <ProvisionalReview api={json} money={money} onUpdated={() => refreshAfterMutation("finance", "reviews")} />
      <Text style={s.section}>Pendências automáticas</Text>
      {reviews.length === 0 && (
        <Text style={s.muted}>Nenhuma pendência automática para revisar.</Text>
      )}
    </>
  );
  const reviewRow = ({ item: r }: { item: any }) => (
    <View style={s.reviewCard}>
      <Text style={s.body}>
        {r.reason === "item_total_mismatch"
          ? "A soma dos itens diverge do total"
          : r.reason === "event_needs_parse" ? "Captura precisa de dados para revisão"
          : r.reason === "parcelamento_needs_card" ? "Compra parcelada sem cartão reconhecido"
          : "Possível correspondência"}
      </Text>
      <Text style={s.muted}>
        Confiança {r.match_score ? `${Math.round(r.match_score * 100)}%` : "—"}
        {r.difference ? ` · Diferença ${money(r.difference)}` : ""}
      </Text>
      {r.source&&<Text style={s.body}>Captura: {money(Number(r.source.valor))} · {financialDateLabel(r.source.data_evento)} · {r.source.merchant_raw||"Origem"}</Text>}
      {r.source_event&&<Text selectable style={s.body}>{r.source_event.raw_payload?.text||r.source_event.raw_payload?.body||r.source_event.raw_payload?.transcript||r.source_event.raw_payload?.raw_text||JSON.stringify(r.source_event.raw_payload||{})}</Text>}
      {r.reason==="event_needs_parse"&&<><Text style={s.warning}>A extração não forneceu dados suficientes. Confira a fonte e registre uma movimentação pela captura manual se necessário.</Text><Button title="Registrar dados manualmente" secondary onPress={()=>setTab("Capturar")}/><Button title="Descartar pendência e preservar original" secondary onPress={()=>decide(r.review_id,"discard")}/></>}
      {r.reason==="parcelamento_needs_card"&&<Text style={s.warning}>A primeira parcela entrou no extrato na data da compra. Abra Compromissos e cadastre o parcelamento no cartão. A conciliação segue o vencimento da fatura.</Text>}
      {r.candidate&&<Text style={s.body}>Candidato: {money(Number(r.candidate.valor))} · {financialDateLabel(r.candidate.data_evento)} · {r.candidate.descricao_original||r.candidate.merchant_raw||"Movimentação"}</Text>}
      {reviewHasDifferences(r)&&<Pressable accessibilityRole="checkbox" accessibilityState={{checked:!!reviewAcknowledgements[r.review_id]}} aria-checked={!!reviewAcknowledgements[r.review_id]} onPress={()=>setReviewAcknowledgements(current=>({...current,[r.review_id]:!current[r.review_id]}))}><Text style={s.warning}>{reviewAcknowledgements[r.review_id]?"✓ ":"□ "}Conferi as diferenças de valor, data e identidade entre estas fontes e quero vinculá-las.</Text></Pressable>}
      {!!(r.candidate_transaction_id || r.candidate_provisional_id) && (
        <Button
          title="Confirmar correspondência"
          disabled={reviewHasDifferences(r)&&!reviewAcknowledgements[r.review_id]}
          onPress={() => decide(r.review_id, "confirm_match")}
        />
      )}
      {r.reason!=="event_needs_parse"&&<Button
        title="Manter separada"
        secondary
        onPress={() => decide(r.review_id, "keep_separate")}
      />}
    </View>
  );
  const showInitialLoading = loading && (
    (tab === "Início" && !loadedResources.início) ||
    (tab === "Movimentações" && !loadedResources.movimentações) ||
    (tab === "Revisão" && !loadedResources.revisão) ||
    ((tab === "Contas" || tab === "Compromissos") &&
      (!loadedResources.contas || !loadedResources.saldos)) ||
    (tab === "Cartões" && !loadedResources.cartões)
  );
  return (
    <LocalBackScope value={activeRoute}><SafeAreaView edges={["top"]} style={s.root}>
      <PendingMutations client={api} onUpdated={()=>refreshAfterMutation("all")} revision={syncRevision+api.getReadGeneration()} enabled={sessionReady&&foreground}/>
      <View style={s.header}>
        <View>
          <Text style={s.brand}>FINANCEIRO</Text>
          <Text style={s.title}>
            {tab === "Movimentações" ? "Extrato" : tab}
          </Text>
        </View>
        <View style={s.connection}>
          <View
            style={[s.dot, { backgroundColor: online&&!connectionFailed ? "#44D7A8" : "#F4B942" }]}
          />
          <Text style={s.connectionText}>
            {online&&!connectionFailed ? "Conectado" : availability==='checking' ? "Conectando" : "Reconectando"}
            {queueCount ? ` · ${queueCount} na fila` : ""}
          </Text>
        </View>
      </View>
      {(manualLoading || showInitialLoading) && (
        <View style={s.progress}>
          <ActivityIndicator color="#4FD1B5" />
          <Text style={s.muted}>Atualizando dados</Text>
        </View>
      )}
      {Object.entries(refreshErrors).map(([resource, error]) => (
        <View key={resource} accessibilityRole="alert" style={s.warningCard}>
          <Text style={s.warning}>Falha ao atualizar {resource}: {error}</Text>
          <Pressable accessibilityRole="button" onPress={retryNow} style={s.retryButton}>
            <Text style={s.retryText}>Tentar novamente</Text>
          </Pressable>
        </View>
      ))}
      {!!syncError && (
        <View accessibilityRole="alert" style={s.warningCard}>
          <Text style={s.warning}>Fila: {syncError}</Text>
          <Text style={s.warningHint}>O conteúdo continua salvo no aparelho e será reenviado quando a conexão voltar.</Text>
          <Pressable accessibilityRole="button" onPress={retryNow} style={s.retryButton}>
            <Text style={s.retryText}>Tentar novamente</Text>
          </Pressable>
        </View>
      )}
      {tab === "Início" &&
        dashboard &&
        Number(dashboard?.provisional_count || 0) > 0 && (
          <Pressable accessibilityRole="button"
            onPress={() => setTab("Revisão")}
            style={{
              marginHorizontal: 18,
              marginTop: 10,
              marginBottom: 2,
              backgroundColor: "#183034",
              borderWidth: 1,
              borderColor: "#4FD1B5",
              borderRadius: 14,
              padding: 13,
              flexDirection: "row",
              alignItems: "center",
              gap: 12,
            }}
          >
            <View style={{ flex: 1 }}>
              <Text
                style={{ color: "#F4F7F5", fontSize: 15, fontWeight: "800" }}
              >
                Validar {dashboard.provisional_count} captura
                {dashboard.provisional_count === 1 ? "" : "s"}
              </Text>
              <Text
                style={{
                  color: "#9EB2AF",
                  fontSize: 11,
                  lineHeight: 16,
                  marginTop: 3,
                }}
              >
                Revise o vendedor ou origem, valor e classificação antes de
                confirmar.
              </Text>
            </View>
            <Text style={{ color: "#4FD1B5", fontSize: 14, fontWeight: "800" }}>
              Abrir ›
            </Text>
          </Pressable>
        )}
      {tab === "Movimentações" ? (
        <FlatList
          key="movements"
          data={visibleMovementItems}
          renderItem={movementRow}
          keyExtractor={(item, index) =>
            String(item.id || item.provisional_id || index)
          }
          ListHeaderComponent={movementHeader}
          ListEmptyComponent={
            <Text style={s.muted}>{movementResultMatches ? 'Nenhuma movimentação encontrada.' : 'Aguardando a consulta deste filtro.'}</Text>
          }
          ListFooterComponent={
            movementLoadingMore ? (
              <ActivityIndicator color="#4FD1B5" />
            ) : movementResultMatches && movementHasMore ? (
              <Button
                title="Carregar mais"
                secondary
                onPress={() => loadMoreMovements()}
              />
            ) : visibleMovementItems.length ? (
              <Text style={s.foot}>
                Todo o histórico filtrado foi carregado.
              </Text>
            ) : null
          }
          contentContainerStyle={[s.content, s.contentShell]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          initialNumToRender={12}
          maxToRenderPerBatch={12}
          windowSize={7}
        />
      ) : tab === "Revisão" ? (
        <FlatList
          key="review"
          data={reviews}
          renderItem={reviewRow}
          keyExtractor={(item, index) => String(item.review_id || index)}
          ListHeaderComponent={reviewHeader}
          ListFooterComponent={<>{reviewPage.next_cursor?<Pressable accessibilityRole="button" disabled={reviewMoreBusy} onPress={()=>loadMoreReviews()}><Text style={{color:"#4FD1B5",padding:18}}>{reviewMoreBusy?"Carregando…":`Carregar mais revisões (${reviews.length} de ${reviewPage.total})`}</Text></Pressable>:null}<RecordReview api={json} money={money} onUpdated={() => refreshAfterMutation("finance", "reviews")}/></>}
          contentContainerStyle={[s.content, s.contentShell]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          initialNumToRender={8}
          maxToRenderPerBatch={8}
          windowSize={5}
        />
      ) : (
        <ScrollView
          key={tab}
          ref={node => {contentScroll.current = node; if (tab === "Compromissos") bindChartScroller(node);}}
          onScroll={event => {if (tab === "Compromissos") recordChartOffset(event.nativeEvent.contentOffset.y);}}
          scrollEventThrottle={16}
          style={{ flex: 1 }}
          contentContainerStyle={[s.content, s.contentShell]}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          {tab === "Início" && !dashboard && (
            <Text style={s.muted}>Carregando o resumo financeiro…</Text>
          )}
          {tab === "Início" && dashboard && (
            <>
              <Text style={s.section}>Visão econômica</Text>
              <View style={s.metricGrid}>
                <Metric
                  label="Impostos e tributos"
                  value={dashboardMoney(dashboard?.tax_total || 0)}
                />
                <Metric
                  label="Custos financeiros"
                  value={dashboardMoney(dashboard?.financial_cost_total || 0)}
                />
              </View>
            </>
          )}
          {tab === "Início" && dashboard && (
            <>
              <Text style={s.filterLabel}>PERÍODO</Text>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                style={s.periodScroll}
                contentContainerStyle={s.periodFilters}
              >
                {DASHBOARD_PERIODS.map((option) => (
                  <Pressable accessibilityRole="button"
                    key={option.key}
                    onPress={() => selectDashboardPeriod(option.key)}
                    style={[
                      s.periodChip,
                      dashboardPeriodState === option.key && s.periodChipActive,
                    ]}
                  >
                    <Text
                      style={[
                        s.periodChipText,
                        dashboardPeriodState === option.key &&
                          s.periodChipTextActive,
                      ]}
                    >
                      {option.label}
                    </Text>
                  </Pressable>
                ))}
              </ScrollView>
              <Text style={s.periodCaption}>
                {dashboard?.period_start
                  ? new Date(
                      dashboard.period_start + "T12:00:00",
                    ).toLocaleDateString("pt-BR") +
                    " a " +
                    new Date(
                      dashboard.period_end + "T12:00:00",
                    ).toLocaleDateString("pt-BR")
                  : "Histórico completo"}{" "}
                · {dashboard?.transaction_count || 0} movimentações
              </Text>
              <Text style={s.eyebrow}>POSIÇÃO DO PERÍODO</Text>
              <View style={s.hero}>
                <Text style={s.label}>Fluxo líquido</Text>
                <SingleLineValue style={s.heroValue}>
                  {dashboardMoney(dashboard?.net_flow || 0)}
                </SingleLineValue>
                <View style={s.row}>
                  <SingleLineValue style={[s.good, { flex: 1 }]}>
                    {`Entradas ${dashboardMoney(dashboard?.income || 0)}`}
                  </SingleLineValue>
                  <SingleLineValue style={[s.bad, { flex: 1, textAlign: "right" }]}>
                    {`Saídas ${dashboardMoney(dashboard?.expenses || 0)}`}
                  </SingleLineValue>
                </View>
              </View>
              <ComparisonCard data={dashboard?.comparison} money={dashboardMoney} />
              <View style={s.metricGrid}>
                <Metric
                  label="Revisões"
                  value={String(dashboard?.pending_reviews ?? "—")}
                />
                <Metric
                  label="Provisórias"
                  value={String(dashboard?.provisional_count ?? "—")}
                />
                <Metric
                  label="Obrigações vencidas/parciais"
                  value={dashboardMoney(dashboard?.overdue_or_partial_obligations || 0)}
                />
                <Metric
                  label="Financiamento futuro"
                  value={dashboardMoney(dashboard?.future_financing_principal || 0)}
                />
                <Metric
                  label="Parcelas futuras"
                  value={
                    String(dashboard?.projected_installment_count ?? 0) +
                    " · " +
                    dashboardMoney(dashboard?.projected_installment_total || 0)
                  }
                />
                <Metric
                  label="Investimentos"
                  value={
                    dashboard?.investment_positions_available
                      ? String(dashboard.investment_position_count) +
                        " posições"
                      : "Dados não disponíveis"
                  }
                />
              </View>
              <View style={s.recurringCard}>
                <View style={s.recurringDescription}>
                  <Text style={s.label}>Compromissos recorrentes mensais</Text>
                  <Text style={s.muted}>
                    {dashboard?.monthly_recurring_count || 0} lançamento(s) ·
                    referência {dashboard?.recurring_reference_month || "—"}
                  </Text>
                </View>
                <SingleLineValue style={s.recurringValue}>
                  {dashboardMoney(dashboard?.monthly_recurring_total || 0)}
                </SingleLineValue>
              </View>
              <Pressable accessibilityRole="button"
                onPress={() => setTab("Compromissos")}
                style={s.recurringCard}
              >
                <View style={s.recurringDescription}>
                  <Text style={s.label}>Próximos 30 dias · compromissos</Text>
                  <Text style={s.muted}>
                    {dashboard?.commitment_due_count || 0} cobrança(s)
                    prevista(s)
                  </Text>
                </View>
                <SingleLineValue style={s.recurringValue}>
                  {dashboardMoney(dashboard?.commitment_due_total || 0)}
                </SingleLineValue>
              </Pressable>
              <Text style={s.section}>Fluxo mensal</Text>
              <Text style={s.sectionCaption}>
                Janeiro a {monthLabel} · entradas e saídas por mês
              </Text>
              <CashflowChart
                data={monthsSinceJanuary(
                  yearDashboard?.monthly_series || [],
                  monthWindow.year,
                  monthWindow.month,
                )}
              />
              <Text style={s.section}>Gastos por categoria</Text>
              <Text style={s.sectionCaption}>Mês vigente: {displayMonth}</Text>
              {(monthDashboard?.category_spending || [])
                .map((x: any) => (
                  <CategoryBar
                    key={x.category}
                    item={x}
                    max={monthDashboard?.category_spending?.[0]?.amount || 1}
                    money={dashboardMoney}
                    onPress={() => openCategory(x.category)}
                  />
                ))}
              <Text style={s.foot}>
                Registros até{" "}
                {dashboard?.data_updated_until
                  ? new Date(dashboard.data_updated_until).toLocaleString(
                      "pt-BR",
                    )
                  : "—"}{" "}
                · extrato até{" "}
                {dashboard?.legacy_data_updated_until
                  ? new Date(
                      dashboard.legacy_data_updated_until,
                    ).toLocaleDateString("pt-BR")
                  : "—"}{" "}
                · capturas até{" "}
                {dashboard?.confirmed_capture_updated_until
                  ? new Date(
                      dashboard.confirmed_capture_updated_until,
                    ).toLocaleDateString("pt-BR")
                  : "—"}
              </Text>
              {dashboard?.legacy_data_is_stale && (
                <Text style={s.warning}>
                  O extrato está sem atualização há mais de 7 dias. As capturas
                  confirmadas aparecem nos totais, mas ainda precisam ser
                  conciliadas com o extrato.
                </Text>
              )}
            </>
          )}
          {tab === "Detalhe" &&
            selectedItem &&
            editingItem &&
            (selectedItem.provisional_id ? (
              <ProvisionalReview
                key={selectedItem.provisional_id}
                initialItem={selectedItem}
                api={json}
                money={money}
                onUpdated={() => refreshAfterMutation("finance", "reviews")}
                onExit={() => setEditingItem(false)}
              />
            ) : (
              <TransactionEditor
                key={selectedItem.id}
                item={selectedItem}
                api={json}
                money={money}
                onCancel={() => setEditingItem(false)}
                onSaved={transactionSaved}
              />
            ))}
          {tab === "Detalhe" && selectedItem && !editingItem && (
            <>
              {detailLoading && <ActivityIndicator color="#4FD1B5" />}
              <Text style={s.section}>
                {ownAccountTransfer(selectedItem)?"Transferência entre minhas contas":selectedItem.vendedor ||
                  selectedItem.fornecedor ||
                  selectedItem.merchant_name ||
                  selectedItem.estabelecimento_original ||
                  selectedItem.merchant_raw ||
                  selectedItem.descricao_original ||
                  "Movimentação"}
              </Text>
              <Text style={s.heroValue}>
                {money(
                  typeof selectedItem.valor === "number"
                    ? selectedItem.valor
                    : Number(selectedItem.valor?.$numberDecimal || 0),
                )}
              </Text>
              {ownAccountTransfer(selectedItem)&&<>
                <View style={s.listRow}><Text style={s.muted}>Conta de origem</Text><Text style={s.body}>{accountLabel(accountProfiles.find((item:any)=>item.key===selectedItem.transfer?.from_account_key), "A identificar")}</Text></View>
                <View style={s.listRow}><Text style={s.muted}>Conta de destino</Text><Text style={s.body}>{accountLabel(accountProfiles.find((item:any)=>item.key===selectedItem.transfer?.to_account_key), "A identificar")}</Text></View>
                <View style={s.listRow}><Text style={s.muted}>Conciliação</Text><Text style={s.body}>{selectedItem.transfer?.reconciliation_status?.replaceAll('_',' ')||"Pendente"}</Text></View>
              </>}
              <View style={s.detailField}>
                <Text style={s.muted}>Descrição original</Text>
                <Text selectable style={[s.body, s.descriptionValue]}>
                  {selectedItem.estabelecimento_original ||
                    selectedItem.merchant_raw ||
                    selectedItem.descricao_original ||
                    "—"}
                </Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Data</Text>
                <Text style={s.body}>
                  {financialDateLabel(String(selectedItem.data_evento || "")) || "—"}
                </Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Natureza</Text>
                <Text style={[s.body, s.detailValue]}>{natureLabel(selectedItem.natureza) || "—"}</Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Meio de pagamento</Text>
                <Text style={[s.body, s.detailValue]}>{paymentLabel(selectedItem.metodo_pagamento) || "—"}</Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Conta ou cartão</Text>
                <Text style={[s.body, s.detailValue]}>{statementInstrument(selectedItem, ledgerAccountLabel(selectedItem)) || "—"}</Text>
              </View>
              {!ownAccountTransfer(selectedItem)&&<>
                <View style={s.detailField}><Text style={s.muted}>Plataforma</Text><Text style={s.body}>{selectedItem.plataforma||selectedItem.plataforma_sugerida||"—"}</Text></View>
                <View style={s.detailField}><Text style={s.muted}>Vendedor</Text><Text style={s.body}>{selectedItem.vendedor||selectedItem.fornecedor||"Não identificado"}</Text></View>
                <View style={s.detailField}><Text style={s.muted}>Categoria</Text><Text style={s.body}>{recordCategory(selectedItem)}</Text></View>
              </>}
              <View style={s.listRow}>
                <Text style={s.muted}>Subcategoria</Text>
                <Text style={s.body}>{selectedItem.subcategoria || "—"}</Text>
              </View>
              {selectedItem.natureza==="TRANSFERENCIA" && !ownAccountTransfer(selectedItem) && (
                <View style={s.listRow}>
                  <Text style={s.muted}>Natureza</Text>
                  <Text style={[s.body, s.detailValue]}>Transferência entre minhas contas, sem origem e destino</Text>
                </View>
              )}
              <View style={s.listRow}>
                <Text style={s.muted}>Recorrência</Text>
                <Text style={[s.body, s.detailValue]}>
                  {selectedItem.recorrencia?.tipo
                    ? `${selectedItem.recorrencia.tipo.replaceAll("_", " ")} · ${String(selectedItem.recorrencia.periodicidade || "").replaceAll("_", " ")}`
                    : "Não classificada"}
                </Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Estado</Text>
                <Text style={s.body}>
                  {selectedItem.status ||
                    selectedItem.status_conciliacao ||
                    "confirmada"}
                </Text>
              </View>
              <View style={s.listRow}>
                <Text style={s.muted}>Origem</Text>
                <Text numberOfLines={2} style={[s.body, s.detailValue]}>
                  {selectedItem.source?.fonte ||
                    selectedItem.event_id ||
                    "Ledger legado"}
                </Text>
              </View>
              {selectedItem.commitment && (
                <View style={s.listRow}>
                  <Text style={s.muted}>Compromisso vinculado</Text>
                  <Text style={[s.body, s.detailValue]}>
                    {selectedItem.commitment.name}
                  </Text>
                </View>
              )}
              <ParcelamentoPurchaseDetail item={selectedItem} />
              {!editingItem && (
                <StatementCommitmentLink
                  item={selectedItem}
                  api={json}
                  money={money}
                  onLinked={(commitment) => setSelectedItem((current: any) => current ? {...current, commitment} : current)}
                  onUnlinked={() => {setSelectedItem((current: any) => current ? {...current, commitment: undefined} : current); void refreshAfterMutation("finance");}}
                />
              )}
              {selectedItem.installment_plan && (
                <View style={s.installmentReference} accessibilityRole="summary">
                  <Text style={s.muted}>Compra parcelada</Text>
                  <Text style={s.body}>
                    {selectedItem.installment_plan.name} · parcela {selectedItem.installment_plan.current_number}/{selectedItem.installment_plan.total}
                  </Text>
                  <Text style={s.muted}>
                    {selectedItem.installment_plan.future_count > 0
                      ? `${selectedItem.installment_plan.future_count} parcela(s) futura(s) · ${money(Number(selectedItem.installment_plan.future_total || 0))}`
                      : "Nenhuma parcela futura pendente"}
                  </Text>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Abrir compromissos e parcelamentos"
                    onPress={() => setTab("Compromissos")}
                  >
                    <Text style={s.syncButtonText}>Ver parcelamento e parcelas futuras ›</Text>
                  </Pressable>
                </View>
              )}
              {(selectedItem.evidence || []).map((e: any, i: number) => (
                <View key={e.evidence_id || i} style={s.listRow}>
                  <View>
                    <Text style={s.body}>
                      {e.source?.source_type || e.target_type || "Evidência"}
                    </Text>
                    <Text style={s.muted}>
                      {e.source?.source_provider ||
                        e.decision ||
                        "captura vinculada"}
                    </Text>
                  </View>
                  <Text style={s.body}>
                    {e.match_score ? `${Math.round(e.match_score * 100)}%` : ""}
                  </Text>
                </View>
              ))}
              {(selectedItem.source_records || []).map(
                (source: any, i: number) => (
                  <View key={source.id || i} style={s.listRow}>
                    <Text style={s.muted}>Registro de origem</Text>
                    <Text style={[s.body, s.detailValue]}>
                      {source.source_sheet || source.source_workbook} · linha{" "}
                      {source.source_row}
                    </Text>
                  </View>
                ),
              )}
              {!!(selectedItem.provisional_id || selectedItem.id) && (
                <Button
                  title="Editar e sincronizar"
                  onPress={() => setEditingItem(true)}
                />
              )}
              <Button
                title="Voltar às movimentações"
                secondary
                onPress={() => {
                  setEditingItem(false);
                  setTab("Movimentações");
                }}
              />
              {!detailLoading && !!(selectedItem.provisional_id || selectedItem.id) && <DeleteMovement
                key={selectedItem.provisional_id || selectedItem.id}
                item={selectedItem} api={json} onDeleted={movementDeleted}
              />}
            </>
          )}
          {tab === "Detalhe" && !selectedItem && (
            <View style={{ alignItems: "center", paddingVertical: 50 }}>
              <Text style={s.section}>Nenhuma movimentação selecionada</Text>
              <Text style={[s.muted, { textAlign: "center", maxWidth: 290 }]}>
                Abra Movimentações e toque em um registro para consultar seus
                dados, origem e evidências.
              </Text>
              <Button
                title="Abrir movimentações"
                onPress={() => setTab("Movimentações")}
              />
            </View>
          )}
          {tab === "Decisões" && <FinancialLab api={json} onBack={()=>setTab("Mais")}/>}
          {tab === "Fechamento" && <MonthClose api={json} money={money} onBack={()=>setTab("Mais")} onNavigate={setTab}/>}
          {tab === "Comprar" && <PurchaseCheck api={json} upload={api.upload} ready={sessionReady} sharedImage={initialPurchaseImage} sharedError={initialPurchaseError} onBack={()=>setTab("Mais")}/>}
          {tab === "Rascunhos" && <DraftManager onBack={() => setTab("Mais")} onPendingCreation={() => setTab("Compromissos")} />}
          {tab === "Mais" && (
            <>
              <Text style={s.section}>Organização financeira</Text>
              <MoreCard fullWidth title="Minha conta e privacidade" subtitle="Senha, digital e sessões" onPress={() => router.push('/account')} />
              <View style={s.moreGrid}>
                <MoreCard title="Rascunhos" subtitle="Revisar e excluir edições salvas no aparelho" onPress={() => setTab("Rascunhos")} />
                <MoreCard title="Cabe na minha vida?" subtitle="Veja o impacto antes de comprar" onPress={()=>setTab("Comprar")}/>
                <MoreCard title="Decisões e evidências" subtitle="Fechamento, guardião e planejamento" onPress={()=>setTab("Decisões")}/> 
                <MoreCard title="Fechamento do mês" subtitle="Checklist de contas e cartões" onPress={()=>setTab("Fechamento")}/>
                <MoreCard
                  title="Contas"
                  subtitle={loadedResources.saldos ? `${accountProfiles.filter(selectableAccount).length} aberta(s)` : "Carregando contas…"}
                  onPress={() => setTab("Contas")}
                />
                <MoreCard
                  title="Cartões"
                  subtitle={loadedResources.cartões ? `${cards.length} cartão(ões)` : "Escolher cartão e ver faturas"}
                  onPress={() => setTab("Cartões")}
                />
                <MoreCard
                  title="Categorias"
                  subtitle="Criar, revisar e excluir"
                  onPress={() => setTab("Categorias")}
                />
                <MoreCard
                  title="Compromissos"
                  subtitle="Assinaturas e compras parceladas"
                  onPress={() => setTab("Compromissos")}
                />
                <MoreCard
                  title="Ajustes"
                  subtitle="Conexão, fila e atualização"
                  onPress={() => setTab("Ajustes")}
                />
              </View>
            </>
          )}
          {tab === "Categorias" && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Voltar a Mais" onPress={() => dispatchLocalBack()} style={{minHeight: 44, justifyContent: "center", paddingVertical: 6}}>
                <Text style={{color: "#67DFC3", fontWeight: "700"}}>‹ Voltar a Mais</Text>
              </Pressable>
              <CategoryManager
                api={json}
                money={money}
                spending={dashboard?.category_spending || []}
                periodLabel={dashboard?.period_start ? `${dashboard.period_start.split('-').reverse().join('/')} a ${dashboard.period_end.split('-').reverse().join('/')}` : 'histórico completo'}
                onUpdated={() => refreshAfterMutation("finance")}
              />
            </>
          )}
          {tab === "Compromissos" && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Voltar a Mais" onPress={() => dispatchLocalBack()} style={{minHeight: 44, justifyContent: "center", paddingVertical: 6}}>
                <Text style={{color: "#67DFC3", fontWeight: "700"}}>‹ Voltar a Mais</Text>
              </Pressable>
              <CommitmentManager profiles={accountProfiles}
                cards={cards}
                api={json}
                upload={api.upload}
                accounts={accounts}
                money={money}
                onUpdated={() => refreshAfterMutation("finance", "accounts", "cards")}
              />
            </>
          )}
          {tab === "Cartões" && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Voltar a Mais" onPress={() => dispatchLocalBack()} style={{minHeight: 44, justifyContent: "center", paddingVertical: 6}}>
                <Text style={{color: "#67DFC3", fontWeight: "700"}}>‹ Voltar a Mais</Text>
              </Pressable>
              <CardManager api={json} cards={cards} money={money} onUpdated={() => refreshAfterMutation("cards", "dashboard")} />
            </>
          )}
          {tab === "Capturar" && (
            <CapturePanel api={json} upload={api.upload} kind={captureKind}
              onKind={value => setCaptureKind(value as "purchase"|"income"|"own_transfer")}
              merchant={merchant} onMerchant={setMerchant} amount={amount} onAmount={setAmount}
              date={captureDate} onDate={setCaptureDate} origin={transferOrigin} onOrigin={setTransferOrigin}
              destination={transferDestination} onDestination={setTransferDestination} accounts={accountProfiles}
              account={captureAccount} onAccount={value => manual.change('account',value)}
              payment={capturePayment} onPayment={value => manual.change('payment',value)}
              installments={manual.draft.installments} onInstallments={value => manual.change('installments', value)}
              queueCount={queueCount} rejectedCount={deadLetters.length} syncError={[syncError,nativeInboxError].filter(Boolean).join(' ')} onSync={() => sync()} onReview={() => setTab('Revisão')} onQueueSettings={() => setTab('Ajustes')} onClearRejected={() => discardLocalItems(deadLetters)}
              transcript={voiceTranscript} onMovementVoice={applyMovementVoice} onEvidenceVoice={saveVoiceEvidence}
              onSave={saveCapture} onPhoto={() => runCaptureTask(capturePhoto)}
              onDiscard={async () => {
                const valid = captureScope();
                if (!valid()) return;
                await manual.clear();
                await deleteVoiceDraft(db, 'capture');
                if (!valid()) return;
                captureMessageId.current = ''; setCaptureMessage('Rascunho excluído.'); setCaptureError('');
              }}
              onDocument={format => runCaptureTask(() => pickDocument("document", format))}
              onStatement={() => runCaptureTask(() => pickDocument("statement"))}
              onBatch={format => runCaptureTask(() => pickDocument("batch", format))}
              onPdfCpfChanged={retryPdfCaptures}
              batchAccounts={accounts} batchRevision={syncRevision} onBatchUpdated={async () => {lastImportCheck.current = 0; void sync(); await refreshAfterMutation("finance", "reviews", "accounts", "cards");}}
              onBatchResolved={batchId => {if (captureMessageId.current === batchId) {captureMessageId.current = ""; setCaptureMessage("");}}}
              busy={captureSaving || !manual.ready} savedRevision={captureSavedRevision} message={captureMessage} error={captureError}
              notificationAvailable={NotificationCapture.available}
              notificationEnabled={NotificationCapture.available && NotificationCapture.isEnabled()}
              onNotifications={() => NotificationCapture.openSettings()} />
          )}
          {tab === "Contas" && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Voltar a Mais" onPress={() => dispatchLocalBack()} style={{minHeight: 44, justifyContent: "center", paddingVertical: 6}}>
                <Text style={{color: "#67DFC3", fontWeight: "700"}}>‹ Voltar a Mais</Text>
              </Pressable>
              <AccountPositions api={json} money={money} onOpen={() => contentScroll.current?.scrollTo({y: 0, animated: false})}
                onUpdated={async items => {setAccountProfiles(items); await refreshAfterMutation("account-catalog");}}/>
            </>
          )}
          {tab === "Ajustes" && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Voltar a Mais" onPress={() => dispatchLocalBack()} style={{minHeight: 44, justifyContent: "center", paddingVertical: 6}}>
                <Text style={{color: "#67DFC3", fontWeight: "700"}}>‹ Voltar a Mais</Text>
              </Pressable>
              <Text style={s.section}>Conexão</Text>
              <Text style={s.body}>API: {API || "não configurada"}</Text>
              <Text style={s.muted}>
                Defina EXPO_PUBLIC_API_BASE_URL com o hostname acessível pela
                tailnet. Não use nem armazene a chave xAI neste aplicativo.
              </Text>
              <Button
                title={manualLoading ? "Sincronizando…" : "Sincronizar agora"}
                disabled={manualLoading || captureSaving}
                onPress={() => { void Promise.all([refresh("all", true), sync()]).catch(() => {}); }}
              />
              <Button title="Minha conta e privacidade" secondary onPress={() => router.push('/account')} />
              <Button title="Bloquear agora" secondary onPress={auth.lock} />
              <Text style={s.section}>{nativeInboxError?'Pendências locais conhecidas':'Fila local'}: {queueCount}</Text>
              {!!nativeInboxError&&<View>
                <Text accessibilityRole="alert" style={s.warning}>{nativeInboxError}</Text>
                <Text style={s.muted}>O conteúdo original permanece preservado. Uma falha de leitura não significa fila vazia.</Text>
                <Button title="Tentar recuperar notificações" secondary disabled={captureSaving} onPress={()=>runCaptureTask(async()=>{await sync();})}/>
                <Button title="Exportar fonte de recuperação" secondary disabled={captureSaving} onPress={()=>runCaptureTask(async()=>{
                  const session=authState.sessionIdentity();ensureQueueSession(session);
                  const uri=NotificationCapture.exportRecovery();
                  const sharing=await import('expo-sharing');ensureQueueSession(session);
                  if(!await sharing.isAvailableAsync())throw new Error('O compartilhamento de arquivos não está disponível neste aparelho.');
                  ensureQueueSession(session);await withDevicePrompt(()=>sharing.shareAsync(uri,{mimeType:'application/json',dialogTitle:'Guardar fonte original de notificações'}));
                })}/>
              </View>}
              {!!deadLetters.length && <View>
                <Text style={s.section}>Arquivos ou eventos com falha: {deadLetters.length}</Text>
                <Text style={s.muted}>São tentativas que falharam neste aparelho. Excluir tira o item da fila local. Lançamentos já salvos no extrato não mudam.</Text>
                <Button title="Limpar falhas" secondary disabled={captureSaving} onPress={() => discardLocalItems(deadLetters)} />
                <Button title="Conferir arquivos já importados" secondary disabled={captureSaving} onPress={() => runCaptureTask(async () => {await reconcileLocalFiles(true);})} />
                {deadLetters.map(row => <View key={row.event_id} style={{paddingVertical:8}}>
                  <Text style={s.muted}>{row.filename || row.event_id} · {queueFailureLabel(row)}</Text>
                  {canReviewAsStatement(row) && <Button title="Revisar como fatura / extrato" secondary disabled={captureSaving} onPress={() => runCaptureTask(() => retryLocalItem(row, true))} />}
                  <Button title="Tentar este item novamente" secondary disabled={captureSaving} onPress={() => runCaptureTask(() => retryLocalItem(row))} />
                  <Button title="Excluir este item" secondary disabled={captureSaving} onPress={() => discardLocalItem(row)} />
                </View>)}
              </View>}
              <Text style={s.muted}>
                Capture de notificações:{" "}
                {NotificationCapture.available
                  ? NotificationCapture.isEnabled()
                    ? "ativada"
                    : "desativada"
                  : "Android build necessário"}
              </Text>
              <Text style={s.muted}>
                {Platform.OS === 'web' ? 'Itens permanecem no armazenamento local do navegador até receber confirmação do servidor.' : 'Itens permanecem no SQLite do aparelho até receber confirmação do servidor.'}
              </Text>
              <AppUpdate api={json} baseUrl={API} />
            </>
          )}
        </ScrollView>
      )}
      <View
        style={{
          flexShrink: 0,
          backgroundColor: "#101C1F",
          borderTopWidth: 1,
          borderColor: "#315054",
          paddingBottom: Math.max(insets.bottom, 12),
        }}
      >
        <View style={s.tabs}>
          {(
            ["Início", "Movimentações", "Capturar", "Revisão", "Mais"] as Tab[]
          ).map((t) => {
            const selected =
              tab === t ||
              (t === "Movimentações" && tab === "Detalhe") ||
              (t === "Mais" &&
                [
                  "Contas",
                  "Cartões",
                  "Categorias",
                  "Compromissos",
                  "Rascunhos",
                  "Decisões",
                  "Comprar",
                  "Fechamento",
                  "Ajustes",
                ].includes(tab));
            const label = t === "Movimentações" ? "Extrato" : t;
            return (
              <Pressable
                key={t}
                accessibilityRole="tab"
                accessibilityLabel={t}
                accessibilityState={{ selected }}
                aria-selected={selected}
                onPress={() => setTab(t)}
                style={s.tab}
              >
                <Text style={[s.tabText, selected && s.tabActive]}>
                  {label}
                </Text>
                {t === "Revisão" && reviews.length > 0 && (
                  <View style={s.badge}>
                    <Text style={s.badgeText}>{reviews.length}</Text>
                  </View>
                )}
              </Pressable>
            );
          })}
        </View>
      </View>
    </SafeAreaView></LocalBackScope>
  );
}
function CashflowChart({ data }: { data: any[] }) {
  const {fontScale}=useWindowDimensions();
  const visible = data;
  const max = Math.max(
    1,
    ...visible.flatMap((item) => [
      Number(item.income || 0),
      Number(item.expenses || 0),
    ]),
  );
  if (!visible.length)
    return (
      <Text style={s.muted}>Sem movimentações financeiras neste período.</Text>
    );
  return (
    <View style={s.chartCard}>
      <View style={s.chartLegend}>
        <View style={[s.legendDot, { backgroundColor: "#4FD1B5" }]} />
        <Text style={s.legendText}>Entradas</Text>
        <View style={[s.legendDot, { backgroundColor: "#FF8C83" }]} />
        <Text style={s.legendText}>Saídas</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={s.chartContent}
      >
        {visible.map((item) => {
          const incomeHeight = Math.max(
              3,
              Math.round((Number(item.income || 0) / max) * 112),
            ),
            expenseHeight = Math.max(
              3,
              Math.round((Number(item.expenses || 0) / max) * 112),
            );
          return (
            <View key={item.month} style={[s.chartGroup,{minWidth:34*Math.max(1,fontScale||1)}]}>
              <View style={s.chartBars}>
                <View
                  style={[
                    s.chartBar,
                    { height: incomeHeight, backgroundColor: "#4FD1B5" },
                  ]}
                />
                <View
                  style={[
                    s.chartBar,
                    { height: expenseHeight, backgroundColor: "#FF8C83" },
                  ]}
                />
              </View>
              <Text style={s.chartMonth}>
                {String(item.month).slice(5)}/{String(item.month).slice(2, 4)}
              </Text>
              <Text
                style={[s.chartNet, Number(item.net || 0) < 0 ? s.bad : s.good]}
              >
                {Number(item.net || 0) < 0
                  ? "−"
                  : Number(item.net || 0) > 0
                    ? "+"
                    : "·"}
              </Text>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}
function ComparisonCard({ data, money: formatMoney }: { data: any; money: (value: number) => string }) {
  if (!data) return null;
  const income =
      data.income_change_pct == null ? NaN : Number(data.income_change_pct),
    expenses =
      data.expenses_change_pct == null ? NaN : Number(data.expenses_change_pct);
  const pct = (value: number) =>
    Number.isFinite(value)
      ? `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`
      : "sem base";
  return (
    <View style={s.comparisonCard}>
      <View style={s.comparisonHeader}>
        <Text style={s.comparisonTitle}>Comparação com o período anterior</Text>
        <Text style={s.comparisonDates}>
          {String(data.period_start || "")
            .split("-")
            .reverse()
            .join("/")}{" "}
          a{" "}
          {String(data.period_end || "")
            .split("-")
            .reverse()
            .join("/")}
        </Text>
      </View>
      <View style={s.comparisonRow}>
        <View style={s.comparisonMetric}>
          <Text style={s.muted}>Entradas</Text>
          <Text style={[s.comparisonValue, income >= 0 ? s.good : s.bad]}>
            {pct(income)}
          </Text>
        </View>
        <View style={s.comparisonMetric}>
          <Text style={s.muted}>Despesas</Text>
          <Text style={[s.comparisonValue, expenses <= 0 ? s.good : s.bad]}>
            {pct(expenses)}
          </Text>
        </View>
        <View style={s.comparisonMetric}>
          <Text style={s.muted}>Saldo</Text>
          <SingleLineValue
            style={[
              s.comparisonValue,
              Number(data.net_change) >= 0 ? s.good : s.bad,
            ]}
          >
            {formatMoney(Number(data.net_change || 0))}
          </SingleLineValue>
        </View>
      </View>
    </View>
  );
}
function CategoryBar({
  item,
  max,
  onPress,
  money: formatMoney,
}: {
  item: any;
  max: number;
  onPress: () => void;
  money: (value: number) => string;
}) {
  const width =
    `${Math.max(3, Math.min(100, (Number(item.amount || 0) / Math.max(1, max)) * 100))}%` as `${number}%`;
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={s.categoryItem}>
      <View style={s.categoryHeader}>
        <Text numberOfLines={1} style={[s.body, { flex: 1 }]}>
          {item.category}
        </Text>
        <SingleLineValue style={[s.body, { flexBasis: "48%", textAlign: "right" }]}>{`${formatMoney(Number(item.amount || 0))} ›`}</SingleLineValue>
      </View>
      <View style={s.categoryTrack}>
        <View style={[s.categoryFill, { width }]} />
      </View>
    </Pressable>
  );
}
function MoreCard({
  title,
  subtitle,
  onPress,
  fullWidth = false,
}: {
  title: string;
  subtitle: string;
  onPress: () => void;
  fullWidth?: boolean;
}) {
  const {width,fontScale}=useWindowDimensions();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={title} onPress={onPress} style={[s.moreCard, fullWidth||compactLayout(width,fontScale) ? s.moreCardFull : s.moreCardGrid]}>
      <Text style={s.moreTitle}>{title}</Text>
      <Text style={s.moreSubtitle}>{subtitle}</Text>
      <Text style={s.moreLink}>Abrir ›</Text>
    </Pressable>
  );
}
function Metric({ label, value }: { label: string; value: string }) {
  const { width,fontScale } = useWindowDimensions();
  return (
    <View style={[s.metric, compactLayout(width,fontScale) && s.metricNarrow, width/fontScale >= 900 && s.metricDesktop]}>
      <Text
        style={{
          fontSize: 12,
          lineHeight: 17,
          color: "#92A4A2",
          minHeight: 34,
        }}
      >
        {label}
      </Text>
      <SingleLineValue style={s.metricValue}>
        {value}
      </SingleLineValue>
    </View>
  );
}
function Button({
  title,
  onPress,
  secondary = false,
  disabled = false,
}: {
  title: string;
  onPress: () => void;
  secondary?: boolean;
  disabled?:boolean;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={title} accessibilityState={{disabled}} aria-disabled={disabled} disabled={disabled}
      onPress={onPress}
      style={[s.button, secondary && s.buttonSecondary]}
    >
      <Text style={[s.buttonText, secondary && s.buttonTextSecondary]}>
        {title}
      </Text>
    </Pressable>
  );
}
const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0C1518" },
  authOverlay: {
    position: "absolute",
    zIndex: 5,
    left: 16,
    right: 16,
    top: 100,
    backgroundColor: "#122124",
    padding: 20,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: "#315054",
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 18,
    backgroundColor: "#122124",
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  brand: {
    fontSize: 10,
    letterSpacing: 2,
    color: "#4FD1B5",
    fontWeight: "800",
  },
  title: { fontSize: 25, color: "#F4F7F5", fontWeight: "700", marginTop: 4 },
  connection: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 8, height: 8, borderRadius: 8 },
  connectionText: { color: "#B5C5C4", fontSize: 11 },
  content: { padding: 18, paddingBottom: 30 },
  contentShell: { width: "100%", maxWidth: 1120, alignSelf: "center" },
  progress: { padding: 8, alignItems: "center", gap: 4 },
  muted: { fontSize: 12, color: "#92A4A2", marginTop: 5 },
  eyebrow: {
    fontSize: 10,
    letterSpacing: 1.5,
    color: "#92A4A2",
    marginBottom: 10,
  },
  hero: {
    backgroundColor: "#183034",
    padding: 20,
    borderRadius: 18,
    marginBottom: 12,
  },
  label: { fontSize: 13, color: "#AFC1BF" },
  heroValue: {
    fontSize: 34,
    color: "#F6FAF7",
    fontWeight: "700",
    marginTop: 8,
    marginBottom: 16,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 10,
    marginBottom: 8,
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginBottom: 8,
  },
  good: { fontSize: 13, color: "#4FD1B5" },
  bad: { fontSize: 13, color: "#FF8C83" },
  metric: {
    flexGrow: 1,
    flexBasis: "46%",
    minWidth: 0,
    backgroundColor: "#142326",
    padding: 16,
    borderRadius: 14,
    marginBottom: 4,
  },
  metricDesktop: { flexBasis: "22%" },
  metricNarrow: { flexBasis: "100%" },
  metricValue: {
    fontSize: 21,
    color: "#F4F7F5",
    fontWeight: "700",
    marginTop: 5,
  },
  section: {
    fontSize: 18,
    fontWeight: "700",
    color: "#F4F7F5",
    marginTop: 18,
    marginBottom: 10,
  },
  sectionCaption: { color: "#92A4A2", fontSize: 11, marginBottom: 10 },
  listRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 13,
    borderBottomWidth: 1,
    borderColor: "#213235",
    gap: 12,
  },
  body: { color: "#E7EFEC", fontSize: 14, fontWeight: "500" },
  originalText: { color: "#738684", fontSize: 10, marginTop: 2 },
  detailField: { alignSelf:"stretch",gap:5,paddingVertical:10,borderBottomWidth:1,borderColor:"#213235" },
  installmentReference: { alignSelf:"stretch", gap:6, marginVertical:10, padding:12, borderWidth:1, borderColor:"#34766D", borderRadius:12, backgroundColor:"#102A29" },
  detailValue: { flex: 1, minWidth: 0, flexShrink: 1, textAlign: "right" },
  descriptionValue: { minWidth: 0, flexShrink: 1, ...(Platform.OS === "web" ? { overflowWrap: "anywhere" as any } : {}) },
  syncRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
    gap: 10,
  },
  syncButton: {
    borderWidth: 1,
    borderColor: "#3D625D",
    borderRadius: 10,
    paddingHorizontal: 11,
    paddingVertical: 9,
    minHeight: 40,
    justifyContent: "center",
  },
  syncButtonText: { color: "#4FD1B5", fontSize: 11, fontWeight: "700" },
  foot: { color: "#738684", fontSize: 11, marginTop: 22 },
  warningCard: { marginHorizontal: 18, marginTop: 10, padding: 12, backgroundColor: "#2B2515", borderWidth: 1, borderColor: "#705D23", borderRadius: 12 },
  warning: { color: "#F4B942", fontSize: 12 },
  warningHint: { color: "#C8B984", fontSize: 11, lineHeight: 16, marginTop: 5 },
  retryButton: { alignSelf: "flex-start", marginTop: 9, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 9, backgroundColor: "#F4B942" },
  retryText: { color: "#201A0B", fontSize: 11, fontWeight: "800" },
  input: {
    backgroundColor: "#142326",
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 13,
    color: "#F5F8F6",
    fontSize: 16,
    marginTop: 6,
    marginBottom: 14,
  },
  button: {
    backgroundColor: "#49C5AA",
    padding: 14,
    minHeight: 46,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 8,
  },
  buttonSecondary: {
    backgroundColor: "#1A2A2D",
    borderWidth: 1,
    borderColor: "#315054",
  },
  buttonText: { color: "#061412", fontSize: 14, fontWeight: "700" },
  buttonTextSecondary: { color: "#C5D8D4" },
  reviewCard: {
    backgroundColor: "#142326",
    borderRadius: 14,
    padding: 15,
    marginBottom: 12,
  },
  comparisonCard: {
    backgroundColor: "#10282A",
    borderWidth: 1,
    borderColor: "#28534D",
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
  },
  comparisonHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 10,
  },
  comparisonTitle: {
    color: "#DCE8E5",
    fontSize: 12,
    fontWeight: "800",
    flex: 1,
  },
  comparisonDates: { color: "#AFC1BF", fontSize: 11 },
  comparisonRow: { flexDirection: "row", gap: 8, marginTop: 11 },
  comparisonMetric: { flex: 1 },
  comparisonValue: { fontSize: 14, fontWeight: "800", marginTop: 4 },
  activeFilter: {
    backgroundColor: "#183034",
    borderWidth: 1,
    borderColor: "#3D766B",
    borderRadius: 12,
    padding: 11,
    marginBottom: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  activeFilterLabel: { color: "#92A4A2", fontSize: 8, letterSpacing: 1 },
  activeFilterText: {
    color: "#DCE8E5",
    fontSize: 11,
    fontWeight: "700",
    marginTop: 3,
  },
  activeFilterClear: { color: "#4FD1B5", fontSize: 11, fontWeight: "800" },
  filterLabel: {
    fontSize: 10,
    letterSpacing: 1.5,
    color: "#92A4A2",
    marginBottom: 8,
  },
  periodScroll: { flexGrow: 0, marginHorizontal: -2, marginBottom: 8 },
  periodFilters: { gap: 7, paddingHorizontal: 2 },
  periodChip: {
    borderWidth: 1,
    borderColor: "#315054",
    backgroundColor: "#142326",
    borderRadius: 18,
    paddingHorizontal: 13,
    paddingVertical: 9,
    minHeight: 40,
  },
  periodChipActive: { backgroundColor: "#49C5AA", borderColor: "#49C5AA" },
  periodChipText: { color: "#AFC1BF", fontSize: 11, fontWeight: "700" },
  periodChipTextActive: { color: "#061412" },
  periodCaption: {
    color: "#92A4A2",
    fontSize: 10,
    lineHeight: 15,
    marginBottom: 17,
  },
  chartCard: { backgroundColor: "#142326", borderRadius: 14, padding: 14 },
  chartLegend: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 12,
  },
  legendDot: { width: 8, height: 8, borderRadius: 4, marginLeft: 4 },
  legendText: { color: "#92A4A2", fontSize: 10, marginRight: 8 },
  chartContent: {
    alignItems: "flex-end",
    gap: 5,
    minHeight: 150,
    paddingHorizontal: 2,
  },
  chartGroup: { width: 33, alignItems: "center" },
  chartBars: {
    height: 116,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 3,
  },
  chartBar: { width: 10, borderRadius: 4 },
  chartMonth: { color: "#92A4A2", fontSize: 9, marginTop: 5 },
  chartNet: { fontSize: 10, fontWeight: "800", marginTop: 2 },
  categoryItem: {
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderColor: "#213235",
  },
  categoryHeader: { flexDirection: "row", alignItems: "center", gap: 12 },
  categoryTrack: {
    height: 5,
    backgroundColor: "#17282B",
    borderRadius: 4,
    overflow: "hidden",
    marginTop: 8,
  },
  categoryFill: { height: 5, backgroundColor: "#4FD1B5", borderRadius: 4 },
  recurringCard: {
    backgroundColor: "#142326",
    borderRadius: 14,
    padding: 16,
    marginBottom: 14,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  recurringValue: {
    color: "#4FD1B5",
    fontSize: 19,
    fontWeight: "800",
    flexShrink: 1,
    flexBasis: "42%",
    textAlign: "right",
  },
  recurringDescription: { flex: 1, minWidth: 0 },
  searchCard: {
    backgroundColor: "#122124",
    borderRadius: 14,
    padding: 12,
    marginBottom: 12,
  },
  accountFilterBox: {
    backgroundColor: "#122124",
    borderRadius: 14,
    padding: 12,
    marginBottom: 12,
  },
  searchInput: {
    backgroundColor: "#18292C",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 11,
    color: "#F5F8F6",
    fontSize: 14,
  },
  searchActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8,
  },
  searchPrimary: {
    backgroundColor: "#49C5AA",
    borderRadius: 9,
    paddingHorizontal: 16,
    paddingVertical: 10,
    minHeight: 40,
  },
  searchPrimaryText: { color: "#061412", fontWeight: "800", fontSize: 12 },
  searchClear: {
    borderWidth: 1,
    borderColor: "#3D625D",
    borderRadius: 9,
    paddingHorizontal: 16,
    paddingVertical: 10,
    minHeight: 40,
  },
  searchClearText: { color: "#C5D8D4", fontWeight: "700", fontSize: 12 },
  moreGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  moreCardGrid: {
    flexGrow: 1,
    flexBasis: "46%",
    minWidth: 145,
  },
  moreCardFull: { alignSelf: "stretch", marginBottom: 10 },
  moreCard: {
    minHeight: 132,
    backgroundColor: "#142326",
    borderRadius: 14,
    padding: 15,
    justifyContent: "space-between",
  },
  moreTitle: { color: "#F4F7F5", fontSize: 17, fontWeight: "800" },
  moreSubtitle: {
    color: "#92A4A2",
    fontSize: 11,
    lineHeight: 16,
    marginTop: 6,
  },
  moreLink: {
    color: "#4FD1B5",
    fontSize: 12,
    fontWeight: "800",
    marginTop: 12,
  },
  tabs: {
    flexDirection: "row",
    justifyContent: "space-around",
    paddingHorizontal: 8,
    paddingVertical: 8,
    paddingBottom: 10,
    borderTopWidth: 1,
    borderColor: "#213235",
    backgroundColor: "#101C1F",
  },
  tab: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 3,
    paddingVertical: 10,
    minHeight: 44,
  },
  tabText: { fontSize: 11, color: "#819390" },
  tabActive: { color: "#4FD1B5", fontWeight: "700" },
  badge: {
    position: "absolute",
    right: -5,
    top: -5,
    minWidth: 16,
    height: 16,
    borderRadius: 9,
    backgroundColor: "#E66F66",
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: { color: "#fff", fontSize: 9, fontWeight: "700" },
});