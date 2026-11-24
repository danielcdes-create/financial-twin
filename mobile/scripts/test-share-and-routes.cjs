// P1 runtime contract suite for app/+native-intent.ts (exposes BUG-11), app/handle-share.tsx,
// app/_layout.tsx, app/account.tsx, app/compromissos/mes/[month].tsx, app/index.tsx, app/login.tsx,
// and modules/notification-capture/index.ts (v2 snapshot & recovery).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');

global.IS_REACT_ACT_ENVIRONMENT = true;
const done = typeof globalThis.__expectAsyncCompletion === 'function'
  ? globalThis.__expectAsyncCompletion('test-share-and-routes')
  : null;
let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'test-share-and-routes async suite did not finish');
});

const settle = (ms = 20) => act(async () => { await new Promise(resolve => setTimeout(resolve, ms)); });
const plain = v => JSON.parse(JSON.stringify(v));

(async () => {
  const bug11Failures = [];

  // =========================================================================
  // 1. modules/notification-capture/index.ts (v2 getInboxSnapshot & recovery)
  // =========================================================================
  {
    const loadNotificationBridge = nativeImpl => {
      const file = path.resolve('modules/notification-capture/index.ts');
      const mod = {exports: {}};
      const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
      }).outputText;
      vm.runInNewContext(
        compiled,
        {
          module: mod,
          exports: mod.exports,
          Date,
          Number,
          String,
          Boolean,
          Array,
          Object,
          Error,
          JSON,
          require: name => {
            if (name === 'expo-modules-core') {
              return {
                requireNativeModule: modName => {
                  assert.equal(modName, 'NotificationCapture');
                  if (!nativeImpl) throw new Error('Module unavailable on web');
                  return nativeImpl;
                },
              };
            }
            throw new Error(`Unexpected import: ${name}`);
          },
        },
        {filename: file},
      );
      return mod.exports;
    };

    // Without native module (e.g. Web/iOS)
    const unavailMod = loadNotificationBridge(null).default;
    assert.equal(unavailMod.available, false);
    assert.equal(unavailMod.isEnabled(), false);
    assert.deepEqual(plain(unavailMod.getInboxSnapshot()), {
      version: 2,
      events: [],
      state: 'ready',
      recovery_count: 0,
      error: '',
    });
    assert.deepEqual(plain(unavailMod.getQueuedEvents()), []);
    assert.throws(() => unavailMod.exportRecovery(), /Atualize o Android/);

    // With v2 native module
    let snapshotPayload = '';
    const syncedIds = [];
    let settingsOpened = 0;
    const validEvent = {
      event_id: '550e8400-e29b-41d4-a716-446655440000',
      source_type: 'android_notification',
      source_provider: 'com.itau',
      captured_at: '2026-11-10T17:35:00.000Z',
      raw_payload: {title: 'Compra aprovada', text: 'R$ 45,90'},
      parsed_payload: {amount: 45.9, currency: 'BRL'},
    };

    const bridge = loadNotificationBridge({
      isEnabled: () => true,
      openSettings: () => { settingsOpened++; },
      getQueuedEvents: () => '[]',
      getInboxSnapshot: () => snapshotPayload,
      exportRecovery: () => 'file:///data/user/0/com.financialtwin.app/files/notification-recovery/notifications-1.json',
      markSynced: id => { syncedIds.push(id); },
    });

    assert.equal(bridge.validateCapturedEvent(validEvent), true);
    assert.equal(bridge.validateCapturedEvent({...validEvent, captured_at: '2026-02-30T10:00:00.000Z'}), false, 'Impossible calendar date rejected');
    assert.equal(bridge.validateCapturedEvent({...validEvent, event_id: 'not-a-uuid'}), false);
    assert.equal(bridge.validateCapturedEvent(null), false);

    // Ready v2 snapshot with dropped_count
    snapshotPayload = JSON.stringify({
      version: 2,
      events: [validEvent],
      state: 'ready',
      recovery_count: 0,
      dropped_count: 3,
      error: '',
    });
    assert.equal(bridge.default.available, true);
    assert.equal(bridge.default.isEnabled(), true);
    bridge.default.openSettings();
    assert.equal(settingsOpened, 1);
    const readySnap = bridge.default.getInboxSnapshot();
    assert.equal(readySnap.state, 'ready');
    assert.equal(readySnap.events.length, 1);
    assert.equal(readySnap.dropped_count, 3);
    assert.equal(readySnap.recovery_count, 0);
    assert.equal(bridge.default.getQueuedEvents().length, 1);

    // Explicit needs_recovery snapshot + invalid event filtering promotion to needs_recovery
    snapshotPayload = JSON.stringify({
      version: 2,
      events: [validEvent, {...validEvent, event_id: 'bad-id'}],
      state: 'ready',
      recovery_count: 2,
      dropped_count: 1,
      error: '',
    });
    const filteredSnap = bridge.default.getInboxSnapshot();
    assert.equal(filteredSnap.state, 'needs_recovery');
    assert.equal(filteredSnap.events.length, 1);
    assert.equal(filteredSnap.recovery_count, 2);
    assert.equal(filteredSnap.dropped_count, 1);
    assert.match(filteredSnap.error, /inválidas preservadas/);

    // Unavailable state throws from getQueuedEvents()
    snapshotPayload = JSON.stringify({
      version: 2,
      events: [],
      state: 'unavailable',
      recovery_count: 0,
      dropped_count: 0,
      error: 'Falha de leitura local.',
    });
    assert.equal(bridge.default.getInboxSnapshot().state, 'unavailable');
    assert.throws(() => bridge.default.getQueuedEvents(), /Falha de leitura local/);

    // Corrupt JSON from native returns unavailable snapshot safely
    snapshotPayload = '{corrupt-json';
    assert.equal(bridge.default.getInboxSnapshot().state, 'unavailable');

    // exportRecovery and markSynced
    assert.match(bridge.default.exportRecovery(), /^file:\/\/\/.*notification-recovery/);
    bridge.default.markSynced(validEvent.event_id);
    assert.deepEqual(syncedIds, [validEvent.event_id]);
  }

  // =========================================================================
  // 2. Shared route harness for app/+native-intent.ts, handle-share.tsx,
  //    _layout.tsx, account.tsx, compromissos/mes/[month].tsx, index.tsx, login.tsx
  // =========================================================================
  const sharedGlobal = {};
  let incomingShareState = {resolvedSharedPayloads: [], isResolving: true, error: null};
  let authContextState = {
    authenticated: false,
    loginScreen: React.createElement('Text', null, 'Mock Login Screen'),
    account: {username: 'daniel', display_name: 'Daniel'},
    biometricOn: false,
    biometricSupported: true,
    setAccount() {},
    toggleBiometric: async () => {},
    replaceSession: async () => {},
    lock() {},
    logout: async () => {},
  };
  const routerCalls = [];
  let canGoBackFlag = true;
  let searchParams = {month: '2026-11', periodo: '6'};
  const executedSql = [];
  let lastStackProps = null;
  let lastActiveScreenNames = [];

  function MockStack(props) {
    lastStackProps = props;
    const active = [];
    for (const group of React.Children.toArray(props.children)) {
      if (group?.props?.guard) {
        for (const scr of React.Children.toArray(group.props.children)) {
          if (scr?.props?.name) active.push(scr.props.name);
        }
      }
    }
    lastActiveScreenNames = active;
    return React.createElement('View', {testID: 'stack'}, active.map(n => React.createElement('Text', {key: n}, n)));
  }
  MockStack.Protected = function Protected() { return null; };
  MockStack.Screen = function Screen() { return null; };

  const native = {
    Platform: {OS: 'android'},
    StyleSheet: {create: s => s},
    BackHandler: {addEventListener: () => ({remove() {}})},
    Keyboard: {dismiss() {}},
    ...Object.fromEntries(['ActivityIndicator', 'Pressable', 'ScrollView', 'Text', 'TextInput', 'View'].map(n => [n, n])),
  };

  const mocks = {
    react: React,
    'react-native': native,
    'react-native-safe-area-context': {
      SafeAreaProvider: ({children}) => React.createElement('SafeAreaProvider', null, children),
      SafeAreaView: ({children, style}) => React.createElement('SafeAreaView', {style}, children),
    },
    'expo-status-bar': {StatusBar: () => null},
    'expo-sharing': {
      useIncomingShare: () => incomingShareState,
    },
    'expo-router': {
      Stack: MockStack,
      usePathname: () => '/account',
      useLocalSearchParams: () => searchParams,
      router: {
        canGoBack: () => canGoBackFlag,
        back: () => routerCalls.push({op: 'back'}),
        replace: href => routerCalls.push({op: 'replace', href}),
        push: href => routerCalls.push({op: 'push', href}),
      },
    },
    '../App': {
      __esModule: true,
      default: props => React.createElement('AppStub', props),
    },
    '../components/AuthProvider': {
      __esModule: true,
      default: ({children}) => React.createElement('AuthProviderStub', null, children),
      useAuth: () => authContextState,
      AuthButton: ({title, onPress}) => React.createElement('Pressable', {accessibilityLabel: title, onPress}, React.createElement('Text', null, title)),
      authStyles: {root: {flex: 1}},
    },
    '../components/AccountSecurity': {
      __esModule: true,
      default: () => React.createElement('AccountSecurityStub'),
    },
    '../components/AppDialogHost': {
      __esModule: true,
      default: () => null,
    },
    '../components/UserOperationBoundary': {
      __esModule: true,
      default: ({children}) => React.createElement(React.Fragment, null, children),
    },
    '../../../components/CommitmentProjection': {
      __esModule: true,
      default: props => React.createElement('CommitmentProjectionStub', props),
    },
    '../../../utils/sessionClient': {
      api: {json: async () => ({items: []})},
    },
    '../utils/queueDb': {
      SQLiteProvider: ({children, onInit}) => {
        React.useEffect(() => {
          void onInit?.({
            execAsync: async sql => { executedSql.push(sql); },
          });
        }, [onInit]);
        return React.createElement(React.Fragment, null, children);
      },
    },
  };

  const cache = new Map();
  function loadRoute(file) {
    file = path.resolve(file);
    if (cache.has(file)) return cache.get(file);
    const mod = {exports: {}};
    cache.set(file, mod.exports);
    const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.React,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
    }).outputText;
    vm.runInNewContext(
      compiled,
      {
        module: mod,
        exports: mod.exports,
        console,
        Error,
        Date,
        Intl,
        Number,
        String,
        Boolean,
        URL,
        Symbol,
        Promise,
        setTimeout,
        clearTimeout,
        globalThis: sharedGlobal,
        require: name => {
          if (mocks[name]) return mocks[name];
          if (!name.startsWith('.')) return require(name);
          const target = path.resolve(path.dirname(file), name);
          return loadRoute(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
        },
      },
      {filename: file},
    );
    cache.set(file, mod.exports);
    return mod.exports;
  }

  // 2A. handle-share.tsx (resolving spinner, valid image share, non-image/empty share, share error)
  {
    const HandleShare = loadRoute('app/handle-share.tsx').default;
    sharedGlobal.__financialTwinPendingShare = true;
    sharedGlobal.__financialTwinShareSeq = 5;
    incomingShareState = {resolvedSharedPayloads: [], isResolving: true, error: null};

    let renderer;
    await act(async () => { renderer = create(React.createElement(HandleShare)); });
    await settle();
    assert.equal(renderer.root.findAllByType('ActivityIndicator').length, 1);
    assert.ok(renderer.root.findAllByType('Text').some(t => t.props.children === 'Lendo imagem compartilhada…'));
    assert.equal(sharedGlobal.__financialTwinPendingShare, true, 'Remains pending while resolving');

    // Valid image share
    incomingShareState = {
      resolvedSharedPayloads: [
        {
          contentType: 'image',
          contentUri: 'file:///cache/shared-product.png',
          contentMimeType: 'image/png',
          originalName: 'fone.png',
          contentSize: 8192,
        },
      ],
      isResolving: false,
      error: null,
    };
    await act(async () => { renderer.update(React.createElement(HandleShare)); });
    await settle();
    assert.equal(sharedGlobal.__financialTwinPendingShare, false, 'Cleared once share resolves');
    const appWithImg = renderer.root.findByType('AppStub');
    assert.equal(appWithImg.props.navigationRoute, '/handle-share');
    assert.deepEqual(plain(appWithImg.props.initialPurchaseImage), {
      uri: 'file:///cache/shared-product.png',
      mimeType: 'image/png',
      fileName: 'fone.png',
      fileSize: 8192,
    });
    assert.equal(appWithImg.props.initialPurchaseError, undefined);

    // Non-image / empty share fallback error message
    incomingShareState = {
      resolvedSharedPayloads: [{contentType: 'text', value: 'https://loja.example/item'}],
      isResolving: false,
      error: null,
    };
    await act(async () => { renderer.update(React.createElement(HandleShare)); });
    await settle();
    const appEmpty = renderer.root.findByType('AppStub');
    assert.equal(appEmpty.props.initialPurchaseImage, null);
    assert.match(appEmpty.props.initialPurchaseError, /Compartilhe um print de produto em PNG ou JPEG/);

    // Explicit share error
    incomingShareState = {
      resolvedSharedPayloads: [],
      isResolving: false,
      error: new Error('Permissão negada no anexo compartilhado'),
    };
    await act(async () => { renderer.update(React.createElement(HandleShare)); });
    await settle();
    assert.equal(renderer.root.findByType('AppStub').props.initialPurchaseError, 'Permissão negada no anexo compartilhado');
    await act(async () => renderer.unmount());
  }

  // 2B. _layout.tsx (SQLite table init, Stack.Protected guards, pending share one-shot redirect on auth)
  {
    const RootLayout = loadRoute('app/_layout.tsx').default;
    const operation = loadRoute('utils/userOperation.ts');
    routerCalls.length = 0;
    executedSql.length = 0;
    authContextState = {...authContextState, authenticated: false};
    sharedGlobal.__financialTwinPendingShare = true;

    let renderer;
    await act(async () => { renderer = create(React.createElement(RootLayout)); });
    await settle();
    assert.equal(executedSql.length, 8, 'onInit initializes all 8 durable SQLite tables');
    assert.deepEqual(lastActiveScreenNames, ['login'], 'Unauthenticated guard exposes only login');
    assert.equal(routerCalls.length, 0, 'Does not redirect to /handle-share before authentication');
    assert.equal(lastStackProps.screenOptions.gestureEnabled, true);

    // Unlock authentication -> redirects to /handle-share ONCE and clears pending flag
    authContextState = {...authContextState, authenticated: true};
    await act(async () => { renderer.update(React.createElement(RootLayout)); });
    await settle();
    assert.deepEqual(lastActiveScreenNames, ['index', 'account', 'handle-share', 'compromissos/mes/[month]']);
    assert.deepEqual(routerCalls, [{op: 'replace', href: '/handle-share'}]);
    assert.equal(sharedGlobal.__financialTwinPendingShare, false);

    // Subsequent re-render while authenticated does NOT redirect a second time
    await act(async () => { renderer.update(React.createElement(RootLayout)); });
    await settle();
    assert.equal(routerCalls.length, 1);

    // UserOperation busy disables stack swipe gestures
    let releaseOp;
    await act(async () => {
      operation.runUserAction(() => new Promise(res => { releaseOp = res; }));
    });
    assert.equal(lastStackProps.screenOptions.gestureEnabled, false);
    await act(async () => { releaseOp(); });
    assert.equal(lastStackProps.screenOptions.gestureEnabled, true);
    await act(async () => renderer.unmount());
  }

  // 2C. account.tsx, compromissos/mes/[month].tsx, index.tsx, login.tsx
  {
    const AccountScreen = loadRoute('app/account.tsx').default;
    routerCalls.length = 0;
    canGoBackFlag = true;
    let renderer;
    await act(async () => { renderer = create(React.createElement(AccountScreen)); });
    await settle();
    assert.equal(renderer.root.findAllByType('AccountSecurityStub').length, 1);
    const backBtn = renderer.root.findByProps({accessibilityLabel: '‹ Voltar'});
    await act(async () => { backBtn.props.onPress(); });
    assert.deepEqual(routerCalls, [{op: 'back'}]);

    routerCalls.length = 0;
    canGoBackFlag = false;
    await act(async () => { backBtn.props.onPress(); });
    assert.deepEqual(routerCalls, [{op: 'replace', href: '/'}]);
    await act(async () => renderer.unmount());

    const MonthRoute = loadRoute('app/compromissos/mes/[month].tsx').default;
    searchParams = {month: '2026-11', periodo: '6'};
    await act(async () => { renderer = create(React.createElement(MonthRoute)); });
    let proj = renderer.root.findByType('CommitmentProjectionStub');
    assert.equal(proj.props.initialMonth, '2026-11');
    assert.equal(proj.props.initialMonths, 6);
    assert.equal(proj.props.urlDriven, true);
    assert.match(proj.props.money(1234.5), /1\.234,50/);

    searchParams = {month: '2026-12', periodo: 'all'};
    await act(async () => { renderer.update(React.createElement(MonthRoute)); });
    proj = renderer.root.findByType('CommitmentProjectionStub');
    assert.equal(proj.props.initialMonth, '2026-12');
    assert.equal(proj.props.initialMonths, null);
    await act(async () => renderer.unmount());

    const IndexRoute = loadRoute('app/index.tsx').default;
    await act(async () => { renderer = create(React.createElement(IndexRoute)); });
    assert.equal(renderer.root.findAllByType('AppStub').length, 1);
    await act(async () => renderer.unmount());

    const LoginRoute = loadRoute('app/login.tsx').default;
    await act(async () => { renderer = create(React.createElement(LoginRoute)); });
    assert.ok(renderer.root.findAllByType('Text').some(t => t.props.children === 'Mock Login Screen'));
    await act(async () => renderer.unmount());
  }

  // 2D. app/+native-intent.ts (redirectSystemPath + BUG-11 stale pending share clearing)
  {
    const {redirectSystemPath} = loadRoute('app/+native-intent.ts');
    delete sharedGlobal.__financialTwinPendingShare;
    delete sharedGlobal.__financialTwinShareSeq;

    const first = await redirectSystemPath({path: 'financialtwin://expo-sharing?payload=1', initial: true});
    assert.equal(first, '/handle-share');
    assert.equal(sharedGlobal.__financialTwinPendingShare, true);
    assert.equal(sharedGlobal.__financialTwinShareSeq, 1);

    // User cancels login while locked, then re-opens the app normally via '/' or 'financialtwin:///'
    const normalRelative = await redirectSystemPath({path: '/', initial: true});
    assert.equal(normalRelative, '/');
    try {
      assert.equal(
        sharedGlobal.__financialTwinPendingShare,
        false,
        "BUG-11: Normal relative system path ('/') must clear __financialTwinPendingShare=false so cancelled share does not hijack later launch",
      );
    } catch (err) {
      bug11Failures.push(err.message);
    }

    await redirectSystemPath({path: 'financialtwin://expo-sharing?payload=2', initial: false});
    assert.equal(sharedGlobal.__financialTwinPendingShare, true);
    assert.equal(sharedGlobal.__financialTwinShareSeq, 2);

    const normalScheme = await redirectSystemPath({path: 'financialtwin:///', initial: false});
    assert.equal(normalScheme, 'financialtwin:///');
    try {
      assert.equal(
        sharedGlobal.__financialTwinPendingShare,
        false,
        "BUG-11: Normal scheme system path ('financialtwin:///') must clear __financialTwinPendingShare=false",
      );
    } catch (err) {
      bug11Failures.push(err.message);
    }
  }

  assert.equal(
    bug11Failures.length,
    0,
    `Share intent & route contract violations:\n- ${bug11Failures.join('\n- ')}`,
  );

  completed = true;
  if (done) done();
  console.log('PASS +native-intent, handle-share, _layout, account, month route, and notification-capture v2 bridge contracts.');
})().catch(error => {
  completed = true;
  if (done) done();
  console.error(error);
  process.exitCode = 1;
});
