"use client";

/**
 * App-wide toasts without prop drilling: call notify("Saved") anywhere and
 * the Toaster mounted in AppShell renders it bottom-center for 3 seconds.
 */

export function notify(message: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<string>("vf:toast", { detail: message }));
}

export function subscribeToasts(handler: (message: string) => void): () => void {
  const onToast = (e: Event) => handler((e as CustomEvent<string>).detail);
  window.addEventListener("vf:toast", onToast);
  return () => window.removeEventListener("vf:toast", onToast);
}
