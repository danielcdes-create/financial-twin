import assert from 'node:assert/strict';
import test from 'node:test';
import { createQueueSync, nextQueueAttempt, permanentQueueError } from './syncQueue.ts';

const row = id => ({ id, event_id: `event-${id}`, kind: 'event', payload: '{}', local_uri: null, filename: null, mime_type: null });

test('a permanently rejected row is isolated and the next row is sent', async () => {
  const rows = [row(1), row(2)];
  const sent = [];
  const failures = [];
  const sync = createQueueSync({
    prepare: async () => true,
    list: async () => rows.filter(item => !failures.some(entry => entry.id === item.id && entry.quarantine)),
    send: async item => {
      sent.push(item.id);
      if (item.id === 1) throw Object.assign(new Error('invalid event'), { status: 422 });
      return { persisted: true, event_id: item.event_id };
    },
    remove: async item => { rows.splice(rows.indexOf(item), 1); },
    recordFailure: async (item, error, attempts, nextAttemptAt, quarantine) => { failures.push({ id: item.id, error, attempts, nextAttemptAt, quarantine }); },
    done: () => {},
    changed: () => {},
  });
  await sync();
  assert.deepEqual(sent, [1, 2]);
  assert.deepEqual(rows.map(item => item.id), [1]);
  assert.equal(failures[0].quarantine, true);
  assert.equal(failures[0].attempts, 1);
});

test('transient failures back off with bounded jitter', () => {
  assert.equal(permanentQueueError({ status: 503 }), false);
  assert.equal(permanentQueueError({ status: 410 }), true);
  assert.equal(permanentQueueError({ status: 409, retryable: true }), false);
  assert.equal(permanentQueueError({ status: 409, retryable: false }), true);
  const low = Date.parse(nextQueueAttempt(1, 1000, () => 0)) - 1000;
  const high = Date.parse(nextQueueAttempt(1, 1000, () => 1)) - 1000;
  assert.equal(low, 3750);
  assert.equal(high, 6250);
});
