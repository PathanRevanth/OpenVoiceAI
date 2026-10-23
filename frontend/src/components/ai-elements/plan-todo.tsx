"use client";

/**
 * Live plan surface for the chat, animated with Motion (framer-motion).
 *
 * - `PlanStepRow`: one to-do row; the status icon springs between states so
 *   real-time `plan.task` updates pop as delegated subagents progress.
 * - `PlanTodoPanel`: the execution to-do list attached flush to the composer  - 
 *   slides up on a spring once the plan is approved, streams task updates, and
 *   (driven by the parent unmounting it inside `AnimatePresence`) slides away
 *   when the run settles.
 *
 * Pair with the official AI Elements `Plan` card (`@/components/ai-elements/plan`)
 * which renders the plan in the message thread.
 */

import * as React from "react";
import { AnimatePresence, motion } from "motion/react";
import { Icon } from "@/components/icons";
import { cn } from "@/lib/utils";

/** Lifecycle of the orchestrator's plan in the chat. */
export type PlanPhase = "streaming" | "awaiting" | "approved" | "declined" | "completed";

/** One step, mirroring pydantic-ai-harness `PlanItem`. */
export interface PlanStep {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed" | "cancelled" | "blocked";
  active_form?: string;
}

/** Client-side plan state carried alongside the conversation. */
export interface PlanCardState {
  items: PlanStep[];
  phase: PlanPhase;
  approvalId?: string;
}

const spring = { type: "spring" as const, stiffness: 380, damping: 30 };

function StatusIcon({ status }: { status: PlanStep["status"] }) {
  const done = status === "completed" || status === "cancelled";
  const active = status === "in_progress";
  const blocked = status === "blocked";
  return (
    <span className="relative mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center">
      <AnimatePresence mode="popLayout" initial={false}>
        {done ? (
          <motion.span
            key="done"
            initial={{ scale: 0.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.3, opacity: 0 }}
            transition={spring}
            className="absolute grid h-[18px] w-[18px] place-items-center rounded-full bg-[var(--ok-dim)] text-[var(--ok)]"
          >
            <Icon name="check" size={11} strokeWidth={2.2} />
          </motion.span>
        ) : active ? (
          <motion.span
            key="active"
            initial={{ scale: 0.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.3, opacity: 0 }}
            transition={spring}
            className="relative grid h-[18px] w-[18px] place-items-center"
          >
            <span className="animate-live absolute h-[9px] w-[9px] rounded-full bg-[var(--accent)]" />
            <span className="animate-ring absolute inset-0 rounded-full border border-[var(--accent-border)]" />
          </motion.span>
        ) : (
          <motion.span
            key={blocked ? "blocked" : "pending"}
            initial={{ scale: 0.3, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.3, opacity: 0 }}
            transition={spring}
            className={cn(
              "h-[13px] w-[13px] rounded-full border-[1.5px]",
              blocked ? "border-[var(--warn)]" : "border-[var(--hairline-strong)]",
            )}
          />
        )}
      </AnimatePresence>
    </span>
  );
}

/** One to-do row with a spring-animated status icon and live sub-label. */
export function PlanStepRow({ step }: { step: PlanStep }) {
  const done = step.status === "completed" || step.status === "cancelled";
  const active = step.status === "in_progress";
  return (
    <li className="relative flex items-start gap-3 py-1.5">
      <StatusIcon status={step.status} />
      <div className="min-w-0 flex-1">
        <motion.p
          className={cn(
            "fluid text-[13.5px] leading-snug",
            done ? "text-[var(--text-3)] line-through decoration-[var(--hairline-strong)]" : "text-[var(--text-1)]",
          )}
        >
          {step.content}
        </motion.p>
        <AnimatePresence initial={false}>
          {active && step.active_form ? (
            <motion.p
              key="active-form"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden text-[11.5px] leading-snug text-[var(--text-3)]"
            >
              {step.active_form}
            </motion.p>
          ) : null}
        </AnimatePresence>
      </div>
    </li>
  );
}

const PHASE_META: Record<PlanPhase, { label: string; tone: string }> = {
  streaming: { label: "Drafting the plan", tone: "text-[var(--text-3)]" },
  awaiting: { label: "Awaiting approval", tone: "text-[var(--warn)]" },
  approved: { label: "Executing the plan", tone: "text-[var(--accent)]" },
  declined: { label: "Declined  -  revising", tone: "text-[var(--danger)]" },
  completed: { label: "Plan completed", tone: "text-[var(--ok)]" },
};

/**
 * The execution to-do list attached flush to the composer. Render it inside an
 * `AnimatePresence` so unmounting slides it back down.
 */
export function PlanTodoPanel({
  plan,
  className,
}: {
  plan: PlanCardState;
  className?: string;
}) {
  const { phase, items } = plan;
  // "streaming" with steps present means subagents are executing post-approval.
  const executing = phase === "streaming" && items.length > 0;
  const meta = executing
    ? { label: "Executing the plan", tone: "text-[var(--accent)]" }
    : PHASE_META[phase];
  const doneCount = items.filter((s) => s.status === "completed").length;
  const pct = items.length ? (doneCount / items.length) * 100 : 0;

  return (
    <motion.div
      data-slot="plan-todo"
      initial={{ opacity: 0, y: 28, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 28, scale: 0.985 }}
      transition={spring}
      className={cn(
        "composer-glass overflow-hidden rounded-b-none border-b-0",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3 px-4 pb-1 pt-3">
        <p className={cn("text-[12px] font-semibold font-medium", meta.tone)}>
          {meta.label}
          {items.length > 0 ? ` · ${doneCount}/${items.length}` : ""}
        </p>
        {phase === "streaming" && items.length === 0 && (
          <span className="text-[10.5px] text-[var(--text-3)]">thinking…</span>
        )}
      </div>

      {/* live progress bar */}
      <div className="mx-4 h-[2px] overflow-hidden rounded-full bg-[var(--panel-hover)]">
        <motion.div
          className="h-full rounded-full bg-[var(--accent)]"
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={spring}
        />
      </div>

      <ul className="plan-panel-scroll max-h-[168px] overflow-y-auto px-4 pb-2 pt-1">
        {items.length > 0 ? (
          items.map((step, i) => <PlanStepRow key={step.id || `step-${i}`} step={step} />)
        ) : (
          <li className="flex items-center gap-2 py-2 text-[12.5px] text-[var(--text-3)]">
            <span className="animate-live h-1.5 w-1.5 rounded-full bg-[var(--text-3)]" />
            The orchestrator is laying out the steps…
          </li>
        )}
      </ul>
    </motion.div>
  );
}
