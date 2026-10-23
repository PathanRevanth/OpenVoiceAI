"use client";

/**
 * VoiceOrb — the voice interaction affordance, built on a waveform motif.
 *
 * Idle: white disc with three static waveform bars.
 * Active: indigo glow, expanding ring, live equalizer bars. Click stops.
 */

import { Icon } from "@/components/icons";

export type OrbMode = "idle" | "listening" | "thinking" | "speaking" | "fallback";

const ACTIVE = new Set<OrbMode>(["listening", "thinking", "speaking", "fallback"]);

function Bars({ active, size }: { active: boolean; size: number }) {
  const heights = [8, 13, 10].map((h) => (h * size) / 44);
  return (
    <span className="flex items-center gap-[3px]" style={{ height: size * 0.34 }}>
      {heights.map((h, i) => (
        <span
          key={i}
          className={`block w-[2.5px] rounded-full ${active ? "eq-bar text-[var(--accent-contrast)] bg-current" : "bg-[var(--text-3)]"}`}
          style={{
            height: h,
            animationDelay: `${i * 150}ms`,
            animationDuration: `${950 + i * 120}ms`,
          }}
        />
      ))}
    </span>
  );
}

export function VoiceOrb({
  mode,
  size = 64,
  onClick,
}: {
  mode: string;
  size?: number;
  onClick?: () => void;
}) {
  const isActive = ACTIVE.has(mode as OrbMode);
  const listening = mode === "listening" || mode === "fallback";

  return (
    <button
      onClick={onClick}
      aria-label={isActive ? "Stop voice" : "Start voice"}
      style={{ width: size, height: size }}
      className="press fluid group relative grid shrink-0 place-items-center rounded-full outline-none"
    >
      {/* glow */}
      <span
        className={`fluid absolute inset-0 rounded-full ${
          isActive ? "animate-breathe" : "group-hover:bg-[var(--panel-hover)]"
        }`}
        style={
          isActive
            ? {
                background:
                  "radial-gradient(circle at 50% 42%, rgba(79,70,229,0.30), rgba(79,70,229,0.04) 70%)",
                boxShadow: "0 8px 32px rgba(79,70,229,0.28)",
              }
            : { boxShadow: "var(--shadow-card)" }
        }
      />

      {/* expanding ring while listening */}
      {listening && (
        <span className="animate-ring absolute inset-0 rounded-full border-2 border-[var(--accent-border)]" />
      )}

      {/* core disc */}
      <span
        className="fluid relative grid h-[78%] w-[78%] place-items-center rounded-full border"
        style={{
          borderColor: isActive ? "var(--accent)" : "var(--hairline-strong)",
          background: isActive ? "var(--accent)" : "var(--bg-raised)",
        }}
      >
        {mode === "thinking" ? (
          <Icon name="spark" size={size * 0.3} className="animate-breathe text-[var(--accent-contrast)]" />
        ) : isActive && !listening ? (
          <Icon name="stop" size={size * 0.28} className="text-[var(--accent-contrast)]" />
        ) : (
          <Bars active={listening} size={size} />
        )}
      </span>
    </button>
  );
}
