const {spawnSync} = require('node:child_process');
const {existsSync, mkdtempSync, readdirSync, rmSync} = require('node:fs');
const {homedir, tmpdir} = require('node:os');
const {delimiter, join} = require('node:path');

function hasCommand(cmd) {
  const probe = process.platform === 'win32' ? 'where' : 'which';
  const res = spawnSync(probe, [cmd], {stdio: 'ignore'});
  return !res.error && res.status === 0;
}

function findJarRecursive(dir) {
  if (!existsSync(dir)) return null;
  for (const entry of readdirSync(dir, {withFileTypes: true})) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      const found = findJarRecursive(full);
      if (found) return found;
    } else if (entry.name.endsWith('.jar') && !entry.name.endsWith('-sources.jar') && !entry.name.endsWith('-javadoc.jar')) {
      return full;
    }
  }
  return null;
}

function findJsonJar() {
  const gradleJsonDir = join(homedir(), '.gradle', 'caches', 'modules-2', 'files-2.1', 'org.json', 'json');
  const fromGradle = findJarRecursive(gradleJsonDir);
  if (fromGradle) return fromGradle;
  const m2JsonDir = join(homedir(), '.m2', 'repository', 'org', 'json', 'json');
  return findJarRecursive(m2JsonDir);
}

const outDir = mkdtempSync(join(tmpdir(), 'fin-notification-'));
try {
  if (process.platform === 'win32') {
    const result = spawnSync(
      'powershell',
      ['-NoProfile', '-File', join(__dirname, 'test-notification-native.ps1'), '-OutputDirectory', outDir],
      {stdio: 'inherit'},
    );
    if (result.error) throw result.error;
    process.exitCode = result.status || 0;
  } else {
    const hasKotlinc = hasCommand('kotlinc');
    const hasJava = hasCommand('java');
    const jsonJar = findJsonJar();
    if (!hasKotlinc || !hasJava || !jsonJar) {
      console.log(
        `SKIP compiled Kotlin fixtures: requires kotlinc (${hasKotlinc}), java (${hasJava}), and org.json jar (${Boolean(jsonJar)}) on non-Windows host.`,
      );
      process.exitCode = 0;
    } else {
      const fixturesDir = join(__dirname, 'fixtures', 'notification-inbox');
      const nativeDir = join(
        __dirname,
        '..',
        'modules',
        'notification-capture',
        'android',
        'src',
        'main',
        'java',
        'expo',
        'modules',
        'notificationcapture',
      );
      const fixtureSources = readdirSync(fixturesDir)
        .filter(name => name.endsWith('.kt'))
        .sort()
        .map(name => join(fixturesDir, name));
      const nativeSources = [
        join(nativeDir, 'NotificationCaptureModule.kt'),
        join(nativeDir, 'NotificationCaptureService.kt'),
        join(nativeDir, 'NotificationInbox.kt'),
      ];
      const probeJar = join(outDir, 'inbox-probe.jar');
      const compile = spawnSync(
        'kotlinc',
        ['-include-runtime', '-cp', jsonJar, '-d', probeJar, ...fixtureSources, ...nativeSources],
        {stdio: 'inherit'},
      );
      if (compile.error) throw compile.error;
      if (compile.status !== 0) {
        process.exitCode = compile.status || 1;
      } else {
        const run = spawnSync('java', ['-cp', `${probeJar}${delimiter}${jsonJar}`, 'InboxProbeKt'], {stdio: 'inherit'});
        if (run.error) throw run.error;
        process.exitCode = run.status || 0;
      }
    }
  }
} finally {
  rmSync(outDir, {recursive: true, force: true});
}
