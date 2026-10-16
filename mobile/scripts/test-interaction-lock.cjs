// Real operation store, controls, dialog host and selector. Native events,
// networking and DOM nodes are simulated; this does not prove device layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const {act, create} = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
};
function harness(platform) {
  const cache = new Map(), nativeDialogs = [], backHandlers = [], documentEvents = new Map(), nodes = [];
  let keyboardDismissals = 0;
  const target = {};
  const documentValue = {
    visibilityState: 'visible', activeElement: null,
    addEventListener(name, listener) {const set = documentEvents.get(name) || new Set(); set.add(listener); documentEvents.set(name, set);},
    removeEventListener(name, listener) {documentEvents.get(name)?.delete(listener);},
  };
  const native = {
    Platform: {OS: platform}, AppState: {currentState: 'active', addEventListener: () => ({remove() {}})},
    Keyboard: {dismiss() {keyboardDismissals++;}}, StyleSheet: {create: styles => styles},
    BackHandler: {addEventListener(_name, handler) {backHandlers.push(handler); return {remove() {backHandlers.splice(backHandlers.indexOf(handler), 1);}};}},
    Alert: {alert(title, message, buttons, options) {nativeDialogs.push({title, message, buttons, options});}},
    FlatList: ({data, renderItem}) => React.createElement('View', null,
      data.map(item => React.createElement(React.Fragment, {key: item.value}, renderItem({item})))),
    ...Object.fromEntries(['View', 'Text', 'TextInput', 'Pressable', 'ActivityIndicator', 'Modal', 'ScrollView'].map(name => [name, name])),
  };
  function load(file) {
    file = path.resolve(file);
    if (cache.has(file)) return cache.get(file);
    const m = {exports: {}};
    cache.set(file, m.exports);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true},
    }).outputText;
    vm.runInNewContext(code, {
      module: m, exports: m.exports, console, document: documentValue, Symbol, Promise, Error,
      setTimeout, clearTimeout,
      require(name) {
        if (name === 'react') return React;
        if (name === 'react-native') return native;
        if (name === 'react-native-safe-area-context') return {SafeAreaView: 'View'};
        const targetFile = path.resolve(path.dirname(file), name);
        return load(fs.existsSync(targetFile + '.tsx') ? targetFile + '.tsx' : targetFile + '.ts');
      },
    }, {filename: file});
    cache.set(file, m.exports);
    return m.exports;
  }
  const store = load('utils/userOperation.ts');
  const controls = load('components/InteractionControls.tsx');
  const dialogs = load('utils/appAlert.ts');
  const Boundary = load('components/UserOperationBoundary.tsx').default;
  const DialogHost = load('components/AppDialogHost.tsx').default;
  const ChoiceProvider = load('components/ChoiceOverlay.tsx').ChoiceOverlayProvider;
  const Choice = load('components/ControlledChoice.tsx').default;
  let renderer;
  const mount = async children => act(async () => {
    renderer = create(React.createElement(React.Fragment, null,
      React.createElement(Boundary, null, children), React.createElement(DialogHost)), {
      createNodeMock(element) {
        const node = {inert: false, isConnected: true, contains: eventTarget => eventTarget === target,
          focus() {documentValue.activeElement = node;}, querySelectorAll: () => []};
        nodes.push({element, node});
        return node;
      },
    });
  });
  const nativeButton = label => {
    const button = renderer.root.findAllByType('Pressable').find(node => node.props.accessibilityLabel === label);
    assert.ok(button, `Button: ${label}`);
    return button;
  };
  const dismiss = async (index = 0) => act(async () => {
    // Every entry point, including a late callback delivered before render,
    // discards taps while a task owns the gate. Confirm explicitly once idle.
    const current=dialogs.dialogSnapshot();dialogs.dismissDialog(current.buttons[index],current);
  });
  const unmount = async () => act(async () => {dialogs.clearDialogs(); renderer.unmount();});
  const dispatch = name => {
    const event = {target, prevented: false, stopped: false,
      preventDefault() {this.prevented = true;}, stopImmediatePropagation() {this.stopped = true;}};
    documentEvents.get(name)?.forEach(listener => listener(event));
    return event;
  };
  return {store, controls, dialogs, ChoiceProvider, Choice, mount, nativeButton, dismiss, unmount, dispatch,
    nativeDialogs, backHandlers, nodes, target, get renderer() {return renderer;},
    get keyboardDismissals() {return keyboardDismissals;}};
}

async function check(platform) {
  const h = harness(platform), {Pressable, TextInput} = h.controls;
  const save = deferred();
  let writes = 0, navigation = 0, textChanges = 0, submits = 0;
  await h.mount(React.createElement('View', null,
    React.createElement(Pressable, {accessibilityLabel: 'Save', onPress: () => {writes++; return save.promise;}}, 'Save'),
    React.createElement(Pressable, {accessibilityLabel: 'Navigate', onPress: () => {navigation++;}}, 'Navigate'),
    React.createElement(TextInput, {accessibilityLabel: 'Editor', onChangeText: () => textChanges++, onSubmitEditing: () => submits++}),
  ));
  const queuedSave = h.nativeButton('Save').props.onPress;
  const queuedNavigation = h.nativeButton('Navigate').props.onPress;
  const queuedInput = h.renderer.root.findByType('TextInput').props;
  act(() => {
    queuedSave({}); queuedSave({}); queuedNavigation({});
    queuedInput.onChangeText('late'); queuedInput.onSubmitEditing({});
  });
  assert.equal(writes, 1, `${platform}: duplicate tap is rejected synchronously`);
  assert.equal(navigation, 0, `${platform}: queued navigation never runs`);
  assert.equal(textChanges, 0, `${platform}: stale keyboard callback cannot mutate the draft`);
  assert.equal(submits, 0, `${platform}: keyboard submit cannot start another action`);
  assert.equal(h.renderer.root.findByType('TextInput').props.editable, false);
  assert.ok(h.keyboardDismissals > 0);
  if (platform === 'web') {
    for (const name of ['click', 'pointerdown', 'keydown', 'submit']) {
      const event = h.dispatch(name);
      assert.ok(event.prevented && event.stopped, `Pending work blocks ${name}`);
    }
    assert.ok(h.nodes.some(row => row.element.props.pointerEvents !== undefined && row.node.inert));
  } else assert.equal(h.backHandlers.at(-1)(), true, 'Hardware back is consumed during work');
  await act(async () => save.resolve());
  assert.equal(h.store.isUserInteractionBlocked(), false);
  assert.equal(writes, 1, 'Ignored actions never replay');
  assert.equal(navigation, 0);
  act(() => queuedNavigation({}));
  assert.equal(navigation, 1);
  await h.unmount();

  const failed = deferred();
  await h.mount(React.createElement(Pressable, {accessibilityLabel: 'Fail', onPress: () => failed.promise}, 'Fail'));
  act(() => {h.nativeButton('Fail').props.onPress({});});
  await act(async () => failed.reject(new Error('Synthetic save failure')));
  assert.equal(h.store.isUserOperationBusy(), false);
  assert.equal(h.store.isUserInteractionBlocked(), true, 'Failure dialog owns the gate after task rejection');
  assert.match(h.dialogs.dialogSnapshot().message, /Synthetic save failure/);
  await h.dismiss();
  assert.equal(h.store.isUserInteractionBlocked(), false);
  await h.unmount();

  const success = deferred();
  let successClicks = 0;
  await h.mount(React.createElement('View', null,
    React.createElement(Pressable, {accessibilityLabel: 'Success', onPress: () => success.promise.then(() => {
      h.dialogs.Alert.alert('Saved', 'Confirmed by server');
    })}, 'Success'),
    React.createElement(Pressable, {accessibilityLabel: 'Other', onPress: () => {successClicks++;}}, 'Other'),
  ));
  act(() => {h.nativeButton('Success').props.onPress({});});
  await act(async () => success.resolve());
  assert.equal(h.store.isUserOperationBusy(), false);
  assert.equal(h.store.isUserInteractionBlocked(), true);
  act(() => h.nativeButton('Other').props.onPress({}));
  assert.equal(successClicks, 0, 'Only result dialog buttons remain actionable');
  await h.dismiss();
  assert.equal(h.store.isUserInteractionBlocked(), false);
  await h.unmount();

  const preview = deferred(), deleteResult = deferred();
  let deletions = 0;
  await h.mount(React.createElement(Pressable, {accessibilityLabel: 'Preview', onPress: () => {
    h.dialogs.Alert.alert('Confirm deletion?', 'Preview is still finishing', [{text: 'Delete', onPress: () => {
      deletions++; return deleteResult.promise;
    }}]);
    return preview.promise;
  }}, 'Preview'));
  act(() => {h.nativeButton('Preview').props.onPress({});});
  await h.dismiss();
  assert.equal(deletions, 0, 'Early confirmation callback is discarded while preview is pending');
  await act(async () => preview.resolve());
  assert.equal(deletions, 0, 'The ignored tap is never replayed');
  await h.dismiss();
  assert.equal(deletions, 1, 'A new explicit confirmation starts once idle');
  assert.equal(h.store.isUserOperationBusy(), true, 'Confirmed action acquires its own gate');
  await act(async () => deleteResult.resolve());
  assert.equal(h.store.isUserInteractionBlocked(), false, 'Preview-confirmation flow cannot deadlock');
  await h.unmount();

  const owner = deferred(), secondWrite = deferred();
  let callbackCalls = 0;
  await h.mount(React.createElement(Pressable, {accessibilityLabel: 'Queue confirmations', onPress: () => {
    h.dialogs.Alert.alert('First', '', [{text: 'Continue', onPress: () => {callbackCalls++; return secondWrite.promise;}}]);
    h.dialogs.Alert.alert('Second', 'Queued result');
    return owner.promise;
  }}, 'Queue confirmations'));
  act(() => {h.nativeButton('Queue confirmations').props.onPress({});});
  await h.dismiss();
  await act(async () => owner.resolve());
  assert.equal(callbackCalls, 0, 'Busy confirmation is not queued');
  await h.dismiss();
  assert.equal(callbackCalls, 1, 'Next dialog never blocks the current explicit callback from acquiring its task');
  assert.equal(h.store.isUserOperationBusy(), true);
  assert.equal(h.dialogs.dialogSnapshot().title, 'Second');
  await h.dismiss();
  assert.equal(h.store.isUserOperationBusy(), true, 'Dismissing another result cannot release an active task');
  await act(async () => secondWrite.resolve());
  assert.equal(h.dialogs.dialogSnapshot().title, 'Second', 'The ignored dismissal leaves its result available');
  await h.dismiss();
  assert.equal(h.store.isUserInteractionBlocked(), false);
  await h.unmount();

  const staleOwner = deferred();
  let staleCalls = 0;
  await h.mount(React.createElement(Pressable, {accessibilityLabel: 'Stale confirmation', onPress: () => {
    h.dialogs.Alert.alert('Stale', '', [{text: 'Continue', onPress: () => {staleCalls++;}}]);
    return staleOwner.promise;
  }}, 'Stale confirmation'));
  act(() => {h.nativeButton('Stale confirmation').props.onPress({});});
  await h.dismiss();
  act(() => {h.dialogs.clearDialogs();});
  await act(async () => staleOwner.resolve());
  assert.equal(staleCalls, 0, 'A session/dialog reset discards deferred confirmation callbacks');
  assert.equal(h.store.isUserInteractionBlocked(), false);
  await h.unmount();

  if (platform === 'android') {
    let nativeCallbacks = 0;
    await h.mount(React.createElement('View'));
    act(() => {h.dialogs.Alert.alert('Native focus', '', [{text: 'Continue', onPress: () => {nativeCallbacks++;}}],{inActivity:false});});
    const currentNative = h.nativeDialogs.at(-1);
    act(() => {h.dialogs.clearDialogs({preserveNative: true});});
    assert.equal(h.dialogs.dialogSnapshot().title, 'Native focus', 'Expected Android Alert blur preserves its active dialog');
    await act(async () => {currentNative.buttons[0].onPress();});
    assert.equal(nativeCallbacks, 1, 'Native dialog callback remains available after its expected focus transfer');
    act(() => {h.dialogs.Alert.alert('Expired', '', [{text: 'Continue', onPress: () => {nativeCallbacks++;}}],{inActivity:false});});
    const expiredNative = h.nativeDialogs.at(-1);
    act(() => {h.dialogs.clearDialogs();});
    await act(async () => {expiredNative.buttons[0].onPress(); expiredNative.options.onDismiss();});
    assert.equal(nativeCallbacks, 1, 'Real session cleanup rejects late native button callbacks');
    assert.equal(h.store.isUserInteractionBlocked(), false);
    await h.unmount();
  }

  let chosen = '';
  await h.mount(React.createElement(h.ChoiceProvider, null,
    React.createElement(h.Choice, {label: 'Category', value: '', options: ['A', 'B'], onChange: value => {chosen = value;}}),
  ));
  act(() => h.nativeButton('Selecionar Category').props.onPress({}));
  act(() => h.nativeButton('Escolher A').props.onPress({}));
  assert.equal(chosen, 'A', 'Sync busy acquisition cannot erase a selector option before selection');
  assert.equal(h.store.isUserInteractionBlocked(), false);
  await h.unmount();
  console.log(`PASS: ${platform} real controls, dialogs, selector and boundary prevent overlapping actions and retain required callbacks.`);
}

(async () => {await check('android'); await check('web');})()
  .catch(error => {console.error(error); process.exitCode = 1;});
