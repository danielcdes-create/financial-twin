const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const {compileModsAsync} = require('expo/config-plugins');
const withNotificationCapture = require('../plugins/withNotificationCapture.js');

let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'Async audit assertions did not finish');
});

const intentGlobal = {};
const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  const moduleObject = {exports: {}};
  const sandbox = {
    module: moduleObject,
    exports: moduleObject.exports,
    Intl,
    Date,
    Math,
    Number,
    String,
    Error,
    JSON,
    URL,
    Promise,
    globalThis: intentGlobal,
    require: name => {
      if (!name.startsWith('.')) throw new Error('Dependência inesperada: ' + name);
      const target = path.resolve(path.dirname(file), name);
      return load(fs.existsSync(target + '.ts') ? target + '.ts' : target);
    },
  };
  vm.runInNewContext(compiled, sandbox, {filename: file});
  cache.set(file, moduleObject.exports);
  return moduleObject.exports;
}

const presentation = load('utils/commitmentPresentation.ts');
const money = load('utils/moneyInput.ts');
const operation = load('utils/userOperation.ts');
const movements = load('utils/movementFilters.ts');
const queue = load('utils/queueFinalization.ts');
const nativeIntent = load('app/+native-intent.ts');

assert.equal(presentation.DEFAULT_COMMITMENT_CUTOFF, '2026-10-01');
presentation.setPresentationClock('2026-12-15');
assert.equal(presentation.commitmentCutoff(), '2026-12-01');
assert.equal(presentation.occurrenceState('2026-09-30', false), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-10-10', false), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-11-10', false, '2026-10-15'), 'Pago (assumido)');
assert.equal(presentation.occurrenceState('2026-12-10', false), 'Vencido · sem vínculo');
assert.equal(presentation.occurrenceState('2026-09-30', true), 'Pago vinculado');
assert.equal(presentation.reconcileCharge('2026-12-10', 'Vencido · sem vínculo', '2026-12-15'), true);
assert.equal(presentation.reconcileCharge('2026-10-10', 'Pago (assumido)', '2026-12-15'), false);
assert.equal(presentation.reconcileCharge('2026-11-10', 'Previsto', '2026-10-15'), false);
const partial = presentation.pendingOccurrences('managed', {
  status: 'ativo', kind: 'assinatura', frequency: 'mensal', renewal_status: 'automatica',
  next_due_date: '2026-12-10', amount: 40, linked_transactions: [],
  installment_overrides: {'2026-12-10': {amount: '15.00', due_date: '2026-12-12', cancelled: false}, '2027-01-10': {amount: '40.00', cancelled: true}},
}, '2026-12-15');
assert.equal(partial[0].due, '2026-12-12');
assert.equal(partial[0].amount, 15);
assert.equal(partial.some(row => row.due === '2027-01-10'), false);
assert.equal(presentation.obligationShare(100, 3, 1), 33.33);
assert.equal(presentation.obligationShare(100, 3, 2), 33.33);
assert.equal(presentation.obligationShare(100, 3, 3), 33.34);
assert.equal(presentation.anchoredDue({next_due_date: '2026-01-31', frequency: 'mensal', schedule_anchor_day: 31}, 1), '2026-02-28');
assert.equal(presentation.anchoredDue({next_due_date: '2026-01-31', frequency: 'mensal', schedule_anchor_day: 31}, 2), '2026-03-31');
assert.equal(presentation.paymentMethodLabel('cartao'), 'Cartão de crédito');
assert.equal(presentation.paymentMethodLabel('cartao_credito'), 'Cartão de crédito');
assert.equal(presentation.paymentMethodLabel('codigo_desconhecido'), '');
const octoberUtc = new Date('2026-10-01T02:30:00Z');
presentation.setPresentationClock(null);
assert.equal(presentation.civilToday(octoberUtc), '2026-09-30');
const month = presentation.civilMonthWindow(octoberUtc);
assert.equal(month.monthStart, '2026-09-01');
assert.equal(month.monthEnd, '2026-09-30');

assert.equal(money.parseMoneyInput('1.234,56'), '1234.56');
assert.equal(money.parseMoneyInput('1234.56'), '1234.56');
assert.equal(money.parseMoneyInput('1.2,34'), null);
assert.equal(money.parseMoneyInput('1e2'), null);
assert.equal(money.parseMoneyInput('-10,00'), null);
assert.equal(money.parseMoneyInput('-10,00', {signed: true}), '-10.00');

assert.equal(movements.classificationLine({natureza: 'COMPRA', categoria: 'Outros', subcategoria: 'Doação', metodo_pagamento: 'cartao_credito'}), 'Compras e Consumo · Outros › Doação · Cartão de crédito');
assert.equal(movements.classificationLine({natureza: 'TRANSFERENCIA', categoria: 'Outros', subcategoria: 'Doação', metodo_pagamento: 'cartao_credito'}), 'Transferência Neutra · Outros › Doação · Cartão de crédito');
assert.equal(movements.ownAccountTransfer({natureza: 'TRANSFERENCIA', vendedor: 'Instituição', subcategoria: 'Doação'}), false);
assert.equal(movements.ownAccountTransfer({natureza: 'TRANSFERENCIA', transfer: {from_account_key: 'origem', to_account_key: 'destino'}}), true);
const plan = movements.parcelamentoPurchaseSummary({parcelamento_purchase: true, valor: -25, parcelamento: {count: 4, total: 100}});
assert.equal(plan.count, 4);
assert.equal(plan.total, 100);
assert.equal(movements.parcelamentoListMark({parcelamento_purchase: true, installment_count: 3, valor: -25, parcelamento: {count: 4, total: 100}}), 'parcela 1/4');

async function verifyNotificationCapturePlugin() {
  const savedApiUrl = process.env.EXPO_PUBLIC_API_BASE_URL;
  const savedReleaseCfg = process.env.FT_RELEASE_CONFIG_FILE;
  const baseManifest = '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:name=".MainApplication" android:usesCleartextTraffic="true"></application></manifest>';
  const baseGradle = [
    'android {',
    '    signingConfigs {',
    '        debug {',
    "            storeFile file('debug.keystore')",
    "            storePassword 'android'",
    "            keyAlias 'androiddebugkey'",
    "            keyPassword 'android'",
    '        }',
    '    }',
    '    buildTypes {',
    '        release {',
    '            // Caution! In production, you need to generate your own keystore file.',
    '            signingConfig signingConfigs.debug',
    '        }',
    '    }',
    '}',
  ].join('\n');

  const evaluateReleaseConfig = (projectRoot, envVars) => {
    const releaseConfigPath = envVars.FT_RELEASE_CONFIG_FILE || null;
    let releaseConfigJson = null;
    if (releaseConfigPath && fs.existsSync(releaseConfigPath)) {
      releaseConfigJson = JSON.parse(fs.readFileSync(releaseConfigPath, 'utf8'));
    }
    const releaseStorePath = envVars.FT_RELEASE_STORE_FILE || (releaseConfigJson ? releaseConfigJson.store_file : null);
    const releaseStorePass = envVars.FT_RELEASE_STORE_PASSWORD || (releaseConfigJson ? releaseConfigJson.store_password : null);
    const releaseKeyAlias = envVars.FT_RELEASE_KEY_ALIAS || (releaseConfigJson ? releaseConfigJson.key_alias : null);
    const releaseKeyPass = envVars.FT_RELEASE_KEY_PASSWORD || (releaseConfigJson ? releaseConfigJson.key_password : null);
    const resolvedStore = releaseStorePath
      ? (path.isAbsolute(releaseStorePath) ? releaseStorePath : path.join(projectRoot, 'android', 'app', releaseStorePath))
      : null;
    return Boolean(resolvedStore && fs.existsSync(resolvedStore) && releaseStorePass && releaseKeyAlias && releaseKeyPass);
  };

  const runPluginInTempRoot = async ({apiUrl, withReleaseConfig}) => {
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'fin-plugin-audit-'));
    try {
      const mainDir = path.join(tempRoot, 'android', 'app', 'src', 'main');
      const appDir = path.join(tempRoot, 'android', 'app');
      fs.mkdirSync(mainDir, {recursive: true});
      fs.writeFileSync(path.join(mainDir, 'AndroidManifest.xml'), baseManifest);
      fs.writeFileSync(path.join(appDir, 'build.gradle'), baseGradle);

      if (apiUrl === undefined) delete process.env.EXPO_PUBLIC_API_BASE_URL;
      else process.env.EXPO_PUBLIC_API_BASE_URL = apiUrl;

      let cfgFile;
      if (withReleaseConfig) {
        const keystoreFile = path.join(tempRoot, 'release.keystore');
        fs.writeFileSync(keystoreFile, 'synthetic-keystore-bytes');
        cfgFile = path.join(tempRoot, 'release-signing.json');
        fs.writeFileSync(cfgFile, JSON.stringify({
          store_file: keystoreFile,
          store_password: 'synthetic-store-pass',
          key_alias: 'synthetic-key',
          key_password: 'synthetic-key-pass',
        }));
        process.env.FT_RELEASE_CONFIG_FILE = cfgFile;
      } else {
        delete process.env.FT_RELEASE_CONFIG_FILE;
      }

      const config = withNotificationCapture({name: 'Financial Twin', slug: 'financial-twin'});
      await compileModsAsync(config, {projectRoot: tempRoot, platforms: ['android']});
      // Re-run to verify idempotency of service & signing block injection
      await compileModsAsync(withNotificationCapture({name: 'Financial Twin', slug: 'financial-twin'}), {projectRoot: tempRoot, platforms: ['android']});

      const manifestXml = fs.readFileSync(path.join(mainDir, 'AndroidManifest.xml'), 'utf8');
      const gradleText = fs.readFileSync(path.join(appDir, 'build.gradle'), 'utf8');
      const hasPrivateKey = evaluateReleaseConfig(tempRoot, process.env);
      return {manifestXml, gradleText, hasPrivateKey};
    } finally {
      fs.rmSync(tempRoot, {recursive: true, force: true});
    }
  };

  try {
    const withoutReleaseKey = await runPluginInTempRoot({apiUrl: 'https://private.example', withReleaseConfig: false});
    assert.doesNotMatch(withoutReleaseKey.manifestXml, /android:usesCleartextTraffic="true"/, 'Release HTTPS config must disable cleartext traffic');
    assert.equal(
      (withoutReleaseKey.manifestXml.match(/expo\.modules\.notificationcapture\.NotificationCaptureService/g) || []).length,
      1,
      'NotificationCaptureService registered exactly once in AndroidManifest',
    );
    assert.match(withoutReleaseKey.manifestXml, /android:permission="android\.permission\.BIND_NOTIFICATION_LISTENER_SERVICE"/);
    assert.match(withoutReleaseKey.manifestXml, /android\.service\.notification\.NotificationListenerService/);
    assert.match(withoutReleaseKey.gradleText, /FT_RELEASE_CONFIG_FILE/);
    assert.match(withoutReleaseKey.gradleText, /signingConfig hasPrivateReleaseKey \? signingConfigs\.release : signingConfigs\.debug/);
    assert.equal(withoutReleaseKey.hasPrivateKey, false, 'Without FT_RELEASE_CONFIG_FILE, release falls back cleanly');

    const withReleaseKey = await runPluginInTempRoot({apiUrl: undefined, withReleaseConfig: true});
    assert.doesNotMatch(withReleaseKey.manifestXml, /android:usesCleartextTraffic="true"/, 'Default release manifest must not enable cleartext traffic');
    assert.match(withReleaseKey.gradleText, /storeFile file\(releaseStorePath\)/);
    assert.equal(withReleaseKey.hasPrivateKey, true, 'With FT_RELEASE_CONFIG_FILE + keystore present, private release key activates');
  } finally {
    if (savedApiUrl === undefined) delete process.env.EXPO_PUBLIC_API_BASE_URL;
    else process.env.EXPO_PUBLIC_API_BASE_URL = savedApiUrl;
    if (savedReleaseCfg === undefined) delete process.env.FT_RELEASE_CONFIG_FILE;
    else process.env.FT_RELEASE_CONFIG_FILE = savedReleaseCfg;
  }
}

const stored = [];
const db = {
  async getAllAsync() { return stored.map(row => ({...row})); },
  async runAsync(sql, ...params) {
    if (sql.startsWith('DELETE FROM event_queue')) {
      const [eventId, kind, localUri, payload, filename, mime, id] = params;
      const index = stored.findIndex(row => row.event_id === eventId && row.kind === kind && row.local_uri === localUri && row.payload === payload && row.filename === filename && row.mime_type === mime && row.id === id);
      if (index >= 0) stored.splice(index, 1);
    }
    assert.doesNotMatch(sql, /transactions|provisional/);
  },
};
const failed = {id: 7, event_id: 'evt-1', kind: 'file', local_uri: 'file://recibo.jpg', payload: '{}', filename: 'recibo.jpg', mime_type: 'image/jpeg'};
stored.push({...failed});
void (async () => {
  await queue.finalizeQueueRow(db, {...failed, payload: '{"alterado":true}'}, () => {}).then(() => { throw new Error('identidade diferente deveria falhar'); }, error => {
    assert.match(error.message, /preservado/);
  });
  assert.equal(stored.length, 1);
  await queue.finalizeQueueRow(db, failed, () => {});
  assert.equal(stored.length, 0);
  const pending = operation.runUserAction(async () => {
    void new Promise(() => {});
    return 'ack';
  });
  assert.equal(operation.userOperationSnapshot().busy, true);
  assert.equal(await pending, 'ack');
  assert.equal(operation.userOperationSnapshot().busy, false);
  const app = fs.readFileSync('App.tsx', 'utf8');
  const refresh = app.slice(app.indexOf('const refreshAfterMutation'), app.indexOf('const refreshAfterMutation') + 700);
  assert.match(refresh, /void Promise\.all/);
  assert.match(refresh, /return Promise\.resolve\(\)/);
  const board = fs.readFileSync('components/CommitmentBoard.tsx', 'utf8');
  assert.match(board, /Editar parcelas futuras/);
  assert.match(board, /source="managed"/);

  const shareRoute = await nativeIntent.redirectSystemPath({path: 'financialtwin://expo-sharing?source=gallery', initial: true});
  assert.equal(shareRoute, '/handle-share');
  assert.equal(intentGlobal.__financialTwinPendingShare, true);
  assert.equal(intentGlobal.__financialTwinShareSeq, 1);
  const secondShareRoute = await nativeIntent.redirectSystemPath({path: 'financialtwin://expo-sharing?source=camera', initial: false});
  assert.equal(secondShareRoute, '/handle-share');
  assert.equal(intentGlobal.__financialTwinShareSeq, 2);
  assert.equal(await nativeIntent.redirectSystemPath({path: '/account', initial: false}), '/account');

  await verifyNotificationCapturePlugin();
  completed = true;
  console.log('Adversarial client probes passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
