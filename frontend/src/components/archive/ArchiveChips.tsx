import { Grid2X2 } from "lucide-react";

const chips = [
  { label: "All Entries", count: "1,284", active: true },
  { label: "2026", count: "247" },
  { label: "2025", count: "412" },
  { label: "2024", count: "358" },
  { label: "2023", count: "167" },
  { label: "2022", count: "76" },
  { label: "Growth", count: "312" },
  { label: "Family", count: "198" },
  { label: "Fitness", count: "186" },
  { label: "Travel", count: "84" },
];

export default function ArchiveChips() {
  return (
    <section className="archive-chips">
      {chips.map((chip, index) => (
        <button key={chip.label} className={chip.active ? "archive-chip active" : "archive-chip"}>
          <div className={`chip-image chip-${index}`}>
            {chip.active ? <Grid2X2 size={24} /> : null}
          </div>
          <strong>{chip.label}</strong>
          <span>{chip.count}</span>
        </button>
      ))}
    </section>
  );
}
