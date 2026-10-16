const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = path.resolve('utils/userOperation.ts');
const moduleValue = {exports: {}};
const code = ts.transpileModule(fs.readFileSync(source, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText;
vm.runInNewContext(code, {module: moduleValue, exports: moduleValue.exports, console, Symbol, Promise}, {filename: source});
const {runUserAction, afterUserOperation, isUserOperationBusy, isUserInteractionBlocked,
  getUserOperationSnapshot, subscribeUserOperation, setUserDialogOpen} = moduleValue.exports;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
};

(async () => {
  const idle = getUserOperationSnapshot();
  assert.equal(idle, getUserOperationSnapshot(), 'Snapshot stays stable until state changes');
  let changes = 0;
  const unsubscribe = subscribeUserOperation(() => changes++);
  setUserDialogOpen(false);
  assert.equal(changes, 0);

  const first = deferred();
  let attempts = 0;
  const running = runUserAction(() => {
    attempts++;
    assert.equal(isUserOperationBusy(), true, 'Gate is acquired before callback/first await');
    return first.promise;
  }, 'Salvando registro…');
  assert.equal(getUserOperationSnapshot().label, 'Salvando registro…');
  assert.equal(runUserAction(() => attempts++), undefined);
  assert.equal(runUserAction(() => attempts++), undefined);
  assert.equal(attempts, 1, 'Double taps never invoke the competing callback');
  first.resolve('ACK');
  assert.equal(await running, 'ACK');
  assert.equal(isUserOperationBusy(), false);
  assert.equal(attempts, 1, 'Ignored clicks do not replay after completion');

  let immediate = false;
  const beforeSync = changes;
  const syncValue = runUserAction(() => {immediate = true; return 17;});
  assert.equal(syncValue, 17);
  assert.equal(immediate, true);
  assert.equal(isUserOperationBusy(), false, 'Sync navigation releases in the same turn');
  assert.equal(changes, beforeSync, 'Sync selectors/navigation never publish a transient busy overlay');

  const expectedError = new Error('Failed acknowledgement');
  const failure = deferred();
  let observedError;
  const failed = runUserAction(() => failure.promise, 'Salvando…', error => {
    observedError = error;
    assert.equal(isUserOperationBusy(), true, 'Error reporting happens before task release');
    setUserDialogOpen(true);
  });
  failure.reject(expectedError);
  assert.equal(await failed, undefined);
  assert.equal(observedError, expectedError);
  assert.equal(isUserOperationBusy(), false);
  assert.equal(isUserInteractionBlocked(), true, 'Result dialog keeps the interaction gate');
  attempts = 0;
  runUserAction(() => attempts++);
  assert.equal(attempts, 0, 'Actions stay blocked while the result is presented');
  setUserDialogOpen(false);
  assert.equal(isUserInteractionBlocked(), false);

  assert.throws(() => runUserAction(() => {throw expectedError;}), error => error === expectedError);
  assert.equal(isUserOperationBusy(), false, 'Sync throw releases the operation');
  await assert.rejects(runUserAction(() => Promise.reject(expectedError)), error => error === expectedError);
  assert.equal(isUserOperationBusy(), false, 'Rejected promise releases the operation');
  let handled = false;
  runUserAction(() => {throw expectedError;}, undefined, () => {handled = true;});
  assert.equal(handled, true);
  assert.equal(isUserOperationBusy(), false);

  const preview = deferred(), write = deferred(), order = [];
  let writeResult;
  const previewResult = runUserAction(() => preview.promise, 'Conferindo antes de excluir…');
  setUserDialogOpen(true);
  setUserDialogOpen(false);
  afterUserOperation(() => {
    order.push('confirmed');
    writeResult = runUserAction(() => {
      order.push('write');
      return write.promise;
    });
  });
  const cancel = afterUserOperation(() => order.push('cancelled'));
  cancel();
  afterUserOperation(() => order.push('next'));
  assert.equal(order.length, 0, 'Dialog button waits for preview owner to settle');
  preview.resolve();
  await previewResult;
  assert.deepEqual(order, ['confirmed', 'write']);
  assert.equal(isUserOperationBusy(), true, 'Next explicit callback waits for the newly acquired write');
  write.resolve();
  await writeResult;
  assert.deepEqual(order, ['confirmed', 'write', 'next']);
  assert.equal(isUserOperationBusy(), false);

  const background = deferred();
  background.promise.then(() => order.push('background'));
  assert.equal(isUserInteractionBlocked(), false, 'An ordinary background promise does not acquire the gate');
  background.resolve();
  await background.promise;
  afterUserOperation(() => order.push('immediate'));
  assert.equal(order.at(-1), 'immediate', 'Explicit callback executes immediately once idle');
  const before = changes;
  unsubscribe();
  setUserDialogOpen(true);
  assert.equal(changes, before);
  setUserDialogOpen(false);
  console.log('PASS: User operation gate blocks overlap synchronously, preserves result dialogs and releases on every outcome.');
})().catch(error => {console.error(error); process.exitCode = 1;});
