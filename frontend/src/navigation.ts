// Navigate by changing the URL hash and ALWAYS dispatching a
// hashchange event manually. Setting ``window.location.hash`` directly
// is unreliable across browsers when the new value is empty or only
// differs by a trailing "#" (Safari sometimes skips the event entirely),
// which lets the route-state hooks fall out of sync with the URL.
// Using history.pushState + a manual dispatch is consistent everywhere.
//
// Shared by App's nav tabs and any in-page action that needs to clear
// a hash route override (e.g. #/type-curves/{id} forces the TC tab —
// a page switch must clear it first or the override swallows the nav).
export function navigateHash(newHash: string): void {
  const base = window.location.pathname + window.location.search;
  const target = newHash ? base + newHash : base;
  window.history.pushState(null, "", target);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
