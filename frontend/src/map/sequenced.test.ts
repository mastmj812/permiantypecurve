import { describe, it, expect } from "vitest";

import { latestWins } from "./sequenced";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("latestWins", () => {
  it("drops a stale result that resolves after a newer call started", async () => {
    // Reproduces the lasso race: lasso A (slow) then lasso B (fast). B
    // resolves first, then A resolves last. Without the guard A would
    // clobber B; with it, A is dropped and B's selection wins.
    const committed: string[] = [];
    const run = latestWins<string>((v) => committed.push(v));
    const a = deferred<string>();
    const b = deferred<string>();

    const pA = run(() => a.promise); // generation 1 (slow lasso)
    const pB = run(() => b.promise); // generation 2 (fast lasso)

    b.resolve("lasso-B");
    await pB;
    a.resolve("lasso-A");
    await pA;

    expect(committed).toEqual(["lasso-B"]);
  });

  it("commits every result when calls resolve in order", async () => {
    const committed: string[] = [];
    const run = latestWins<string>((v) => committed.push(v));

    await run(() => Promise.resolve("one"));
    await run(() => Promise.resolve("two"));

    expect(committed).toEqual(["one", "two"]);
  });

  it("commits the latest even if an earlier call never resolves", async () => {
    const committed: string[] = [];
    const run = latestWins<string>((v) => committed.push(v));

    const stuck = deferred<string>();
    void run(() => stuck.promise); // generation 1, never resolves
    await run(() => Promise.resolve("latest")); // generation 2

    expect(committed).toEqual(["latest"]);
  });

  it("does not commit when produce rejects", async () => {
    const committed: string[] = [];
    const run = latestWins<string>((v) => committed.push(v));

    await expect(
      run(() => Promise.reject(new Error("network"))),
    ).rejects.toThrow("network");
    expect(committed).toEqual([]);
  });
});
