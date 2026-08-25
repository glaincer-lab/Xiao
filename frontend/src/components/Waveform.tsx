export function Waveform({ active }: { active: boolean }) {
  const bars = Array.from({ length: 28 })
  return (
    <div className={`wave ${active ? 'wave--on' : ''}`}>
      {bars.map((_, i) => (
        <span
          key={i}
          className="wave-bar"
          style={{ animationDelay: `${(i * 0.055).toFixed(2)}s` }}
        />
      ))}
    </div>
  )
}
