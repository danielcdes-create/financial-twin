const assert = require('node:assert/strict'), fs = require('node:fs'), crypto = require('node:crypto');
const forge = require('node-forge');
const pkg = require('node-forge/package.json');
assert.equal(pkg.version, '1.4.3', 'Expected upstream patched node-forge 1.4.3');
const code = fs.readFileSync(require.resolve('node-forge/lib/rsa'), 'utf8').replace(/\r\n/g, '\n');
assert.equal(crypto.createHash('sha256').update(code).digest('hex'), '6632e195295ce76f6bb9131ee770dcd6cc1ee4ffac7906302208b00faf95a87f');
const keys = crypto.generateKeyPairSync('rsa', {modulusLength: 1024, publicExponent: 65537, privateKeyEncoding: {type: 'pkcs1', format: 'pem'}, publicKeyEncoding: {type: 'pkcs1', format: 'pem'}});
const privateKey = forge.pki.privateKeyFromPem(keys.privateKey), publicKey = forge.pki.publicKeyFromPem(keys.publicKey);
const md = forge.md.sha256.create(); md.update('synthetic-signature-test'); const digest = md.digest().getBytes();
assert.equal(publicKey.verify(digest, privateKey.sign(md)), true);
assert.equal(publicKey.verify(digest, privateKey.sign(forge.md.sha256.create().update('different'))), false);
const a = forge.asn1;
const create = (type, constructed, value) => a.create(a.Class.UNIVERSAL, type, constructed, value);
for (const withNull of [true, false]) {
  const algorithm = [create(a.Type.OID, false, a.oidToDer(forge.pki.oids.sha256).getBytes())];
  if (withNull) algorithm.push(create(a.Type.NULL, false, ''));
  algorithm.push(create(a.Type.OCTETSTRING, false, 'nested-unconsumed-garbage'));
  const info = create(a.Type.SEQUENCE, true, [create(a.Type.SEQUENCE, true, algorithm), create(a.Type.OCTETSTRING, false, digest)]);
  const signature = privateKey.sign(a.toDer(info).getBytes(), 'NONE');
  assert.throws(() => publicKey.verify(digest, signature));
}
console.log('RSA: upstream node-forge 1.4.3 verified; valid signature accepted; tampered digest and nested DigestInfo garbage rejected.');
