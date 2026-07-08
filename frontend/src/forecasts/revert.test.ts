import { describe, expect, it, vi } from "vitest";

import { runRevert } from "./revert";

describe("runRevert", () => {
  it("surfaces the error and applies no state when the delete fails", async () => {
    // The audit finding: a failed override-delete must NOT look like a
    // successful revert. applyResolved must not run, the error must be
    // shown, and the caller must learn it failed (false).
    const applyResolved = vi.fn();
    const setError = vi.fn();

    const ok = await runRevert({
      deleteOverride: () =>
        Promise.reject(new Error("override delete failed: 500")),
      applyResolved,
      setError,
    });

    expect(ok).toBe(false);
    expect(applyResolved).not.toHaveBeenCalled();
    expect(setError).toHaveBeenNthCalledWith(1, null); // cleared first
    expect(setError).toHaveBeenLastCalledWith("override delete failed: 500");
  });

  it("applies the resolved fit and clears the error on success", async () => {
    const resolved = { source: "global", payload: { qi: 1 } };
    const applyResolved = vi.fn();
    const setError = vi.fn();

    const ok = await runRevert({
      deleteOverride: () => Promise.resolve(resolved),
      applyResolved,
      setError,
    });

    expect(ok).toBe(true);
    expect(applyResolved).toHaveBeenCalledWith(resolved);
    expect(setError).toHaveBeenCalledTimes(1);
    expect(setError).toHaveBeenCalledWith(null);
  });

  it("stringifies non-Error rejections", async () => {
    const setError = vi.fn();
    const ok = await runRevert({
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- exercising the non-Error rejection path on purpose
      deleteOverride: () => Promise.reject("boom"),
      applyResolved: vi.fn(),
      setError,
    });
    expect(ok).toBe(false);
    expect(setError).toHaveBeenLastCalledWith("boom");
  });

  it("does not rethrow (no unhandled rejection reaches the caller)", async () => {
    // The event handler awaits this; it must resolve, not reject.
    await expect(
      runRevert({
        deleteOverride: () => Promise.reject(new Error("x")),
        applyResolved: vi.fn(),
        setError: vi.fn(),
      }),
    ).resolves.toBe(false);
  });
});
