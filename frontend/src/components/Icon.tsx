export type IconName = 'bookmark' | 'clock' | 'close' | 'external' | 'user' | 'refresh';
export function Icon({ name, filled = false }: { name: IconName; filled?: boolean }) {
  const paths: Record<IconName, string> = {
    bookmark: 'M5 3h14v18l-7-4-7 4V3Z', clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    close: 'm6 6 12 12M6 18 18 6', external: 'M14 3h7v7M21 3 10 14M10 3H3v18h18v-7',
    user: 'M20 21v-2a7 7 0 0 0-14 0v2M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
    refresh: 'M20 7V2l-3 3a9 9 0 1 0 3 11M20 7h-5',
  };
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill={filled ? 'currentColor' : 'none'}>
    <path d={paths[name]} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}
