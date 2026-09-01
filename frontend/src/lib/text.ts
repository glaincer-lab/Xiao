/* 文本截断：折叠连续空白 + 超长加省略号（App 与 TaskPanel 共用，消除重复实现）。 */
export function truncate(s: string, n = 60): string {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}
