import type { telemetry as TelemetryInstance } from './telemetry';

export type UserOperationSnapshot = Readonly<{busy: boolean; label: string; dialogOpen: boolean}>;
type DeferredAction = {action: () => void; cancelled: boolean};

let snapshot: UserOperationSnapshot = Object.freeze({busy: false, label: '', dialogOpen: false});
let active: symbol | null = null;
let draining = false;
const listeners = new Set<() => void>();
const deferred: DeferredAction[] = [];

declare const require: ((id: string) => any) | undefined;

let cachedTelemetry: typeof TelemetryInstance | null | undefined;
const resolveTelemetry = (): typeof TelemetryInstance | null => {
  if (cachedTelemetry !== undefined) return cachedTelemetry ?? null;
  try {
    if (typeof require === 'function') {
      const mod = require('./telemetry');
      const resolved = mod?.telemetry ?? null;
      cachedTelemetry = resolved;
      return resolved;
    }
  } catch {
    // Allow isolated VM unit tests without a module loader to run unchanged.
  }
  cachedTelemetry = null;
  return null;
};

const publish = (busy: boolean, label: string, dialogOpen = snapshot.dialogOpen) => {
  if (snapshot.busy === busy && snapshot.label === label && snapshot.dialogOpen === dialogOpen) return;
  snapshot = Object.freeze({busy, label, dialogOpen});
  listeners.forEach(listener => listener());
};
export const getUserOperationSnapshot = () => snapshot;
export const userOperationSnapshot = getUserOperationSnapshot;
export const subscribeUserOperation = (listener: () => void) => {
  listeners.add(listener);
  return () => {listeners.delete(listener);};
};
export const isUserOperationBusy = () => active !== null;
export const isUserInteractionBlocked = () => active !== null || snapshot.dialogOpen;
export const setUserDialogOpen = (open: boolean) => publish(snapshot.busy, snapshot.label, open);

const drainDeferred = () => {
  if (draining || active !== null) return;
  draining = true;
  try {
    // A callback can acquire the next operation. Stop until it settles rather
    // than executing another dialog callback over its in-flight work.
    while (active === null && deferred.length) {
      const next = deferred.shift()!;
      if (!next.cancelled) next.action();
    }
  } finally {draining = false;}
};
const finish = (identity: symbol) => {
  if (active !== identity) return;
  active = null;
  publish(false, '');
  drainDeferred();
};

/**
 * User-triggered work acquires the gate before invoking its callback, including
 * before the callback's first await. Busy clicks are discarded, never replayed.
 * Sync navigation/selectors release synchronously; async work holds the gate
 * through acknowledgement and local completion. Background reads stay outside.
 * UI callers provide onError; callers without it must handle throw/rejection.
 */
export function runUserAction<T>(action: () => T | PromiseLike<T>, label = 'Processando…',
  onError?: (error: unknown) => void): T | Promise<T | undefined> | undefined {
  const tracker = resolveTelemetry();
  if (isUserInteractionBlocked()) {
    tracker?.trackUserAction(label, () => undefined, true);
    return;
  }
  const identity = Symbol('user-operation');
  active = identity;
  try {
    const result = tracker ? tracker.trackUserAction(label, action, false) : action();
    if (result != null && typeof (result as PromiseLike<T>).then === 'function') {
      // The synchronous part may consume a selector option. Publishing before
      // it runs would let the overlay's busy subscription close that option
      // before its callback reads it. The active gate already rejects taps.
      publish(true, label);
      return Promise.resolve(result).then(value => {
        finish(identity);
        return value;
      }, error => {
        // Present the result dialog while the task still owns the gate. There
        // is no interval in which another action can slip in before the alert.
        try {
          if (onError) {onError(error); return undefined;}
          throw error;
        } finally {finish(identity);}
      });
    }
    finish(identity);
    return result as T;
  } catch (error) {
    try {
      if (onError) {onError(error); return undefined;}
      throw error;
    } finally {finish(identity);}
  }
}

/**
 * Reserve this for an explicit dialog button callback, never ordinary clicks.
 * The dialog is closed before scheduling; wait for its owner task to finish
 * so a confirmation shown during a preview cannot overlap that preview.
 * The callback should use runUserAction with an error handler for async work.
 * Its cancellation handle allows session/dialog cleanup to discard stale work.
 */
export function afterUserOperation(action: () => void): () => void {
  const entry: DeferredAction = {action, cancelled: false};
  if (active === null && !draining) action();
  else {deferred.push(entry); drainDeferred();}
  return () => {entry.cancelled = true;};
}
