"use client";

/**
 * Claude-style project identity: a color dot palette and a searchable icon
 * grid. The backend stores only the `icon` + `color` keys; this module maps
 * them to hex colors and lucide glyphs, with safe fallbacks for unknown keys.
 */

import type { IconName } from "@/components/icons";

export interface ProjectColor {
  key: string;
  label: string;
  hex: string;
}

/** The eight picker dots, in the reference order. */
export const PROJECT_COLORS: ProjectColor[] = [
  { key: "black", label: "Black", hex: "#1f1f23" },
  { key: "red", label: "Red", hex: "#e5484d" },
  { key: "orange", label: "Orange", hex: "#f76b15" },
  { key: "yellow", label: "Yellow", hex: "#e5a000" },
  { key: "green", label: "Green", hex: "#30a46c" },
  { key: "blue", label: "Blue", hex: "#2684ff" },
  { key: "violet", label: "Purple", hex: "#8e4ec6" },
  { key: "pink", label: "Pink", hex: "#ef5da8" },
];

const FALLBACK_HEX = "#6b7280";

export function projectColorHex(color: string | null | undefined): string {
  if (!color) return FALLBACK_HEX;
  return PROJECT_COLORS.find((c) => c.key === color)?.hex ?? FALLBACK_HEX;
}

export function isProjectColor(color: string): boolean {
  return PROJECT_COLORS.some((c) => c.key === color);
}

export interface ProjectIconDef {
  key: string;
  label: string;
  keywords: string[];
  glyph: IconName;
}

export const PROJECT_ICONS: ProjectIconDef[] = [
  { key: "folder", label: "Folder", keywords: ["folder", "project", "directory"], glyph: "folder" },
  { key: "dollar", label: "Money", keywords: ["money", "dollar", "finance", "budget"], glyph: "dollar" },
  { key: "book", label: "Book", keywords: ["book", "read", "study", "docs"], glyph: "book" },
  { key: "cap", label: "Graduation", keywords: ["school", "study", "learn", "graduation", "university"], glyph: "cap" },
  { key: "pencil", label: "Pencil", keywords: ["write", "edit", "pencil", "draft"], glyph: "pencil" },
  { key: "pen", label: "Pen", keywords: ["write", "pen", "sign", "author"], glyph: "pen" },
  { key: "code", label: "Code", keywords: ["code", "dev", "programming", "brackets"], glyph: "code" },
  { key: "presentation", label: "Presentation", keywords: ["slides", "present", "deck", "talk"], glyph: "presentation" },
  { key: "music", label: "Music", keywords: ["music", "song", "audio", "melody"], glyph: "music" },
  { key: "audio", label: "Headphones", keywords: ["audio", "headphones", "podcast", "listen"], glyph: "audio" },
  { key: "trophy", label: "Trophy", keywords: ["win", "trophy", "award", "champion"], glyph: "trophy" },
  { key: "medal", label: "Medal", keywords: ["medal", "award", "rank", "place"], glyph: "medal" },
  { key: "crown", label: "Crown", keywords: ["crown", "royal", "king", "premium"], glyph: "crown" },
  { key: "star", label: "Star", keywords: ["star", "favorite", "best", "highlight"], glyph: "star" },
  { key: "gem", label: "Gem", keywords: ["gem", "diamond", "jewel", "precious"], glyph: "gem" },
  { key: "fitness", label: "Workout", keywords: ["gym", "workout", "fitness", "dumbbell", "sport"], glyph: "fitness" },
  { key: "game", label: "Gaming", keywords: ["game", "gaming", "controller", "play"], glyph: "game" },
  { key: "ball", label: "Sports", keywords: ["sports", "ball", "team"], glyph: "trophy" },
  { key: "palette", label: "Art", keywords: ["art", "design", "paint", "color", "creative"], glyph: "palette" },
  { key: "photo", label: "Photo", keywords: ["photo", "camera", "picture", "image"], glyph: "photo" },
  { key: "health", label: "Health", keywords: ["health", "medical", "doctor", "clinic"], glyph: "health" },
  { key: "clinic", label: "Clinic", keywords: ["clinic", "medical", "health", "care"], glyph: "clinic" },
  { key: "lab", label: "Science", keywords: ["science", "lab", "flask", "experiment", "research"], glyph: "lab" },
  { key: "scope", label: "Research", keywords: ["microscope", "research", "science", "analyze"], glyph: "scope" },
  { key: "flower", label: "Flower", keywords: ["flower", "plant", "nature", "garden"], glyph: "flower" },
  { key: "lotus", label: "Lotus", keywords: ["lotus", "yoga", "calm", "flower", "zen"], glyph: "lotus" },
  { key: "trees", label: "Trees", keywords: ["trees", "forest", "nature", "outdoors"], glyph: "trees" },
  { key: "waves", label: "Waves", keywords: ["ocean", "sea", "waves", "water", "beach"], glyph: "waves" },
  { key: "paw", label: "Pets", keywords: ["pets", "paw", "dog", "cat", "animals"], glyph: "paw" },
  { key: "cat", label: "Cat", keywords: ["cat", "kitten", "pet"], glyph: "cat" },
  { key: "dog", label: "Dog", keywords: ["dog", "puppy", "pet"], glyph: "dog" },
  { key: "work", label: "Work", keywords: ["work", "briefcase", "job", "business", "office"], glyph: "work" },
  { key: "bank", label: "Bank", keywords: ["bank", "finance", "landmark", "money"], glyph: "bank" },
  { key: "news", label: "News", keywords: ["news", "press", "articles", "media"], glyph: "news" },
  { key: "idea", label: "Ideas", keywords: ["idea", "lightbulb", "brainstorm", "think"], glyph: "idea" },
  { key: "brain", label: "Brain", keywords: ["brain", "mind", "ai", "think", "smart"], glyph: "brain" },
  { key: "heart", label: "Heart", keywords: ["heart", "love", "like", "health"], glyph: "heart" },
  { key: "gift", label: "Gift", keywords: ["gift", "present", "birthday", "surprise"], glyph: "gift" },
  { key: "cake", label: "Cake", keywords: ["cake", "birthday", "party", "celebrate"], glyph: "cake" },
  { key: "sweet", label: "Dessert", keywords: ["dessert", "sweet", "treat", "ice cream"], glyph: "sweet" },
  { key: "food", label: "Food", keywords: ["food", "pizza", "eat", "restaurant", "dinner"], glyph: "food" },
  { key: "coffee", label: "Coffee", keywords: ["coffee", "tea", "drink", "cafe", "break"], glyph: "coffee" },
  { key: "plane", label: "Travel", keywords: ["travel", "plane", "trip", "flight", "vacation"], glyph: "plane" },
  { key: "sail", label: "Sailing", keywords: ["sail", "boat", "sea", "travel"], glyph: "sail" },
  { key: "camp", label: "Camping", keywords: ["camp", "tent", "outdoors", "hike"], glyph: "camp" },
  { key: "car", label: "Car", keywords: ["car", "drive", "auto", "vehicle"], glyph: "car" },
  { key: "bike", label: "Bike", keywords: ["bike", "cycling", "ride"], glyph: "bike" },
  { key: "rocket", label: "Rocket", keywords: ["rocket", "launch", "startup", "space"], glyph: "rocket" },
  { key: "telescope", label: "Space", keywords: ["space", "stars", "telescope", "astronomy"], glyph: "telescope" },
  { key: "scale", label: "Legal", keywords: ["legal", "law", "scale", "justice", "balance"], glyph: "scale" },
  { key: "math", label: "Math", keywords: ["math", "calculator", "numbers", "finance"], glyph: "math" },
  { key: "language", label: "Language", keywords: ["language", "translate", "words", "learn"], glyph: "language" },
  { key: "notes", label: "Notes", keywords: ["notes", "notebook", "journal", "write"], glyph: "notes" },
  { key: "shop", label: "Shopping", keywords: ["shop", "store", "buy", "bag", "retail"], glyph: "shop" },
  { key: "guitar", label: "Guitar", keywords: ["guitar", "music", "instrument", "band"], glyph: "guitar" },
  { key: "drum", label: "Drums", keywords: ["drums", "music", "band", "rhythm"], glyph: "drum" },
  { key: "globe", label: "Globe", keywords: ["globe", "world", "earth", "global", "web"], glyph: "globe" },
];

const KNOWN = new Set(PROJECT_ICONS.map((i) => i.key));

export function projectIconGlyph(icon: string | null | undefined): IconName {
  if (icon && KNOWN.has(icon)) {
    return PROJECT_ICONS.find((i) => i.key === icon)!.glyph;
  }
  return "folder";
}

export function searchProjectIcons(query: string): ProjectIconDef[] {
  const q = query.trim().toLowerCase();
  if (!q) return PROJECT_ICONS;
  return PROJECT_ICONS.filter(
    (i) =>
      i.label.toLowerCase().includes(q) ||
      i.key.includes(q) ||
      i.keywords.some((k) => k.includes(q)),
  );
}
