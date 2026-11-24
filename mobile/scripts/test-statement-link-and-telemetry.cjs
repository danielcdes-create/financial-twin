// P1 runtime contract suite for StatementCommitmentLink.tsx (swap, unlink, and >200 pagination BUG-11),
// queueDb.web.tsx perf_telemetry + telemetry.ts (parseServerMetrics, offline flush cap, getSummary P50/P95),
// and costControlInsights.ts synthesis fallback.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');

global.IS_REACT_ACT_ENVIRONMENT = true;
const done = typeof globalThis.__expectAsyncCompletion === 'function'
  ? globalThis.__expectAsyncCompletion('test-statement-link-and-telemetry')
  : null;
let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'test-statement-link-and-telemetry async suite did not finish');
});

const settle = (ms = 25) => act(async () => { await new Promise(resolve => setTimeout(resolve, ms)); });
const plain = v => JSON.parse(JSON.stringify(v));

(async () => {
  const bug11Failures = [];

  // =========================================================================
  // 1. queueDb.web.tsx perf_telemetry (INSERT, SELECT, DELETE, MAX_WEB_TELEMETRY=200)
  //    & telemetry.ts (parseServerMetrics, offline flush cap, getSummary P50/P95)
  // =========================================================================
  {
    const webStorage = new Map();
    const localStorage = {
      getItem: key => (webStorage.has(key) ? webStorage.get(key) : null),
      setItem: (key, val) => { webStorage.set(key, String(val)); },
    };
    const webDbFile = path.resolve('utils/queueDb.web.tsx');
    const webDbMod = {exports: {}};
    vm.runInNewContext(
      ts.transpileModule(fs.readFileSync(webDbFile, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true},
      }).outputText,
      {
        module: webDbMod,
        exports: webDbMod.exports,
        Error,
        Number,
        String,
        Boolean,
        Math,
        Map,
        JSON,
        Promise,
        localStorage,
        navigator: {locks: {request: async (_name, fn) => fn()}},
        require: name => {
          if (name === 'react') return React;
          throw new Error(`Unexpected webQueueDb dep: ${name}`);
        },
      },
      {filename: webDbFile},
    );
    const {webQueueDb} = webDbMod.exports;

    // Insert 215 rows + 1 duplicate event_id to test INSERT OR IGNORE & MAX_WEB_TELEMETRY=200 cap eviction
    for (let i = 0; i < 215; i++) {
      const id = `ev-${String(i).padStart(3, '0')}`;
      const stamp = `2026-11-10T12:${String(Math.floor(i / 60)).padStart(2, '0')}:${String(i % 60).padStart(2, '0')}.000Z`;
      await webQueueDb.runAsync(
        'INSERT OR IGNORE INTO perf_telemetry(event_id,timestamp,category,name,duration_ms,status,tab,trace_id,metadata) VALUES(?,?,?,?,?,?,?,?,?)',
        id,
        stamp,
        'data_fetch',
        'GET /v1/movements',
        10 + i,
        'ok',
        'Movimentações',
        `trace-${i}`,
        '{}',
      );
    }
    // Duplicate event_id must be ignored
    await webQueueDb.runAsync(
      'INSERT OR IGNORE INTO perf_telemetry(event_id,timestamp,category,name,duration_ms,status,tab,trace_id,metadata) VALUES(?,?,?,?,?,?,?,?,?)',
      'ev-214',
      '2099-01-01T00:00:00.000Z',
      'data_fetch',
      'DUPLICATE',
      9999,
      'ok',
      'Início',
      'trace-dup',
      '{}',
    );

    const allRows = await webQueueDb.getAllAsync('SELECT * FROM perf_telemetry');
    assert.equal(allRows.length, 200, 'MAX_WEB_TELEMETRY caps web telemetry storage at 200 rows');
    assert.equal(allRows[0].event_id, 'ev-214', 'SELECT * FROM perf_telemetry sorts descending by timestamp');
    assert.equal(allRows[0].name, 'GET /v1/movements', 'INSERT OR IGNORE ignores duplicate event_id');
    assert.equal(allRows[199].event_id, 'ev-015', 'Oldest 15 rows (ev-000..ev-014) evicted');
    assert.equal(allRows.some(r => r.event_id === 'ev-000'), false);

    // Targeted DELETE and bulk DELETE
    await webQueueDb.runAsync('DELETE FROM perf_telemetry WHERE event_id=?', 'ev-214');
    const afterSingleDelete = await webQueueDb.getAllAsync('SELECT * FROM perf_telemetry');
    assert.equal(afterSingleDelete.length, 199);
    assert.equal(afterSingleDelete[0].event_id, 'ev-213');

    await webQueueDb.runAsync('DELETE FROM perf_telemetry');
    const afterCapDelete = await webQueueDb.getAllAsync('SELECT * FROM perf_telemetry');
    assert.equal(afterCapDelete.length, 199);

    // Load telemetry.ts in fresh VM to test parseServerMetrics, offline flush requeue cap, and getSummary P50/P95
    const telFile = path.resolve('utils/telemetry.ts');
    const telMod = {exports: {}};
    vm.runInNewContext(
      ts.transpileModule(fs.readFileSync(telFile, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
      }).outputText,
      {
        module: telMod,
        exports: telMod.exports,
        console,
        performance,
        Date,
        Math,
        Number,
        String,
        Boolean,
        Object,
        Map,
        Set,
        RegExp,
        Error,
        JSON,
        Promise,
      },
      {filename: telFile},
    );
    const {telemetry} = telMod.exports;

    // parseServerMetrics: X-Perf-Metrics JSON, malformed JSON fallback to Server-Timing, and null
    assert.equal(telemetry.parseServerMetrics(null), null);
    assert.equal(telemetry.parseServerMetrics(new Headers()), null);
    const jsonMetrics = telemetry.parseServerMetrics(
      new Headers({
        'X-Perf-Metrics': JSON.stringify({total_ms: 48.4, db_ms: 14.2, app_ms: 34.2, db_calls: 2, n_plus_one: ['commitments']}),
      }),
    );
    assert.deepEqual(plain(jsonMetrics), {total_ms: 48.4, db_ms: 14.2, app_ms: 34.2, db_calls: 2, n_plus_one: ['commitments']});

    const timingMetrics = telemetry.parseServerMetrics(
      new Headers({
        'X-Perf-Metrics': '{invalid-json',
        'Server-Timing': 'total;dur=62.5, db;dur=21.3;desc="5 queries", app;dur=41.2',
      }),
    );
    assert.deepEqual(plain(timingMetrics), {total_ms: 62.5, db_ms: 21.3, db_calls: 5, app_ms: 41.2});

    // Offline flush requeue cap + online recovery
    let offline = true;
    const uploadedBatches = [];
    await telemetry.configure({
      db: webQueueDb,
      uploader: async batch => {
        if (offline) throw new Error('Offline');
        uploadedBatches.push(batch);
      },
    });

    // Record 20 deterministic fetch events [10, 20, ..., 200] (2 failures) for P50/P95 check
    for (let i = 1; i <= 20; i++) {
      telemetry.recordDataFetch({
        path: '/v1/dashboard/overview?period=month',
        durationMs: i * 10,
        status: i === 19 ? 'error' : i === 20 ? 'aborted' : 'ok',
        payloadBytes: 512,
        serverMetrics: {total_ms: 5, db_ms: 2, app_ms: 3, db_calls: 1},
      });
    }
    const summaryBeforeFlood = telemetry.getSummary();
    const dashRow = summaryBeforeFlood.find(r => r.key === 'data_fetch:GET /v1/dashboard/overview');
    assert.ok(dashRow);
    assert.equal(dashRow.count, 20);
    // For n=20 sorted [10..200]: floor(0.5 * 19)=9 -> 100ms; floor(0.95 * 19)=18 -> 190ms; max=200ms; avg=105ms; failures=2
    assert.equal(dashRow.p50_ms, 100);
    assert.equal(dashRow.p95_ms, 190);
    assert.equal(dashRow.max_ms, 200);
    assert.equal(dashRow.avg_ms, 105);
    assert.equal(dashRow.failures, 2);

    // Flood 420 more events while offline -> auto-flushes fail & requeue, bounded by MAX_MEMORY_EVENTS (400)
    for (let i = 0; i < 420; i++) {
      telemetry.record({
        category: 'queue_sync',
        name: 'sync.tick',
        duration_ms: 5,
        status: 'ok',
      });
    }
    await telemetry.flush();
    assert.equal(uploadedBatches.length, 0, 'No batches uploaded while offline');

    // Bring uploader online and drain outbox: total drained events must be <= 400 (bounded cap!)
    offline = false;
    for (let step = 0; step < 25; step++) {
      await telemetry.flush();
    }
    const totalDrained = uploadedBatches.reduce((acc, b) => acc + b.length, 0);
    assert.ok(totalDrained > 0 && totalDrained <= 400, `Expected bounded offline outbox (1..400), got ${totalDrained}`);
  }

  // =========================================================================
  // 2. costControlInsights.ts synthesis fallback (over_pace, attention, stable,
  //    controlling, no_baseline, week/mtd/month, clamped non-negative projection)
  // =========================================================================
  {
    const ccFile = path.resolve('utils/costControlInsights.ts');
    const ccMod = {exports: {}};
    vm.runInNewContext(
      ts.transpileModule(fs.readFileSync(ccFile, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
      }).outputText,
      {module: ccMod, exports: ccMod.exports, Intl, Date, Math, Number, String, Boolean, Array, Object},
      {filename: ccFile},
    );
    const {resolveCostControlView, formatSignedPct, statusPalette} = ccMod.exports;

    assert.equal(formatSignedPct(null), 'sem base');
    assert.equal(formatSignedPct(NaN), 'sem base');
    assert.equal(formatSignedPct(12.34), '+12.3%');
    assert.equal(formatSignedPct(-4.56), '-4.6%');
    assert.equal(statusPalette('controlling').text, '#4FD1B5');
    assert.equal(statusPalette('over_pace').text, '#FF8C83');
    assert.equal(statusPalette('attention').text, '#F4B942');
    assert.equal(statusPalette('stable').text, '#F4B942');
    assert.equal(statusPalette('no_baseline').text, '#AFC1BF');

    // Server cost_control passthrough with status='attention' & week period delegating to monthDashboard
    const attentionModel = resolveCostControlView(
      {period: 'week', expenses: 90},
      {
        period: 'month',
        cost_control: {
          status: 'attention',
          status_label: 'Atenção ao ritmo',
          headline: 'Leve aceleração semanal.',
          mtd: {reference_date: '2026-10-15', day_of_month: 15, days_in_month: 31, current_expenses: 520, buckets: [{key: 'S1', reached: true}]},
          weekly_series: [{key: 'W0', label: 'Atual', period_label: '09–15', current_expenses: 150, previous_expenses: 130, current_income: 0, delta_amount: 20, delta_pct: 15.4}],
          monthly_comparison_series: [{month: '2026-10', label: '10/26', current_expenses: 520, previous_expenses: 500, income: 2000, net: 1480, delta_amount: 20, delta_pct: 4}],
          category_drivers: [{category: 'Transporte', current_amount: 220, previous_amount: 180, delta_amount: 40, delta_pct: 22.2}],
          insights: [{id: 'pace_warn', tone: 'warning', title: 'Transporte subiu', detail: '+22%', metric: '+R$ 40', category: 'Transporte'}],
        },
      },
    );
    assert.equal(attentionModel.status, 'attention');
    assert.equal(attentionModel.category_drivers[0].category, 'Transporte');
    assert.equal(attentionModel.insights[0].category, 'Transporte');

    // Synthesis fallback: no_baseline (prevExp = 0)
    const noBase = resolveCostControlView({
      period_end: '2026-10-15',
      expenses: 300,
      income: 1000,
      comparison: {expenses: 0, income: 0},
      monthly_series: [{month: '2026-09', expenses: 0, income: 0, net: 0}, {month: '2026-10', expenses: 300, income: 1000, net: 700}],
    });
    assert.equal(noBase.status, 'no_baseline');
    assert.equal(noBase.mtd.buckets.filter(b => b.reached).length, 3, 'Day 15 reaches S1, S2, S3');
    assert.equal(noBase.weekly_series.length, 4);
    assert.equal(noBase.monthly_comparison_series.length, 2);

    // Synthesis fallback: controlling (deltaPct <= -3%)
    const controlling = resolveCostControlView({
      period_end: '2026-09-15', // 15/30 = 50% of prev month
      expenses: 350,
      income: 2000,
      comparison: {expenses: 1000, income: 2000}, // prevMtdEst ~ 500 (or 15/31 * 1000 = 483.87 -> -27.7%)
    });
    assert.equal(controlling.status, 'controlling');

    // Synthesis fallback: stable (-3% < deltaPct <= +5%)
    const stable = resolveCostControlView({
      period_end: '2026-04-15', // prev month March has 31 days: prevMtdEst = 1000 * (15/31) = 483.87
      expenses: 490, // +1.3%
      income: 500, // 98% income commitment -> warning tone
      comparison: {expenses: 1000, income: 500},
    });
    assert.equal(stable.status, 'stable');
    assert.equal(stable.insights.find(i => i.id === 'income_commitment')?.tone, 'warning');

    // Synthesis fallback: over_pace (deltaPct > +5%) + bad income share (>100%)
    const overPace = resolveCostControlView({
      period_end: '2026-04-15',
      expenses: 800,
      income: 600,
      comparison: {expenses: 1000, income: 600},
    });
    assert.equal(overPace.status, 'over_pace');
    assert.equal(overPace.insights.find(i => i.id === 'income_commitment')?.tone, 'bad');

    // Refund-clamped zero-expense period keeps projection non-negative
    const refundClamped = resolveCostControlView({
      period_end: '2026-04-10',
      expenses: 0,
      income: 1500,
      comparison: {expenses: 900, income: 1500},
    });
    assert.ok(refundClamped.mtd.projected_month_expenses >= 0, 'Projected month expenses never goes negative');
    assert.equal(refundClamped.mtd.projected_month_expenses, 0);
  }

  // =========================================================================
  // 3. StatementCommitmentLink.tsx (swap, unlink with/without warning, and
  //    page 2 pagination offset=200 exposing BUG-11)
  // =========================================================================
  {
    const native = {
      Platform: {OS: 'android', select: o => o.android ?? o.default},
      StyleSheet: {create: s => s},
      BackHandler: {addEventListener: () => ({remove() {}})},
      Alert: {alert() {}},
      ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable'].map(n => [n, n])),
    };
    const mocks = {
      react: React,
      'react-native': native,
    };
    const cache = new Map();
    function loadComp(file) {
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
          Math,
          Number,
          String,
          Boolean,
          JSON,
          URLSearchParams,
          Symbol,
          Promise,
          setTimeout,
          clearTimeout,
          require: name => {
            if (mocks[name]) return mocks[name];
            if (!name.startsWith('.')) return require(name);
            const target = path.resolve(path.dirname(file), name);
            return loadComp(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
          },
        },
        {filename: file},
      );
      cache.set(file, mod.exports);
      return mod.exports;
    }

    const StatementCommitmentLink = loadComp('components/StatementCommitmentLink.tsx').default;
    const dialogs = loadComp('utils/appAlert.ts');
    const money = v => `R$ ${Number(v || 0).toFixed(2)}`;

    const findButtons = (renderer, label) =>
      renderer.root.findAllByType('Pressable').filter(
        n => n.props.accessibilityLabel === label || n.findAllByType('Text').some(t => [].concat(t.props.children).join('') === label),
      );
    const pressBtn = async (renderer, label) => {
      const found = findButtons(renderer, label);
      assert.ok(found.length > 0, `Expected button "${label}"`);
      await act(async () => {
        const res = found[0].props.onPress();
        if (res && typeof res.then === 'function') await res;
      });
      await settle();
    };
    const confirmDialog = async btnText => {
      const snap = dialogs.dialogSnapshot();
      assert.ok(snap, `Expected dialog for button "${btnText}"`);
      const chosen = snap.buttons.find(b => b.text === btnText);
      assert.ok(chosen, `Expected dialog button "${btnText}" in [${snap.buttons.map(b => b.text).join(', ')}]`);
      await act(async () => { dialogs.dismissDialog(chosen, snap); });
      await settle();
    };

    // 3A. Page-1 happy paths: startSwap -> swap (transaction & confirmed_capture) + unlink (without & with warning)
    const page1Commitment = {
      commitment_id: 'cmt-1',
      name: 'Internet Fibra',
      revision: 4,
      amount: 120,
      status: 'ativo',
      kind: 'assinatura',
      next_due_date: '2026-11-15',
    };
    const linkedTxItem = {
      id: 'tx-old',
      source_token: 'tok-old',
      valor: -120,
      data_evento: '2026-11-14T12:00:00Z',
      merchant_raw: 'OPERADORA FIBRA',
      movement_source: 'ledger',
      commitment: {id: 'cmt-1', name: 'Internet Fibra', kind: 'assinatura'},
    };
    const replacementCandidate = {
      id: 'tx-new-99',
      source_kind: 'transaction',
      source_token: 'tok-new-99',
      merchant_raw: 'FIBRA DEBITO AUTOMATICO',
      valor: -120,
      data_evento: '2026-11-15T10:00:00Z',
      linkable: true,
    };

    const postRequests = [];
    let unlinkWarning = null;
    let unlinkedCalls = 0;

    const page1Api = async (reqPath, init = {}) => {
      if (init.method === 'POST') {
        postRequests.push({path: reqPath, body: JSON.parse(init.body)});
        if (reqPath.endsWith('/payment-unlink')) return {persisted: true, warning: unlinkWarning};
        return {persisted: true};
      }
      if (reqPath === '/v1/commitments') return {items: [page1Commitment], has_more: false};
      if (String(reqPath).includes('/statement-search')) {
        return {items: [replacementCandidate], total: 1, truncated: false};
      }
      return {items: []};
    };

    let renderer;
    dialogs.clearDialogs();
    await act(async () => {
      renderer = create(
        React.createElement(StatementCommitmentLink, {
          item: linkedTxItem,
          api: page1Api,
          money,
          onLinked() {},
          onUnlinked: () => { unlinkedCalls++; },
        }),
      );
    });
    await settle();

    // Swap flow
    await pressBtn(renderer, 'Trocar lançamento');
    assert.ok(findButtons(renderer, 'FIBRA DEBITO AUTOMATICO').length > 0, 'StatementSearch renders replacement candidate');
    await pressBtn(renderer, 'FIBRA DEBITO AUTOMATICO');
    assert.equal(postRequests.length, 1);
    assert.equal(postRequests[0].path, '/v1/commitments/cmt-1/payment-swap');
    assert.equal(postRequests[0].body.expected_revision, 4);
    assert.equal(postRequests[0].body.expected_source_token, 'tok-new-99');
    assert.equal(postRequests[0].body.transaction_id, 'tx-old');
    assert.equal(postRequests[0].body.replacement_transaction_id, 'tx-new-99');
    assert.equal(unlinkedCalls, 1);
    assert.equal(dialogs.dialogSnapshot()?.title, 'Lançamento trocado');
    await confirmDialog('OK');

    // Unlink without warning -> 'Vínculo removido'
    await pressBtn(renderer, 'Desvincular pagamento');
    assert.equal(dialogs.dialogSnapshot()?.title, 'Desvincular pagamento');
    await confirmDialog('Desvincular');
    assert.equal(postRequests.length, 2);
    assert.equal(postRequests[1].path, '/v1/commitments/cmt-1/payment-unlink');
    assert.equal(postRequests[1].body.expected_revision, 4);
    assert.equal(postRequests[1].body.transaction_id, 'tx-old');
    assert.equal(dialogs.dialogSnapshot()?.title, 'Vínculo removido');
    await confirmDialog('OK');

    // Unlink WITH warning -> 'Agenda mantida'
    unlinkWarning = 'Cobrança mais recente manteve o próximo vencimento.';
    await pressBtn(renderer, 'Desvincular pagamento');
    await confirmDialog('Desvincular');
    assert.equal(dialogs.dialogSnapshot()?.title, 'Agenda mantida');
    assert.equal(dialogs.dialogSnapshot()?.message, unlinkWarning);
    await confirmDialog('OK');
    await act(async () => renderer.unmount());

    // 3B. BUG-11 Pagination (>200 commitments, target commitment on page 2 at offset=200)
    const page1_200 = Array.from({length: 200}, (_, idx) => ({
      commitment_id: `cmt-p1-${idx}`,
      name: `Assinatura ${idx}`,
      revision: 1,
      amount: 30,
      status: 'ativo',
      kind: 'assinatura',
      next_due_date: '2026-11-20',
    }));
    const page2Target = {
      commitment_id: 'cmt-page2-205',
      name: 'Condomínio Bloco B',
      revision: 7,
      amount: 650,
      status: 'ativo',
      kind: 'assinatura',
      next_due_date: '2026-11-10',
    };
    const pagedCalls = [];
    const pagedPosts = [];
    const pagedApi = async (reqPath, init = {}) => {
      pagedCalls.push(reqPath);
      if (init.method === 'POST') {
        pagedPosts.push({path: reqPath, body: JSON.parse(init.body)});
        return {persisted: true, warning: null};
      }
      if (reqPath === '/v1/commitments') {
        return {items: page1_200, has_more: true, next_offset: 200};
      }
      if (reqPath === '/v1/commitments?offset=200') {
        return {items: [page2Target], has_more: false};
      }
      if (String(reqPath).includes('/statement-search')) {
        return {items: [replacementCandidate], total: 1, truncated: false};
      }
      return {items: []};
    };

    // Test 1: Linking an unlinked movement to page-2 commitment (offset=200)
    dialogs.clearDialogs();
    const unlinkedMovement = {
      id: 'tx-condo-1',
      source_token: 'tok-condo-1',
      valor: -650,
      data_evento: '2026-11-10T12:00:00Z',
      merchant_raw: 'CONDOMINIO BLOCO B',
      movement_source: 'ledger',
    };
    await act(async () => {
      renderer = create(
        React.createElement(StatementCommitmentLink, {
          item: unlinkedMovement,
          api: pagedApi,
          money,
          onLinked() {},
        }),
      );
    });
    await settle();
    await pressBtn(renderer, 'Vincular a compromisso');
    try {
      assert.ok(
        findButtons(renderer, 'Vincular a Condomínio Bloco B').length > 0,
        'BUG-11: StatementCommitmentLink load() must paginate /v1/commitments?offset=200 so page-2 commitments can be linked',
      );
    } catch (err) {
      bug11Failures.push(err.message);
    }
    await act(async () => renderer.unmount());

    // Test 2: Unlinking & swapping an already-linked movement whose commitment is on page 2 (offset=200)
    dialogs.clearDialogs();
    const linkedPage2Movement = {
      ...unlinkedMovement,
      commitment: {id: 'cmt-page2-205', name: 'Condomínio Bloco B', kind: 'assinatura'},
    };
    await act(async () => {
      renderer = create(
        React.createElement(StatementCommitmentLink, {
          item: linkedPage2Movement,
          api: pagedApi,
          money,
          onLinked() {},
          onUnlinked() {},
        }),
      );
    });
    await settle();

    await pressBtn(renderer, 'Desvincular pagamento');
    await confirmDialog('Desvincular');
    try {
      assert.equal(
        pagedPosts.some(p => p.path === '/v1/commitments/cmt-page2-205/payment-unlink' && p.body.expected_revision === 7),
        true,
        `BUG-11: StatementCommitmentLink unlink() must paginate /v1/commitments?offset=200 instead of failing with "${dialogs.dialogSnapshot()?.message || ''}"`,
      );
    } catch (err) {
      bug11Failures.push(err.message);
    }
    dialogs.clearDialogs();

    await pressBtn(renderer, 'Trocar lançamento');
    try {
      assert.ok(
        findButtons(renderer, 'FIBRA DEBITO AUTOMATICO').length > 0,
        `BUG-11: StatementCommitmentLink startSwap() must paginate /v1/commitments?offset=200 instead of failing with "${dialogs.dialogSnapshot()?.message || ''}"`,
      );
    } catch (err) {
      bug11Failures.push(err.message);
    }
    dialogs.clearDialogs();
    await act(async () => renderer.unmount());
  }

  assert.equal(
    bug11Failures.length,
    0,
    `StatementCommitmentLink pagination contract violations:\n- ${bug11Failures.join('\n- ')}`,
  );

  completed = true;
  if (done) done();
  console.log('PASS StatementCommitmentLink swap/unlink/pagination, webQueueDb perf_telemetry cap, telemetry collector, and costControlInsights.');
})().catch(error => {
  completed = true;
  if (done) done();
  console.error(error);
  process.exitCode = 1;
});
