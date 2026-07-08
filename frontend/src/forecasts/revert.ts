/**
 * Revert-override flow with failure surfacing.
 *
 * Deleting a manual override can fail on the server (4xx/5xx). The modal
 * must not apply the "reverted" state or report success when that
 * happens — otherwise the UI looks reverted while the server still holds
 * the override, and the failure is a silent unhandled rejection.
 *
 * `runRevert` clears the error slot, deletes the override, and only on
 * success applies the resolved fit; on failure it routes the message to
 * the same `setError` slot the modal's save()/toggleLock() use and
 * returns false so the caller can skip the follow-up refetch. It never
 * rethrows.
 *
 * Generic over the resolved payload so this stays free of the modal's
 * concrete forecast types (the component supplies them).
 */
export async function runRevert<T>(deps: {
  deleteOverride: () => Promise<T>;
  applyResolved: (resolved: T) => void;
  setError: (message: string | null) => void;
}): Promise<boolean> {
  deps.setError(null);
  try {
    const resolved = await deps.deleteOverride();
    deps.applyResolved(resolved);
    return true;
  } catch (e) {
    deps.setError(e instanceof Error ? e.message : String(e));
    return false;
  }
}
