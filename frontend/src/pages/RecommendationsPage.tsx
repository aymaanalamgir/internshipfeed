import { useEffect, useRef, useState } from 'react';
import { getInteractionStates, recordInteraction } from '../api/interactions';
import { getRecommendations } from '../api/recommendations';
import { RecommendationCard } from '../components/RecommendationCard';
import { confirmInteraction, emptyInteraction, indexInteractions, visibleRecommendations } from '../lib/workflow';
import type { InteractionStates, InteractionType, Recommendation } from '../types/jobs';

export function RecommendationsPage() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [states, setStates] = useState<InteractionStates>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState<Record<number, InteractionType | undefined>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [announcement, setAnnouncement] = useState('');
  const [limit, setLimit] = useState(24);
  const busy = useRef(new Set<number>());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError(false);
    // Do not show a feed with unknown dismiss state if either request fails.
    Promise.all([getRecommendations(controller.signal), getInteractionStates(controller.signal)])
      .then(([jobs, interactions]) => {
        if (!controller.signal.aborted) { setRecommendations(jobs); setStates(indexInteractions(interactions)); }
      })
      .catch(() => { if (!controller.signal.aborted) setLoadError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  async function act(jobId: number, type: 'save' | 'dismiss' | 'applied') {
    if (busy.current.has(jobId)) return;
    busy.current.add(jobId);
    setPending(current => ({ ...current, [jobId]: type }));
    setErrors(current => ({ ...current, [jobId]: '' }));
    try {
      const event = await recordInteraction(jobId, type);
      setStates(current => confirmInteraction(current, jobId, event));
      setAnnouncement(type === 'dismiss' ? 'Job dismissed.' : type === 'save' ? 'Job saved.' : 'Job marked as applied.');
    } catch {
      const message = type === 'save' ? 'Could not save this job.' : type === 'dismiss' ? 'Could not dismiss this job.' : 'Could not mark this job as applied.';
      setErrors(current => ({ ...current, [jobId]: `${message} Please try again.` }));
    } finally {
      busy.current.delete(jobId);
      setPending(current => ({ ...current, [jobId]: undefined }));
    }
  }

  function apply(jobId: number) {
    // A native target=_blank link handles navigation synchronously. Never cancel it.
    setErrors(current => ({ ...current, [jobId]: '' }));
    void recordInteraction(jobId, 'click')
      .then(event => setStates(current => confirmInteraction(current, jobId, event)))
      .catch(() => setErrors(current => ({ ...current, [jobId]: 'Click could not be recorded. The application link is still available.' })));
  }

  const visible = visibleRecommendations(recommendations, states);
  return (
    <main className="recommendations-page" id="main-content">
      <div className="page-heading"><h1>Recommended</h1><span className="workspace-label"><span />{loading ? 'Loading matches' : `${visible.length} available`}</span></div>
      <div className="recommendations-intro"><p>Based on your <a href="/profile">profile preferences</a>.</p><span>Role 40 · Skills 30 · Location 20 · Term 10</span></div>
      <span className="sr-only" role="status">{announcement}</span>
      {loading ? <div className="loading-profile" role="status" aria-label="Loading recommendations">
        <span>Loading recommendations…</span>{[0, 1, 2].map(value => <div className="skeleton-field" key={value} />)}
      </div> : loadError ? <div className="load-error" role="alert"><p>Could not load recommendations and job states.</p>
        <button className="button button-secondary" onClick={() => setAttempt(value => value + 1)}>Try again</button>
      </div> : !visible.length ? <p className="recommendations-empty">{recommendations.length ? 'No active recommendations. Dismissed jobs stay hidden.' : 'No recommendations available yet.'}</p>
      : <>
        <div className="recommendation-list">
          {visible.slice(0, limit).map(recommendation => <RecommendationCard key={recommendation.job.id} recommendation={recommendation}
            interactionState={states[recommendation.job.id] ?? emptyInteraction(recommendation.job.id)}
            pending={pending[recommendation.job.id]} error={errors[recommendation.job.id]}
            onApply={apply} onSave={id => void act(id, 'save')} onDismiss={id => void act(id, 'dismiss')} onApplied={id => void act(id, 'applied')} />)}
        </div>
        <div className="recommendations-more"><span>Showing {Math.min(limit, visible.length)} of {visible.length}</span>
          {limit < visible.length && <button className="button button-secondary" onClick={() => setLimit(value => value + 24)}>Show more</button>}
        </div>
      </>}
    </main>
  );
}
