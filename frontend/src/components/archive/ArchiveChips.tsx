import { Grid2X2 } from "lucide-react";
import type { JournalEntry } from "../../types/journal";

export type ArchiveChipFilter = {
  type: "all" | "year" | "theme";
  value: string;
};

type ArchiveChipsProps = {
  entries: JournalEntry[];
  activeFilter: ArchiveChipFilter;
  onFilterChange: (filter: ArchiveChipFilter) => void;
};

type Chip = {
  label: string;
  count: number;
  filter: ArchiveChipFilter;
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
    .map(([label, count]) => ({
      label,
      count,
      filter: { type: "year", value: label } as ArchiveChipFilter,
    }));
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
    .slice(0, 5)
    .map(([label, count]) => ({
      label,
      count,
      filter: { type: "theme", value: label } as ArchiveChipFilter,
    }));
}

function isActiveChip(activeFilter: ArchiveChipFilter, chipFilter: ArchiveChipFilter) {
  return activeFilter.type === chipFilter.type && activeFilter.value === chipFilter.value;
}

export default function ArchiveChips({
  entries,
  activeFilter,
  onFilterChange,
}: ArchiveChipsProps) {
  const chips: Chip[] = [
    {
      label: "All Entries",
      count: entries.length,
      filter: { type: "all", value: "all" },
    },
    ...getYearChips(entries),
    ...getThemeChips(entries),
  ];

  return (
    <section className="archive-chips">
      {chips.map((chip, index) => {
        const isActive = isActiveChip(activeFilter, chip.filter);

        return (
          <button
            key={`${chip.label}-${index}`}
            className={isActive ? "archive-chip active" : "archive-chip"}
            onClick={() => onFilterChange(chip.filter)}
            title={`Filter by ${chip.label}`}
          >
            <div className={`chip-image chip-${index % 10}`}>
              {isActive ? <Grid2X2 size={24} /> : null}
            </div>
            <strong>{chip.label}</strong>
            <span>{chip.count}</span>
          </button>
        );
      })}
    </section>
  );
}
