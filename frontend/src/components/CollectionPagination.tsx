// Keep loaded jobs visible while the next page loads or offers an inline retry.
export function CollectionPagination({ count, total, hasMore, loading, error, onMore }: {
  count: number; total?: number; hasMore: boolean; loading: boolean; error: boolean; onMore: () => void;
}) {
  return <div className="collection-footer">
    <span>Showing {count}{total !== undefined && ` of ${total}`} jobs</span>
    <span className="pagination-status">
      {error && <span role="alert">Could not load more jobs.</span>}
      {(hasMore || error || loading) && <button className="button" type="button" aria-disabled={loading}
        onClick={() => { if (!loading) onMore(); }}>{loading ? 'Loading more…' : error ? 'Try again' : 'Show more'}</button>}
      {loading && <span className="sr-only" role="status">Loading more…</span>}
    </span>
  </div>;
}
