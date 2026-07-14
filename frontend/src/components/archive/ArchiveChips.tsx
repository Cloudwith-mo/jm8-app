import { Grid2X2 } from "lucide-react";
import type { JournalEntry } from "../../types/journal";

type ArchiveChipsProps = {
  entries: JournalEntry[];
};

type Chip = {
  label: string;
  count: number;
  active?: boolean;
};

function getYearChips(entries: JournalEntry[]) {
  const counts = new Map<string, number>();

  for (const entry of entries) {
    if (!entry.createdAt) continue;

    const year = String(new Date(entry.createdAt).getFullYear());
    counts.set(year, (counts.get(year) || 0) + 1);
  }

  return Array.from(counts.entries())
    .sort(([a], [b]) => Number(b) - Number(a))
    .slice(0, 5)
    .map(([label, count]) => ({ label, count }));
}

function getThemeChips(entries: JournalEntry[]) {
  const counts = new Map<string, number>();

  for (const entry of entries) {
    const themes = entry.analysis?.themes || [];

    if (themes.length === 0) {
      const fallback = entry.sourceType === "image" ? "OCR" : "Typed";
      counts.set(fallback, (counts.get(fallback) || 0) + 1);
      continue;
    }

    for (const theme of themes) {
      counts.set(theme, (counts.get(theme) || 0) + 1);
    }
  }

  return Array.from(counts.entries())
    .sort(([, a], [, b]) => b - a)
    .slice(0, 4)
    .map(([label, count]) => ({ label, count }));
}

export default function ArchiveChips({ entries }: ArchiveChipsProps) {
  const chips: Chip[] = [
    { label: "All Entries", count: entries.length, active: true },
    ...getYearChips(entries),
    ...getThemeChips(entries),
  ];

  return (
    <section className="archive-chips">
      {chips.map((chip, index) => (
        <button
          key={`${chip.label}-${index}`}
          className={chip.active ? "archive-chip active" : "archive-chip"}
        >
          <div className={`chip-image chip-${index % 10}`}>
            {chip.active ? <Grid2X2 size={24} /> : null}
          </div>
          <strong>{chip.label}</strong>
          <span>{chip.count}</span>
        </button>
      ))}
    </section>
  );
}
