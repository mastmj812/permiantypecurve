/**
 * Guards an async selection channel against out-of-order resolution.
 *
 * Map selection is fired from user gestures (lasso, box-drag, click) that
 * each kick off an async request to the backend. If the user draws a
 * second lasso before the first request resolves, the two responses can
 * come back in any order — and if the slower (older) one lands last it
 * clobbers the fresh selection with stale wells (a well visibly
 * highlights, then silently drops).
 *
 * `latestWins` wraps the commit so only the most recently started call is
 * allowed to apply its result. Each invocation opens a new generation;
 * when its `produce()` resolves, `onResult` fires ONLY if no newer
 * invocation has started since. Errors from `produce()` propagate to the
 * caller (which keeps its own try/catch for logging) and never commit.
 */
export function latestWins<T>(
  onResult: (value: T) => void,
): (produce: () => Promise<T>) => Promise<void> {
  let generation = 0;
  return async (produce) => {
    const mine = ++generation;
    const value = await produce();
    if (mine !== generation) return; // a newer call superseded this one
    onResult(value);
  };
}
