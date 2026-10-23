"use client";

/**
 * Claude-style project identity: colored folder icon, searchable icon grid,
 * and the eight-dot color palette. The picker panel and the create dialog
 * are shared by the sidebar quick-add, the Projects page, and project
 * settings, so all three stay identical.
 */

import { useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { backend, type Project } from "@/lib/backend";
import {
  PROJECT_COLORS,
  projectColorHex,
  projectIconGlyph,
  searchProjectIcons,
} from "@/lib/project-style";

/** Folder (or chosen) glyph rendered in the project's color. */
export function ProjectIcon({
  icon,
  color,
  size = 15,
  className = "",
}: {
  icon: string | null | undefined;
  color: string | null | undefined;
  size?: number;
  className?: string;
}) {
  return (
    <Icon
      name={projectIconGlyph(icon)}
      size={size}
      className={className}
      strokeWidth={2}
      style={{ color: projectColorHex(color) }}
    />
  );
}

/** Search + color dots + icon grid, exactly like the reference picker. */
export function IconColorPicker({
  icon,
  color,
  onIcon,
  onColor,
}: {
  icon: string;
  color: string;
  onIcon: (key: string) => void;
  onColor: (key: string) => void;
}) {
  const [query, setQuery] = useState("");
  const results = useMemo(() => searchProjectIcons(query), [query]);
  const hex = projectColorHex(color);

  return (
    <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-3 shadow-[var(--shadow-pop)]">
      <div className="flex items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search icons"
          aria-label="Search icons"
          className="fluid h-9 min-w-0 flex-1 rounded-lg border border-[var(--accent-border)] bg-[var(--bg)] px-3 text-[13px] outline-none placeholder:text-[var(--text-3)]"
        />
        <button
          type="button"
          onClick={() => {
            setQuery("");
            onIcon("folder");
            onColor("black");
          }}
          className="press shrink-0 rounded-full bg-[var(--panel-hover)] px-3.5 py-2 text-[12.5px] font-medium text-[var(--text-1)] hover:bg-[var(--hairline-strong)]"
        >
          Clear
        </button>
      </div>
      <div className="mt-3 flex items-center gap-2.5 px-1">
        {PROJECT_COLORS.map((c) => {
          const selected = color === c.key;
          return (
            <button
              key={c.key}
              type="button"
              aria-label={`${c.label} color`}
              aria-pressed={selected}
              title={c.label}
              onClick={() => onColor(c.key)}
              style={{
                background: c.hex,
                boxShadow: selected ? `0 0 0 3px ${c.hex}33` : undefined,
              }}
              className="press h-7 w-7 shrink-0 rounded-full"
            />
          );
        })}
      </div>
      <div className="mt-3 grid max-h-56 grid-cols-8 gap-0.5 overflow-y-auto">
        {results.map((def) => {
          const selected = icon === def.key;
          return (
            <button
              key={def.key}
              type="button"
              title={def.label}
              aria-label={`${def.label} icon`}
              aria-pressed={selected}
              onClick={() => onIcon(def.key)}
              style={selected ? { background: `${hex}1f` } : undefined}
              className="press grid aspect-square place-items-center rounded-lg hover:bg-[var(--panel-hover)]"
            >
              <Icon name={def.glyph} size={17} strokeWidth={1.75} style={{ color: hex }} />
            </button>
          );
        })}
        {results.length === 0 && (
          <p className="col-span-8 py-4 text-center text-[12px] text-[var(--text-3)]">
            No icons match.
          </p>
        )}
      </div>
    </div>
  );
}

/** Create-project dialog with name, icon/color picker, and goals. */
export function CreateProjectDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  const [name, setName] = useState("");
  const [goals, setGoals] = useState("");
  const [icon, setIcon] = useState("folder");
  const [color, setColor] = useState("black");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const hex = projectColorHex(color);

  const create = async () => {
    const title = name.trim();
    if (!title || saving) return;
    setSaving(true);
    try {
      const created = await backend.createProject(title, { icon, color });
      const description = goals.trim();
      const finalProject = description
        ? await backend.updateProject(created.id, { instructions: description })
        : created;
      onCreated(finalProject);
    } catch {
      /* keep the dialog open on failure */
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button aria-label="Close" className="fixed inset-0 z-50 cursor-default bg-[var(--scrim)]" onClick={onClose} />
      <div
        role="dialog"
        aria-label="Create a project"
        className="fixed left-1/2 top-1/2 z-50 w-[min(480px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] p-6 shadow-[var(--shadow-pop)]"
      >
        <span className="flex items-start justify-between">
          <h2 className="text-[20px] font-medium tracking-tight text-[var(--text-1)]">Create project</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="press rounded-lg p-1.5 text-[var(--text-2)] hover:bg-[var(--panel-hover)] hover:text-[var(--text-1)]"
          >
            <Icon name="x" size={16} />
          </button>
        </span>
        <label className="mt-5 block text-[14px] text-[var(--text-1)]" htmlFor="new-project-name">
          Project name
        </label>
        <span className="relative mt-1.5 block">
          <span className="flex h-11 items-center rounded-xl border border-[var(--hairline-strong)] bg-[var(--bg)] focus-within:border-[var(--accent-border)]">
            <button
              type="button"
              aria-label="Choose project icon and color"
              title="Choose icon and color"
              onClick={() => setPickerOpen((v) => !v)}
              aria-expanded={pickerOpen}
              className="press grid h-full w-11 shrink-0 place-items-center rounded-l-xl bg-[var(--panel-hover)]/60"
            >
              <Icon name={projectIconGlyph(icon)} size={17} strokeWidth={2} style={{ color: hex }} />
            </button>
            <input
              id="new-project-name"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void create();
              }}
              maxLength={128}
              placeholder="Copenhagen Trip"
              className="fluid h-full min-w-0 flex-1 bg-transparent px-3 text-[14px] outline-none placeholder:text-[var(--text-3)]"
            />
          </span>
          {pickerOpen && (
            <span className="absolute inset-x-0 top-full z-10 mt-1 block">
              <IconColorPicker icon={icon} color={color} onIcon={setIcon} onColor={setColor} />
            </span>
          )}
        </span>
        <label className="mt-4 block text-[14px] text-[var(--text-1)]" htmlFor="new-project-goals">
          What are you trying to achieve?
        </label>
        <textarea
          id="new-project-goals"
          value={goals}
          onChange={(e) => setGoals(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Describe your project, goals, subject, etc..."
          className="mt-1.5 w-full resize-y rounded-xl border border-[var(--hairline)] bg-[var(--bg)] p-3 text-[13.5px] outline-none placeholder:text-[var(--text-3)] focus:border-[var(--accent-border)]"
        />
        <span className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="press rounded-xl border border-[var(--hairline)] px-4 py-2 text-[13.5px] font-medium text-[var(--text-2)] hover:text-[var(--text-1)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void create()}
            disabled={!name.trim() || saving}
            className="press rounded-xl bg-[var(--solid)] px-4 py-2 text-[13.5px] font-semibold text-[var(--on-solid)] hover:opacity-90 disabled:opacity-40"
          >
            Create project
          </button>
        </span>
      </div>
    </>
  );
}
