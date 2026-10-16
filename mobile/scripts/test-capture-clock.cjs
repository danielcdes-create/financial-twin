const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
const original=process.env.TZ;
try {
  for(const zone of ['UTC','America/Sao_Paulo','Pacific/Honolulu']) {
    process.env.TZ=zone;
    const module={exports:{}};
    const source=ts.transpileModule(fs.readFileSync('utils/classification.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
    vm.runInNewContext(source,{module,exports:module.exports,Date,Intl},{filename:'classification.ts'});
    const {fromBrasiliaInput,toBrasiliaInput,formatBrasilia,financialDateIso}=module.exports;
    for(const [wall,instant,label] of [
      ['2026-10-01T23:40:00','2026-10-02T02:40:00.000Z','01/10/2026 23:40'],
      ['2026-10-01T00:10:00','2026-10-01T03:10:00.000Z','01/10/2026 00:10']
    ]) {
      assert.equal(fromBrasiliaInput(wall),instant,zone);
      assert.equal(toBrasiliaInput(instant),wall,zone);
      assert.equal(formatBrasilia(instant),label,zone);
      assert.equal(financialDateIso(instant),'2026-10-01',zone);
    }
    assert.equal(fromBrasiliaInput('2026-10-02T02:40:00+00:00'),'2026-10-02T02:40:00.000Z');
  }
} finally {if(original===undefined)delete process.env.TZ;else process.env.TZ=original;}
console.log('PASS: Capture wall clock, explicit instants and civil dates stay in Brasília across device timezones.');
