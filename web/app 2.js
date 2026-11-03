// Legacy vanilla job-directory client retained for reference.
const search = document.querySelector('#search');
const status = document.querySelector('#status');
const sourceFilter = document.querySelector('#source');
const sourceNames = new Map();
const rows = document.querySelector('#jobs');
const summary = document.querySelector('#summary');
const message = document.querySelector('#message');
const list = document.querySelector('#list');
const reload = document.querySelector('#reload');
let jobs = [];
const jobContexts = new Map();
let loaded = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function applicationUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function render() {
  if (!loaded) return;
  const query = search.value.trim().toLowerCase();
  const visible = jobs.filter(job => {
    const context = jobContexts.get(job.id);
    const keywords = context.keywords || {};
    const text = [job.company, job.title, job.location, context.term, ...(context.sources || []).flatMap(record => [sourceNames.get(record.source_id), record.company, record.title, record.location]), ...Object.values(keywords).flat()].join(' ').toLowerCase();
    return (status.value === 'all' || job.status === status.value) && (sourceFilter.value === 'all' || (context.sources || []).some(record => String(record.source_id) === sourceFilter.value && !['closed', 'dead'].includes(record.status))) && text.includes(query);
  });
  summary.textContent = `${visible.length.toLocaleString()} of ${jobs.length.toLocaleString()} jobs · ${jobs.reduce((count, job) => count + jobContexts.get(job.id).sources.length, 0).toLocaleString()} source records`;
  rows.replaceChildren();
  list.hidden = visible.length === 0;
  message.hidden = visible.length > 0;
  message.textContent = jobs.length ? 'No jobs match your filters. Try another search or status.' : 'No internships available yet. Check back soon.';
  const fragment = document.createDocumentFragment();
  for (const job of visible) {
    const context = jobContexts.get(job.id);
    const row = document.createElement('tr');
    const title = document.createElement('td');
    const details = element('button', job.title || 'Untitled role', 'role details-link');
    details.type = 'button';
    details.addEventListener('click', () => showDetails(job.id));
    title.append(element('span', job.company || 'Unknown company', 'company'), details, element('span', [...new Set((context.sources || []).map(record => sourceNames.get(record.source_id) || record.source))].join(' · '), 'source-label'));
    row.append(title, element('td', job.location || 'Not specified'), element('td', context.term || 'Not specified', context.term ? '' : 'muted'));
    const state = document.createElement('td');
    const jobStatus = ['open', 'closed'].includes(job.status) ? job.status : 'unknown';
    state.append(element('span', jobStatus, `badge ${jobStatus}`));
    row.append(state, element('td', job.posted_at || '—'));
    const action = document.createElement('td');
    const url = applicationUrl(job.application_url);
    if (jobStatus === 'open' && url) {
      const link = element('a', 'Apply ↗', 'apply');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `Apply for ${job.title} at ${job.company} (opens in a new tab)`);
      action.append(link);
    } else {
      action.append(element('span', '—', 'muted'));
    }
    row.append(action);
    fragment.append(row);
  }
  rows.append(fragment);
}

async function load() {
  reload.disabled = true;
  summary.textContent = 'Loading jobs…';
  try {
    const response = await fetch('/api/feed', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data) || data.some(entry => !entry?.job || !Array.isArray(entry.sources))) throw new Error('Invalid job data');
    jobContexts.clear();
    for (const entry of data) jobContexts.set(entry.job.id, entry);
    jobs = data.map(entry => entry.job);
    loaded = true;
    render();
  } catch (error) {
    loaded = false;
    rows.replaceChildren();
    list.hidden = true;
    message.hidden = false;
    summary.textContent = 'Unable to load jobs';
    message.textContent = 'Jobs are unavailable right now. Please try refreshing in a moment.';
    console.error('Unable to load listings:', error);
  } finally {
    reload.disabled = false;
  }
}

search.addEventListener('input', render);
status.addEventListener('change', render);
sourceFilter.addEventListener('change', render);
reload.addEventListener('click', load);
load();
loadSources();

async function loadSources() {
  try {
    const response = await fetch('/api/sources');
    if (!response.ok) return;
    const sources = await response.json();
    for (const source of sources) {
      sourceNames.set(source.id, source.name);
      const option = element('option', source.name);
      option.value = source.id;
      sourceFilter.append(option);
    }
    render();
  } catch (error) { console.error('Unable to load source names:', error); }
}

const dialog = document.querySelector('#job-details');
const detailContent = document.querySelector('#detail-content');
let detailRequest = 0;
document.querySelector('#close-details').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => { detailRequest += 1; });

async function showDetails(id) {
  const request = ++detailRequest;
  detailContent.replaceChildren(element('p', 'Loading job details…'));
  dialog.showModal();
  try {
    const response = await fetch(`/api/jobs/${id}/details`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const context = await response.json();
    const job = context.job;
    if (request !== detailRequest) return;
    detailContent.replaceChildren(
      element('p', job.company, 'company'),
      element('h2', job.title),
      element('p', [job.location, context.term, job.status].filter(Boolean).join(' · '), 'muted')
    );
    detailContent.append(element('p', `Canonical job #${job.id}`, 'muted'));
    const provenance = document.createElement('details');
    provenance.append(element('summary', `Source records (${context.sources.length})`));
    const observations = document.createElement('ul');
    for (const record of context.sources) {
      const entry = document.createElement('li');
      const sourceLink = element('a', sourceNames.get(record.source_id) || 'Source repository');
      sourceLink.href = applicationUrl(record.source) || '#';
      sourceLink.target = '_blank';
      sourceLink.rel = 'noopener noreferrer';
      entry.append(sourceLink, element('p', `Source job #${record.id} · ${record.status} · ${record.title}`, 'muted'),
        element('p', `Within-source ID: ${record.external_id} · ${record.match_method} (${record.match_confidence})`, 'source-identity'));
      observations.append(entry);
    }
    provenance.append(observations);
    detailContent.append(provenance);
    const keywords = context.keywords || {};
    for (const [key, label] of [['languages', 'Languages'], ['frameworks', 'Frameworks'], ['tools_and_platforms', 'Tools & platforms'], ['concepts', 'Concepts']]) {
      if (keywords[key]?.length) {
        detailContent.append(element('h3', label));
        const chips = document.createElement('div');
        chips.className = 'chips';
        for (const keyword of keywords[key]) chips.append(element('span', keyword, 'keyword'));
        detailContent.append(chips);
      }
    }
    if (keywords.requirements?.length) {
      detailContent.append(element('h3', 'Key requirements'));
      const requirements = document.createElement('ul');
      for (const requirement of keywords.requirements) requirements.append(element('li', requirement));
      detailContent.append(requirements);
    }
    detailContent.append(element('h3', 'Job description'));
    detailContent.append(element('p', context.description || 'A description is not available for this listing yet. Visit the application page for the full posting.', 'description'));
    if (context.description) detailContent.append(element('p', 'Keywords are extracted mentions and may include preferred skills.', 'muted'));
    const url = applicationUrl(job.application_url);
    if (job.status === 'open' && url) {
      const link = element('a', 'View application ↗', 'apply');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      detailContent.append(link);
    }
  } catch (error) {
    if (request !== detailRequest) return;
    detailContent.replaceChildren(element('p', 'Unable to load this job. Please close and try again.'));
    console.error('Unable to load job details:', error);
  }
}


const syncButton = document.querySelector('#sync-jobs');
const syncPanel = document.querySelector('#sync-panel');
const syncStatus = document.querySelector('#sync-status');
const syncProgress = document.querySelector('#sync-progress');
const syncCounts = document.querySelector('#sync-counts');
let pollTimer;
let syncing = false;

function showSync(run) {
  if (!run) return;
  syncPanel.hidden = false;
  syncing = run.status === 'running';
  syncButton.disabled = syncing;
  syncButton.textContent = syncing ? 'Syncing…' : 'Sync & enrich';
  const total = run.scraped || 0;
  const processed = run.processed || 0;
  if (syncing && !total) syncProgress.removeAttribute('value');
  else { syncProgress.max = total || 1; syncProgress.value = processed || (syncing ? 0 : total); }
  syncStatus.textContent = syncing
    ? (run.stage === 'fetching' || !total ? `Fetching ${run.current_source || 'the latest listings'}…` : `Processing ${run.current_source || 'sources'}: ${processed} of ${total} listings. ${run.current_job || ''}`)
    : run.status === 'success' ? 'Sync complete.' : 'Sync ended with errors. Successfully saved jobs are available; try syncing again.';
  syncCounts.textContent = `${run.inserted || 0} new source records · ${run.updated || 0} updated · ${run.skipped_duplicates || 0} duplicates · ${run.failed || 0} failed · ${run.enrichment_successes || 0} descriptions read · ${run.closed || 0} closed skipped · ${run.dead_links || 0} dead links hidden`;
  if (run.reconciliation) syncCounts.textContent += ` · ${run.reconciliation.visible_jobs} unique available jobs · ${run.reconciliation.duplicate_listings_removed} duplicate listings removed`;
  const sources = (run.sources || []).map(source => `${source.name}: ${source.scraped || 0} scraped, ${source.failed || 0} failed (${source.status})`);
  if (sources.length) syncCounts.textContent += ' · ' + sources.join(' · ');
}

async function pollSync() {
  clearTimeout(pollTimer);
  try {
    const response = await fetch('/api/ingestion-batches/latest', { cache: 'no-store' });
    if (!response.ok) throw new Error('Progress unavailable');
    const run = await response.json();
    const wasSyncing = syncing;
    showSync(run);
    if (syncing) pollTimer = setTimeout(pollSync, 2000);
    else if (wasSyncing) await load();
  } catch {
    if (syncing) { syncStatus.textContent = 'Waiting for sync progress…'; pollTimer = setTimeout(pollSync, 5000); }
  }
}

syncButton.addEventListener('click', async () => {
  syncButton.disabled = true;
  syncPanel.hidden = false;
  syncStatus.textContent = 'Starting sync…';
  try {
    const response = await fetch('/api/sync', { method: 'POST' });
    if (!response.ok) throw new Error('Unable to start sync');
    showSync(await response.json());
    await pollSync();
  } catch {
    syncPanel.hidden = false;
    syncStatus.textContent = 'Unable to start sync. Please try again.';
    syncButton.disabled = false;
  }
});
pollSync();
