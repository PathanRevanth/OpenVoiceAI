"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { authClient } from "@/lib/auth-client";
import { Icon } from "@/components/icons";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") {
        const { error } = await authClient.signUp.email({
          email,
          password,
          name: name || email.split("@")[0],
        });
        if (error) throw new Error(error.message ?? "Sign up failed");
      } else {
        const { error } = await authClient.signIn.email({ email, password });
        if (error) throw new Error(error.message ?? "Sign in failed");
      }
      router.push("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative z-[2] grid min-h-dvh place-items-center p-5">
      <div className=" grid w-full max-w-3xl overflow-hidden rounded-[28px] border border-[var(--hairline)] bg-[var(--bg-raised)] shadow-[var(--shadow-pop)] md:grid-cols-[1.05fr_1fr]">
        {/* brand panel */}
        <div className="relative hidden flex-col justify-between p-9 md:flex">
          <div
            className="absolute inset-0 -z-10"
            style={{
              background:
                "radial-gradient(26rem 20rem at 8% 0%, rgba(79,70,229,0.09), transparent 62%)",
            }}
          />
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-[var(--accent)]">
              <Icon name="waveform" size={15} strokeWidth={2} className="text-[var(--accent-contrast)]" />
            </span>
            <span className="text-[13.5px] font-semibold tracking-tight">Voice-First AI</span>
          </div>

          <div>
            <p className="text-[11px] font-medium text-[var(--text-3)]">Agentic control plane</p>
            <h1 className="mt-4 text-[30px] font-semibold leading-[1.12] tracking-tight text-balance">
              Speak. The fleet
              <br />
              does the rest.
            </h1>
            <p className="mt-4 max-w-[30ch] text-[13px] leading-relaxed text-[var(--text-2)]">
              One orchestrator, specialist agents, and human approval before
              anything consequential. Built on Pydantic AI v2.
            </p>
          </div>

          <div className="flex items-center gap-4 text-[10.5px] text-[var(--text-3)]">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" /> Pydantic AI v2
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" /> MCP
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" /> A2A-ready
            </span>
          </div>
        </div>

        {/* form panel */}
        <div className="border-t border-[var(--hairline)] p-8 md:border-l md:border-t-0 md:p-9">
          <div className="mb-7 flex items-center justify-between">
            <h2 className="text-[16px] font-semibold tracking-tight">
              {mode === "signin" ? "Sign in" : "Create account"}
            </h2>
            <div className="flex rounded-lg border border-[var(--hairline)] bg-[var(--panel)] p-0.5 text-[11.5px]">
              {(["signin", "signup"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`fluid rounded-md px-2.5 py-1 ${
                    mode === m
                      ? "bg-[var(--panel-hover)] font-medium text-[var(--text-1)]"
                      : "text-[var(--text-3)] hover:text-[var(--text-2)]"
                  }`}
                >
                  {m === "signin" ? "Sign in" : "Sign up"}
                </button>
              ))}
            </div>
          </div>

          <form onSubmit={submit} className="space-y-3.5">
            {mode === "signup" && (
              <Field label="Name">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Ada Lovelace"
                  autoComplete="name"
                  className={inputCls}
                />
              </Field>
            )}
            <Field label="Email">
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                required
                placeholder="you@company.com"
                autoComplete="email"
                className={inputCls}
              />
            </Field>
            <Field label="Password">
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                required
                minLength={8}
                placeholder="At least 8 characters"
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                className={inputCls}
              />
            </Field>

            {error && (
              <p className="rounded-xl border border-[var(--danger)]/30 bg-[var(--danger-dim)] px-3.5 py-2.5 text-[12px] text-[var(--danger)]">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="press fluid flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[var(--accent)] text-[13px] font-semibold text-[var(--accent-contrast)] hover:bg-[var(--accent-strong)] disabled:opacity-50"
            >
              {busy && (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-white/40 border-t-white" />
              )}
              {busy ? "One moment…" : mode === "signin" ? "Sign in" : "Create account"}
            </button>
          </form>

          <p className="mt-6 text-center text-[11px] leading-relaxed text-[var(--text-3)]">
            New accounts start with the viewer role.
            <br />
            Admins grant ops and admin from the Better Auth user table.
          </p>
        </div>
      </div>
    </main>
  );
}

const inputCls =
  "fluid h-10 w-full rounded-xl border border-[var(--hairline)] bg-[var(--panel)] px-3.5 text-[13px] outline-none placeholder:text-[var(--text-3)] hover:border-[var(--hairline-strong)] focus:border-[var(--accent-border)]";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11.5px] font-medium text-[var(--text-2)]">{label}</span>
      {children}
    </label>
  );
}
