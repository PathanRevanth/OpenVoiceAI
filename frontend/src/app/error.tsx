"use client";

/** Route-level error boundary: catches render/data failures per page segment
  * so one broken console page never blanks the whole app. */

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled UI error:", error);
  }, [error]);

  return (
    <div className="grid min-h-[60dvh] place-items-center px-6">
      <div className="w-full max-w-md rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-8 text-center shadow-[var(--shadow-pop)]">
        <h2 className="text-[15px] font-semibold text-[var(--text-1)]">Something went wrong</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-[var(--text-2)]">
          The page hit an unexpected error. Retry, or head back to the Assistant.
          {error.digest && (
            <span className="mt-2 block font-mono text-[11px] text-[var(--text-3)]">
              ref: {error.digest}
            </span>
          )}
        </p>
        <div className="mt-6 flex items-center justify-center gap-2">
          <button
            onClick={reset}
            className="press fluid rounded-xl bg-[var(--solid)] px-4 py-2 text-[13px] font-semibold text-[var(--on-solid)] hover:opacity-90"
          >
            Try again
          </button>
          <a
            href="/chat"
            className="press fluid rounded-xl border border-[var(--hairline)] bg-[var(--bg-raised)] px-4 py-2 text-[13px] font-medium text-[var(--text-1)] hover:bg-[var(--hover)]"
          >
            Back to Assistant
          </a>
        </div>
      </div>
    </div>
  );
}
