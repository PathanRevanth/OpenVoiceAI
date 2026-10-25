"use client";

/**
 * Voice orb — thinking-orbs for the live speech layer.
 * Idle is a still breathing globe; listening / thinking / speaking each
 * get their own animation so the caller can see the two-layer loop.
 */

import { ThinkingOrb, type OrbState } from "thinking-orbs";

export type OrbMode = "idle" | "listening" | "thinking" | "speaking" | "fallback" | "connecting";

const STATE: Record<OrbMode, OrbState> = {
  idle: "breathing",
  connecting: "connecting",
  listening: "listening",
  fallback: "listening",
  thinking: "working",
  speaking: "composing",
};

export function voiceOrbState(mode: string): OrbState {
  return STATE[(mode as OrbMode) || "idle"] ?? "breathing";
}

export function VoiceOrb({
  mode,
  size = 64,
  onClick,
  interactive = true,
}: {
  mode: string;
  size?: number;
  onClick?: () => void;
  interactive?: boolean;
}) {
  const isActive = mode !== "idle";
  const orbSize: 64 | 32 | 20 = size >= 48 ? 64 : size >= 28 ? 32 : 20;
  const scale = size / orbSize;
  const inner = (
    <span style={{ transform: scale === 1 ? undefined : `scale(${scale})` }}>
      <ThinkingOrb
        state={voiceOrbState(mode)}
        size={orbSize}
        theme="auto"
        speed={isActive ? 1.15 : 0.7}
        aria-label={isActive ? "Voice session" : "Start voice"}
      />
    </span>
  );
  const box = { width: size, height: size } as const;
  if (!interactive) {
    return (
      <span className="grid shrink-0 place-items-center" style={box}>
        {inner}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={isActive ? "Stop voice" : "Start voice"}
      className="press fluid grid shrink-0 place-items-center rounded-full outline-none"
      style={box}
    >
      {inner}
    </button>
  );
}
