'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');

const sourceToPath = new Map();
const compiledToPath = new Map();
const wrappedTranspilers = new WeakMap();
let lastReadTsPath = null;

const originalReadFileSync = fs.readFileSync;
fs.readFileSync = function patchedReadFileSync(file, ...rest) {
  const result = originalReadFileSync.call(this, file, ...rest);
  if (typeof result === 'string' && (typeof file === 'string' || file instanceof URL)) {
    const fileStr = file instanceof URL ? file.pathname : file;
    if (/\.(?:tsx?|jsx?|mjs|cjs)$/i.test(fileStr)) {
      const resolved = path.resolve(fileStr);
      sourceToPath.set(result, resolved);
      if (/\.(?:tsx?)$/i.test(resolved)) {
        lastReadTsPath = resolved;
      }
    }
  }
  return result;
};

function wrapTypeScriptModule(tsModule) {
  if (!tsModule || (typeof tsModule !== 'object' && typeof tsModule !== 'function') || typeof tsModule.transpileModule !== 'function') {
    return tsModule;
  }
  if (wrappedTranspilers.has(tsModule)) {
    return wrappedTranspilers.get(tsModule);
  }
  const originalTranspileModule = tsModule.transpileModule.bind(tsModule);
  const patchedTranspileModule = function patchedTranspileModule(input, transpileOptions) {
    const output = originalTranspileModule(input, transpileOptions);
    const explicitFile =
      transpileOptions && typeof transpileOptions.fileName === 'string' && transpileOptions.fileName
        ? path.resolve(transpileOptions.fileName)
        : null;
    const originPath = explicitFile || (typeof input === 'string' ? sourceToPath.get(input) : null) || lastReadTsPath;
    if (originPath && output && typeof output.outputText === 'string') {
      compiledToPath.set(output.outputText, originPath);
    }
    return output;
  };

  const proxy = new Proxy(Object.create(null), {
    get(_target, prop) {
      if (prop === 'transpileModule') return patchedTranspileModule;
      if (prop === 'default' && tsModule.default === tsModule) return proxy;
      return Reflect.get(tsModule, prop, tsModule);
    },
    has(_target, prop) {
      return prop in tsModule;
    },
    ownKeys() {
      return Reflect.ownKeys(tsModule);
    },
    getOwnPropertyDescriptor(_target, prop) {
      const desc = Object.getOwnPropertyDescriptor(tsModule, prop);
      if (!desc) return undefined;
      if (prop === 'transpileModule') {
        return { value: patchedTranspileModule, writable: true, enumerable: desc.enumerable, configurable: true };
      }
      return {
        value: Reflect.get(tsModule, prop, tsModule),
        writable: true,
        enumerable: desc.enumerable,
        configurable: true,
      };
    },
  });

  wrappedTranspilers.set(tsModule, proxy);
  wrappedTranspilers.set(proxy, proxy);
  return proxy;
}

const originalModuleLoad = Module._load;
Module._load = function patchedModuleLoad(request, parent, isMain) {
  const loaded = originalModuleLoad.call(this, request, parent, isMain);
  if (request === 'typescript' || (typeof request === 'string' && /typescript(?:[\\/].*|\.js)?$/i.test(request))) {
    return wrapTypeScriptModule(loaded);
  }
  return loaded;
};

const originalRunInNewContext = vm.runInNewContext;
vm.runInNewContext = function patchedRunInNewContext(code, contextObject, options) {
  const hasFilename =
    (typeof options === 'string' && options.length > 0) ||
    (options !== null && typeof options === 'object' && Boolean(options.filename));
  if (!hasFilename && typeof code === 'string') {
    const inferred = compiledToPath.get(code) || sourceToPath.get(code);
    if (inferred) {
      const nextOptions =
        options !== null && typeof options === 'object' ? { ...options, filename: inferred } : { filename: inferred };
      return originalRunInNewContext.call(this, code, contextObject, nextOptions);
    }
  }
  return originalRunInNewContext.call(this, code, contextObject, options);
};

process.on('unhandledRejection', err => {
  console.error(err);
  process.exit(1);
});

process.on('uncaughtException', err => {
  console.error(err);
  process.exit(1);
});

globalThis.__expectAsyncCompletion = function expectAsyncCompletion(label = 'Async test suite') {
  let completed = false;
  let reported = false;
  process.on('beforeExit', () => {
    if (!completed && !reported) {
      reported = true;
      console.error(`${label}: async assertions did not call done() before process exit.`);
      process.exitCode = 1;
    }
  });
  return () => {
    completed = true;
  };
};
