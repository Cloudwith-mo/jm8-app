type ChartPoint = { label: string; value: number };

type Props = { title: string; description: string; points: ChartPoint[] };

export default function InsightsLineChart({ title, description, points }: Props) {
  if (points.length < 2) return null;
  const width = 520;
  const height = 210;
  const inset = 22;
  const max = Math.max(...points.map((point) => point.value), 1);
  const coordinates = points.map((point, index) => ({
    ...point,
    x: inset + (index / (points.length - 1)) * (width - inset * 2),
    y: height - inset - (point.value / max) * (height - inset * 2),
  }));

  return <figure className="phase2c-chart">
    <header><h2>{title}</h2><p>{description}</p></header>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${description}`}>
      <line x1={inset} y1={height - inset} x2={width - inset} y2={height - inset} />
      <polyline points={coordinates.map((point) => `${point.x},${point.y}`).join(" ")} />
      {coordinates.map((point) => <circle key={point.label} cx={point.x} cy={point.y} r="4"><title>{`${point.label}: ${point.value}`}</title></circle>)}
    </svg>
    <figcaption>{points[0].label}: {points[0].value}; {points.at(-1)?.label}: {points.at(-1)?.value}.</figcaption>
    <table className="phase2c-visually-hidden"><caption>{title}</caption><thead><tr><th>Period</th><th>Entries</th></tr></thead>
      <tbody>{points.map((point) => <tr key={point.label}><th>{point.label}</th><td>{point.value}</td></tr>)}</tbody></table>
  </figure>;
}
