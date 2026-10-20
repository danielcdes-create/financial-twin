import {useCallback, useLayoutEffect, useRef} from 'react';

type ScopedValidator = (() => boolean) & {
  registerController?: (controller: AbortController) => () => void;
};

// Capture ownership before awaiting. A different editor or unmount invalidates
// UI work and aborts active read controllers, while durable commands and
// receipts still finish independently.
export function useAsyncScope(owner: unknown = null) {
  const scope = useRef<{active: boolean; epoch: number; controllers: Set<AbortController>}>({
    active: true,
    epoch: 0,
    controllers: new Set(),
  });
  useLayoutEffect(() => {
    const current = scope.current;
    current.active = true;
    current.epoch++;
    return () => {
      current.active = false;
      current.epoch++;
      for (const controller of current.controllers) {
        try {
          controller.abort();
        } catch {}
      }
      current.controllers.clear();
    };
  }, [owner]);
  return useCallback((): ScopedValidator => {
    const current = scope.current, epoch = current.epoch;
    const valid: ScopedValidator = () => current.active && current.epoch === epoch;
    valid.registerController = (controller: AbortController) => {
      if (!valid()) {
        try {
          controller.abort();
        } catch {}
        return () => {};
      }
      current.controllers.add(controller);
      return () => {
        current.controllers.delete(controller);
      };
    };
    return valid;
  }, []);
}

// For read/analysis screens without a durable acknowledgement journal. Journal
// consumers must validate/finalize the receipt before discarding UI work.
export function useScopedRequest<A extends unknown[], R>(request: (...args: A) => Promise<R>, capture: ReturnType<typeof useAsyncScope>) {
  return useCallback(async (...args: A) => {
    const valid = capture();
    if (!valid()) throw Object.assign(new Error('Atualização interrompida'), {name: 'AbortError'});
    let release = () => {};
    let callArgs = args;
    if (typeof AbortController !== 'undefined' && args.length >= 1 && typeof args[0] === 'string') {
      const init = (args[1] ?? undefined) as RequestInit | undefined;
      const method = (init?.method || 'GET').toUpperCase();
      if (method === 'GET' && !init?.signal && valid.registerController) {
        const controller = new AbortController();
        release = valid.registerController(controller);
        callArgs = [args[0], {...(init || {}), signal: controller.signal}, ...args.slice(2)] as unknown as A;
      }
    }
    try {
      const result = await request(...callArgs);
      if (!valid()) throw Object.assign(new Error('Atualização interrompida'), {name: 'AbortError'});
      return result;
    } finally {
      release();
    }
  }, [request, capture]);
}
