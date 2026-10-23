"use client";

/**
 * Command palette (Cmd/Ctrl+K): jump to any conversation, start a new one, or
 * open a platform page from the keyboard. Built on cmdk with the app's tokens.
 */

import { useEffect } from "react";
import { Command } from "cmdk";
import { Icon, type IconName } from "@/components/icons";

export interface PaletteConversation {
  id: string;
  title: string;
}

export interface PaletteNavItem {
  href: string;
  label: string;
  icon: IconName;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  conversations: PaletteConversation[];
  navItems: PaletteNavItem[];
  onOpenConversation: (id: string) => void;
  onNewConversation: () => void;
  onNavigate: (href: string) => void;
  onExportMine: () => void;
}

export function CommandPalette({
  open,
  onClose,
  conversations,
  navItems,
  onOpenConversation,
  onNewConversation,
  onNavigate,
  onExportMine,
}: CommandPaletteProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const run = (action: () => void) => {
    action();
    onClose();
  };

  // Palette shows only the 10 latest; the full list lives on /chats.
  const preview = conversations.slice(0, 10);
  const hasMore = conversations.length > preview.length;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]">
      <button aria-label="Close command palette" onClick={onClose} className="fixed inset-0 bg-[var(--scrim)] backdrop-blur-[2px]" />
      <Command
        label="Command palette"
        loop
        className="relative w-full max-w-xl overflow-hidden rounded-2xl border border-[var(--hairline)] bg-[var(--bg-raised)] shadow-[var(--shadow-pop)]"
      >
        <div className="flex items-center gap-2.5 border-b border-[var(--hairline)] px-4">
          <Icon name="spark" size={14} className="shrink-0 text-[var(--text-3)]" />
          <Command.Input
            autoFocus
            placeholder="Search conversations or jump to a page"
            className="h-12 w-full bg-transparent text-[13.5px] text-[var(--text-1)] outline-none placeholder:text-[var(--text-3)]"
          />
          <kbd className="shrink-0 rounded-md border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-3)]">
            Esc
          </kbd>
        </div>

        <Command.List className="max-h-[60vh] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-8 text-center text-[12.5px] text-[var(--text-3)]">
            No matches. Try a conversation title or a page name.
          </Command.Empty>

          <Command.Group heading="Actions" className="palette-group">
            <PaletteItem icon="plus" label="New conversation" hint="Ctrl+Shift+O" onSelect={() => run(onNewConversation)} />
            <PaletteItem icon="download" label="Export my data (JSON)" onSelect={() => run(onExportMine)} />
          </Command.Group>

          {preview.length > 0 && (
            <Command.Group heading="Conversations" className="palette-group">
              {preview.map((c) => (
                <PaletteItem
                  key={c.id}
                  value={`conversation ${c.id}`}
                  keywords={[c.title]}
                  icon="chat"
                  label={c.title}
                  onSelect={() => run(() => onOpenConversation(c.id))}
                />
              ))}
              {hasMore && (
                <PaletteItem
                  value="page view all chats"
                  keywords={["view all", "chats"]}
                  icon="search"
                  label={`View all chats (${conversations.length})`}
                  onSelect={() => run(() => onNavigate("/chats"))}
                />
              )}
            </Command.Group>
          )}

          <Command.Group heading="Go to" className="palette-group">
            {navItems.map((item) => (
              <PaletteItem
                key={item.href}
                value={`page ${item.label}`}
                keywords={[item.href]}
                icon={item.icon}
                label={item.label}
                onSelect={() => run(() => onNavigate(item.href))}
              />
            ))}
          </Command.Group>
        </Command.List>

        <div className="flex items-center gap-4 border-t border-[var(--hairline)] px-4 py-2 text-[10.5px] text-[var(--text-3)]">
          <span>
            <kbd className="font-mono">↑↓</kbd> navigate
          </span>
          <span>
            <kbd className="font-mono">↵</kbd> open
          </span>
          <span className="ml-auto">
            <kbd className="font-mono">Ctrl K</kbd> toggle
          </span>
        </div>
      </Command>
    </div>
  );
}

function PaletteItem({
  icon,
  label,
  hint,
  value,
  keywords,
  onSelect,
}: {
  icon: IconName;
  label: string;
  hint?: string;
  value?: string;
  keywords?: string[];
  onSelect: () => void;
}) {
  return (
    <Command.Item
      value={value ?? label}
      keywords={keywords}
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] text-[var(--text-2)] aria-selected:bg-[var(--panel-hover)] aria-selected:text-[var(--text-1)]"
    >
      <Icon name={icon} size={14} className="shrink-0 text-[var(--text-3)]" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span className="font-mono text-[10px] text-[var(--text-3)]">{hint}</span>}
    </Command.Item>
  );
}
