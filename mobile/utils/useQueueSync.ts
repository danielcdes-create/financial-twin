import { useState } from "react";
import { createQueueSync, QueueRow } from "./syncQueue";

type QueueSyncOptions = {
  prepare: () => Promise<boolean>;
  list: () => Promise<QueueRow[]>;
  send: (row: QueueRow) => Promise<any>;
  remove: (row: QueueRow) => Promise<void>;
  getAcknowledgement?: (row: QueueRow) => Promise<any | null>;
  acknowledge?: (row: QueueRow, result: any) => Promise<void>;
  recordFailure: (row: QueueRow, error: string, attempts: number, nextAttemptAt: string, quarantine: boolean) => Promise<void>;
  done: (row: QueueRow, result: any) => void | Promise<void>;
  changed: (count: number, error: string) => void;
};

export function useQueueSync(options: QueueSyncOptions) {
  const [sync] = useState(() => createQueueSync(options));
  return sync;
}
