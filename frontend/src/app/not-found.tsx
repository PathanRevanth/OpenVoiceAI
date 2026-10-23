import Link from "next/link";

export default function NotFound() {
  return (
    <div className="grid min-h-[60dvh] place-items-center px-6">
      <div className="w-full max-w-md rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-8 text-center shadow-[var(--shadow-pop)]">
        <p className="font-mono text-[11px] font-semibold font-medium text-[var(--text-3)]">
          404
        </p>
        <h2 className="mt-2 text-[15px] font-semibold text-[var(--text-1)]">Page not found</h2>
        <p className="mt-2 text-[13px] text-[var(--text-2)]">
          That route does not exist in this workspace.
        </p>
        <Link
          href="/chat"
          className="press fluid mt-6 inline-block rounded-xl bg-[var(--solid)] px-4 py-2 text-[13px] font-semibold text-[var(--on-solid)] hover:opacity-90"
        >
          Back to Assistant
        </Link>
      </div>
    </div>
  );
}
