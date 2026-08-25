export function Orb({ state }: { state: string }) {
  return (
    <div className={`orb-wrap orb--${state}`}>
      <div className="orb">
        <div className="orb-core" />
        <div className="orb-ring" />
        <div className="orb-scan" />
      </div>
    </div>
  )
}
