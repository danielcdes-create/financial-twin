// Guard the public card boundary against accidental disclosure of private fields.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
const sandbox = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('utils/purchaseCard.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, sandbox);
const privateValues = ['SALARIO_PRIVADO', 'SALDO_PRIVADO', 'DIVIDA_PRIVADA', 'LOJA_PRIVADA', 'META_PRIVADA'];
const card = sandbox.exports.purchaseCardLines({
  price: '300.00', workHours: '16.0', delayDays: 30,
  salary: privateValues[0], balance: privateValues[1], debt: privateValues[2],
  product: privateValues[3], goalName: privateValues[4],
}).join('\n');
assert.ok(card.includes('300,00') && card.includes('16,0') && card.includes('30 dias'));
for (const value of privateValues) assert.ok(!card.includes(value));
const incomplete = sandbox.exports.purchaseCardLines({ price: '300.00', workHours: null, delayDays: null }).join('\n');
assert.ok(!incomplete.includes('horas') && !incomplete.includes('dias'));
console.log('Purchase card: private fields excluded; missing facts omitted — passed');
