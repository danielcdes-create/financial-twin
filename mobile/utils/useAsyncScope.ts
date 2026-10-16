import {useCallback, useLayoutEffect, useRef} from 'react';

// Capture ownership before awaiting. A different editor or unmount invalidates
// UI work, while durable commands and receipts still finish independently.
export function useAsyncScope(owner: unknown = null) {
  const scope = useRef({active: true, epoch: 0});
  useLayoutEffect(() => {
    const current = scope.current;
    current.active = true; current.epoch++;
    return () => {current.active = false; current.epoch++;};
  }, [owner]);
  return useCallback(() => {
    const current = scope.current, epoch = current.epoch;
    return () => current.active && current.epoch === epoch;
  }, []);
}

// For read/analysis screens without a durable acknowledgement journal. Journal
// consumers must validate/finalize the receipt before discarding UI work.
export function useScopedRequest<A extends unknown[], R>(request: (...args: A) => Promise<R>, capture: ReturnType<typeof useAsyncScope>) {
  return useCallback(async (...args: A) => {
    const valid = capture();
    if (!valid()) throw Object.assign(new Error('Atualização interrompida'), {name: 'AbortError'});
    const result = await request(...args);
    if (!valid()) throw Object.assign(new Error('Atualização interrompida'), {name: 'AbortError'});
    return result;
  }, [request, capture]);
}
