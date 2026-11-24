// P0 runtime contract suite for mobile/components/AppUpdate.tsx (exposes BUG-04).
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');

global.IS_REACT_ACT_ENVIRONMENT = true;
const done = typeof globalThis.__expectAsyncCompletion === 'function'
  ? globalThis.__expectAsyncCompletion('test-app-update-security')
  : null;
let completed = false;
process.on('beforeExit', () => {
  assert.equal(completed, true, 'test-app-update-security async suite did not finish');
});

const platform = {OS: 'android'};
const alerts = [];
const shareCalls = [];
const openUrls = [];
const installerIntents = [];

let sharingAvailable = true;
let nativeDownloadEnabled = true;
let nativeInstallerEnabled = true;
let downloadedBytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0xde, 0xad, 0xbe, 0xef]);
const deletedFiles = [];

class MockFile {
  constructor(dirOrUri, name) {
    const base = typeof dirOrUri === 'string' ? dirOrUri : (dirOrUri?.uri || 'file:///cache');
    this.uri = name ? `${base.replace(/\/$/, '')}/${name}` : base;
    this.contentUri = this.uri.replace(/^file:\/\/\//, 'content://com.financialtwin.app.FileSystemFileProvider/');
    this.exists = false;
  }
  delete() {
    this.exists = false;
    deletedFiles.push(this.uri);
  }
  async bytes() {
    return downloadedBytes;
  }
}

const fileSystemMock = {
  get File() {
    if (!nativeDownloadEnabled) return {};
    return Object.assign(MockFile, {
      downloadFileAsync: async (_url, dest) => {
        dest.exists = true;
        return dest;
      },
    });
  },
  get Paths() {
    return nativeDownloadEnabled ? {cache: {uri: 'file:///data/user/0/com.financialtwin.app/cache'}} : {};
  },
  getContentUriAsync: async uri => String(uri).replace(/^file:\/\/\//, 'content://com.financialtwin.app.FileSystemFileProvider/'),
};

const linkingMock = {
  openURL: async url => {
    openUrls.push(url);
    return true;
  },
  canOpenURL: async () => true,
  sendIntent: async (action, extras) => {
    if (!nativeInstallerEnabled) throw new Error('Installer indisponível');
    const extraMap = Array.isArray(extras)
      ? Object.fromEntries(extras.filter(e => e && e.key).map(e => [e.key, e.value]))
      : (extras && typeof extras === 'object' ? extras : {});
    installerIntents.push({
      source: 'Linking.sendIntent',
      action,
      data: extraMap.data || extraMap.uri,
      type: extraMap.type || extraMap.mimeType,
      extras,
    });
  },
};

const intentLauncherMock = {
  get startActivityAsync() {
    if (!nativeInstallerEnabled) return undefined;
    return async (action, params = {}) => {
      installerIntents.push({
        source: 'expo-intent-launcher',
        action,
        data: params.data,
        type: params.type,
        flags: params.flags,
      });
      return {resultCode: 0};
    };
  },
};

const recordNativeInstall = async (uri, mimeType = 'application/vnd.android.package-archive') => {
  installerIntents.push({
    source: 'expo-notification-capture',
    action: 'android.intent.action.VIEW',
    data: String(uri).startsWith('content://')
      ? String(uri)
      : String(uri).replace(/^file:\/\/\//, 'content://com.financialtwin.app.FileSystemFileProvider/'),
    type: mimeType,
  });
};

const notificationCaptureMock = {
  available: true,
  get installApk() {
    return nativeInstallerEnabled ? recordNativeInstall : undefined;
  },
  get installPackage() {
    return nativeInstallerEnabled ? recordNativeInstall : undefined;
  },
  get openApkInstaller() {
    return nativeInstallerEnabled ? recordNativeInstall : undefined;
  },
};

const sharingMock = {
  isAvailableAsync: async () => sharingAvailable,
  shareAsync: async (uri, opts) => {
    shareCalls.push({uri, opts});
  },
};

const native = {
  Platform: platform,
  StyleSheet: {create: styles => styles},
  Linking: linkingMock,
  Alert: {alert: (title, message) => alerts.push({title, message})},
  ...Object.fromEntries(['View', 'Text', 'Pressable', 'TextInput'].map(name => [name, name])),
};

const mocks = {
  react: React,
  'react-native': native,
  'expo-application': {
    nativeBuildVersion: '132',
    nativeApplicationVersion: '1.20.86',
  },
  'expo-file-system': fileSystemMock,
  'expo-linking': linkingMock,
  'expo-sharing': sharingMock,
  'expo-intent-launcher': intentLauncherMock,
  'expo-notification-capture': {__esModule: true, default: notificationCaptureMock, ...notificationCaptureMock},
  'expo-modules-core': {
    requireNativeModule: name => {
      if (name === 'NotificationCapture' && nativeInstallerEnabled) return notificationCaptureMock;
      throw new Error(`Unavailable module: ${name}`);
    },
  },
  '../utils/appAlert': {
    Alert: {
      alert: (title, message) => alerts.push({title, message: String(message || '')}),
    },
  },
  'expo-secure-store': {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 7,
    canUseBiometricAuthentication: () => false,
    getItemAsync: async () => null,
    setItemAsync: async () => {},
    deleteItemAsync: async () => {},
  },
  'expo-local-authentication': {
    SecurityLevel: {BIOMETRIC_STRONG: 3},
    hasHardwareAsync: async () => false,
    isEnrolledAsync: async () => false,
    getEnrolledLevelAsync: async () => 0,
  },
};

const cache = new Map();
function load(file) {
  file = path.resolve(file);
  if (cache.has(file)) return cache.get(file);
  const m = {exports: {}};
  cache.set(file, m.exports);
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
      module: m,
      exports: m.exports,
      console,
      Error,
      Date,
      Math,
      Number,
      String,
      Boolean,
      Uint8Array,
      Uint32Array,
      BigInt,
      JSON,
      URL,
      Symbol,
      Promise,
      setTimeout,
      clearTimeout,
      globalThis: {crypto: crypto.webcrypto},
      require: name => {
        if (mocks[name]) return mocks[name];
        if (name.endsWith('.json')) return JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), name), 'utf8'));
        if (!name.startsWith('.')) return require(name);
        const target = path.resolve(path.dirname(file), name);
        return load(fs.existsSync(target + '.tsx') ? target + '.tsx' : fs.existsSync(target + '.ts') ? target + '.ts' : target);
      },
    },
    {filename: file},
  );
  cache.set(file, m.exports);
  return m.exports;
}

const AppUpdate = load('components/AppUpdate.tsx').default;

const settle = (ms = 20) => act(async () => { await new Promise(resolve => setTimeout(resolve, ms)); });
const textOf = renderer =>
  renderer.root
    .findAllByType('Text')
    .map(n => [].concat(n.props.children).flat(Infinity).filter(v => v !== null && v !== undefined && v !== false).join(''))
    .join('\n');
const pressButton = async (renderer, label) => {
  const btn = renderer.root.findAllByType('Pressable').find(
    n => n.props.accessibilityLabel === label || n.findAllByType('Text').some(t => [].concat(t.props.children).join('') === label),
  );
  assert.ok(btn, `Expected button "${label}"`);
  assert.ok(!btn.props.disabled, `Expected button "${label}" to be enabled`);
  await act(async () => {
    await btn.props.onPress();
  });
  await settle();
};

const sha256Hex = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function resetCalls() {
  alerts.length = 0;
  shareCalls.length = 0;
  openUrls.length = 0;
  installerIntents.length = 0;
  deletedFiles.length = 0;
}

(async () => {
  let renderer = null;
  const bug04Failures = [];

  try {
    // 1. Status states: Nenhum pacote verificado disponível, Aplicativo atualizado, Versão X disponível
    let manifestResponse = {available: false};
    const api = async endpoint => {
      assert.equal(endpoint, '/v1/app-update');
      return manifestResponse;
    };

    platform.OS = 'android';
    await act(async () => {
      renderer = create(React.createElement(AppUpdate, {api, baseUrl: 'https://private.example'}));
    });
    await settle();
    assert.match(textOf(renderer), /Versão instalada: 1\.20\.86 \(132\)/);
    assert.match(textOf(renderer), /Nenhum pacote verificado disponível/);

    manifestResponse = {available: true, version_code: 132, version_name: '1.20.86'};
    await pressButton(renderer, 'Verificar novamente');
    assert.match(textOf(renderer), /Aplicativo atualizado/);

    const validApkBytes = new Uint8Array(Buffer.from('PK\x03\x04synthetic-signed-apk-payload-v133', 'utf8'));
    const validSha = sha256Hex(validApkBytes);

    manifestResponse = {
      available: true,
      version_code: 133,
      version_name: '1.20.87',
      sha256: validSha,
      size_bytes: validApkBytes.byteLength,
      download_url: '/v1/app-update/apk',
    };
    await pressButton(renderer, 'Verificar novamente');
    assert.match(textOf(renderer), /Versão 1\.20\.87 disponível/);
    assert.match(textOf(renderer), /Baixar atualização/);

    // 2. Rejects missing/invalid SHA-256 or size_bytes <= 0
    for (const badPatch of [
      {sha256: '', label: 'missing sha256'},
      {sha256: 'not-a-64-char-hex-digest', label: 'malformed sha256'},
      {size_bytes: 0, label: 'zero size_bytes'},
      {size_bytes: -50, label: 'negative size_bytes'},
    ]) {
      resetCalls();
      manifestResponse = {...manifestResponse, ...badPatch};
      await pressButton(renderer, 'Baixar atualização');
      assert.equal(alerts.length, 1, `Expected alert for ${badPatch.label}`);
      assert.match(alerts[0].message, /Manifesto de atualização incompleto/);
      assert.equal(openUrls.length, 0);
      assert.equal(shareCalls.length, 0);
      assert.equal(installerIntents.length, 0);
      manifestResponse = {
        available: true,
        version_code: 133,
        version_name: '1.20.87',
        sha256: validSha,
        size_bytes: validApkBytes.byteLength,
        download_url: '/v1/app-update/apk',
      };
    }

    // 3. Rejects non-HTTPS non-loopback baseUrl or download_url
    await act(async () => renderer.unmount());
    renderer = null;

    resetCalls();
    await act(async () => {
      renderer = create(React.createElement(AppUpdate, {api, baseUrl: 'http://100.70.1.2:8000'}));
    });
    await settle();
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 1, 'Cleartext non-loopback baseUrl must be rejected');
    assert.match(alerts[0].message, /HTTPS/i);
    assert.equal(openUrls.length, 0);
    await act(async () => renderer.unmount());
    renderer = null;

    resetCalls();
    manifestResponse = {...manifestResponse, download_url: 'http://private.example/v1/app-update/apk'};
    await act(async () => {
      renderer = create(React.createElement(AppUpdate, {api, baseUrl: 'https://private.example'}));
    });
    await settle();
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 1, 'Cleartext download_url must be rejected');
    assert.match(alerts[0].message, /HTTPS/i);
    assert.equal(openUrls.length, 0);

    // 4. Rejects cross-origin download_url
    resetCalls();
    manifestResponse = {...manifestResponse, download_url: 'https://attacker.example/v1/app-update/apk'};
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 1, 'Cross-origin download_url must be blocked');
    assert.match(alerts[0].message, /Origem de download divergente bloqueada por segurança/);
    assert.equal(openUrls.length, 0);

    // 5. Rejects tampered/corrupted APK (SHA-256 mismatch & size mismatch) and deletes staged file
    resetCalls();
    downloadedBytes = new Uint8Array(Buffer.from('PK\x03\x04TAMPERED-APK-BYTES-SAME-LEN!!----', 'utf8'));
    manifestResponse = {
      available: true,
      version_code: 133,
      version_name: '1.20.87',
      sha256: validSha,
      size_bytes: validApkBytes.byteLength,
      download_url: '/v1/app-update/apk',
    };
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 1, 'Tampered SHA-256 must alert');
    assert.match(alerts[0].message, /A verificação SHA-256 do APK falhou/);
    assert.ok(deletedFiles.some(u => u.endsWith('financial-twin-133.apk')), 'Corrupted staged APK must be deleted immediately');
    assert.equal(openUrls.length, 0);
    assert.equal(shareCalls.length, 0);
    assert.equal(installerIntents.length, 0);

    resetCalls();
    downloadedBytes = validApkBytes;
    manifestResponse = {...manifestResponse, size_bytes: validApkBytes.byteLength + 4};
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 1, 'Byte-length mismatch must alert');
    assert.match(alerts[0].message, /A verificação SHA-256 do APK falhou/);
    assert.ok(deletedFiles.some(u => u.endsWith('financial-twin-133.apk')), 'Size-mismatched staged APK must be deleted');
    assert.equal(openUrls.length, 0);
    await act(async () => renderer.unmount());
    renderer = null;

    // 6. Web platform: verified private targetUrl opens via Linking.openURL
    resetCalls();
    platform.OS = 'web';
    manifestResponse = {
      available: true,
      version_code: 133,
      version_name: '1.20.87',
      sha256: validSha,
      size_bytes: validApkBytes.byteLength,
      download_url: '/v1/app-update/apk',
    };
    await act(async () => {
      renderer = create(React.createElement(AppUpdate, {api, baseUrl: 'https://private.example'}));
    });
    await settle();
    assert.match(textOf(renderer), /Versão web: 1\.20\.86 \(132\)/);
    await pressButton(renderer, 'Baixar atualização');
    assert.equal(alerts.length, 0, 'Web verified private download should not alert');
    assert.deepEqual(openUrls, ['https://private.example/v1/app-update/apk']);
    await act(async () => renderer.unmount());
    renderer = null;

    // 7. BUG-04 Contract A: Android verified APK launches ACTION_VIEW package installer with content:// URI (never Sharing.shareAsync ACTION_SEND)
    resetCalls();
    platform.OS = 'android';
    nativeDownloadEnabled = true;
    nativeInstallerEnabled = true;
    sharingAvailable = true;
    downloadedBytes = validApkBytes;
    await act(async () => {
      renderer = create(React.createElement(AppUpdate, {api, baseUrl: 'https://private.example'}));
    });
    await settle();
    await pressButton(renderer, 'Baixar atualização');

    try {
      assert.equal(shareCalls.length, 0, 'BUG-04: Android update must NOT invoke Sharing.shareAsync (ACTION_SEND)');
      const contentUrlOpens = openUrls.filter(u => String(u).startsWith('content://'));
      const httpsUrlOpens = openUrls.filter(u => !String(u).startsWith('content://'));
      assert.equal(httpsUrlOpens.length, 0, 'BUG-04: Android update must NOT open remote HTTPS URL in browser');
      const validViewIntent = installerIntents.some(
        it =>
          it.action === 'android.intent.action.VIEW' &&
          typeof it.data === 'string' &&
          it.data.startsWith('content://') &&
          it.type === 'application/vnd.android.package-archive',
      );
      assert.equal(
        validViewIntent,
        true,
        `BUG-04: Expected ACTION_VIEW intent with content:// URI and application/vnd.android.package-archive, got intents=${JSON.stringify(installerIntents)} openUrls=${JSON.stringify(contentUrlOpens)}`,
      );
    } catch (err) {
      bug04Failures.push(err.message);
    }

    // 8. BUG-04 Contract B1: When native File.downloadFileAsync is unavailable on Android, must error safely (never Linking.openURL(targetUrl))
    resetCalls();
    nativeDownloadEnabled = false;
    await pressButton(renderer, 'Baixar atualização');
    try {
      assert.equal(
        openUrls.length,
        0,
        `BUG-04: When File.downloadFileAsync is unavailable on Android, must NOT fall through to unverified Linking.openURL(${JSON.stringify(openUrls)})`,
      );
      assert.equal(alerts.length, 1, 'BUG-04: Unavailable Android native file staging must surface a safe error alert');
    } catch (err) {
      bug04Failures.push(err.message);
    }

    // 9. BUG-04 Contract B2: When native installer handoff is unavailable on Android, must error safely (never Linking.openURL(targetUrl))
    resetCalls();
    nativeDownloadEnabled = true;
    nativeInstallerEnabled = false;
    sharingAvailable = false;
    await pressButton(renderer, 'Baixar atualização');
    try {
      assert.equal(
        openUrls.length,
        0,
        `BUG-04: When Android installer handoff is unavailable, must NOT fall through to unverified Linking.openURL(${JSON.stringify(openUrls)})`,
      );
      assert.equal(alerts.length, 1, 'BUG-04: Unavailable Android installer handoff must surface a safe error alert');
    } catch (err) {
      bug04Failures.push(err.message);
    }

    await act(async () => renderer.unmount());
    renderer = null;

    assert.equal(
      bug04Failures.length,
      0,
      `AppUpdate security installer contract violations:\n- ${bug04Failures.join('\n- ')}`,
    );

    completed = true;
    if (done) done();
    console.log('PASS AppUpdate manifest, private transport, same-origin, SHA-256 verification, Web openURL, and Android ACTION_VIEW installer contracts.');
  } finally {
    if (renderer) {
      await act(async () => renderer.unmount());
      renderer = null;
    }
  }
})().catch(error => {
  completed = true;
  if (done) done();
  console.error(error);
  process.exitCode = 1;
});
