// Reference data only. The API client owns this memory cache and clears it on session change.
export class ReferenceCache<T> {
  private generation = 0;
  private value?: { data: T; expiresAt: number };
  constructor(private ttlMs = 300_000) {}
  invalidate() {
    this.generation++;
    this.value = undefined;
  }
  async get(load: (generation: number) => Promise<T>): Promise<T> {
    if (this.value && this.value.expiresAt > Date.now()) return this.value.data;
    const generation = this.generation;
    // The HTTP client coalesces reads and tracks cancellation per consumer.
    const data = await load(generation);
    if (generation !== this.generation) return this.get(load);
    this.value = { data, expiresAt: Date.now() + this.ttlMs };
    return data;
  }
}
