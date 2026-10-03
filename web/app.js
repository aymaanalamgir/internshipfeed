const search = document.querySelector('#search');
const status = document.querySelector('#status');
const rows = document.querySelector('#jobs');
const summary = document.querySelector('#summary');
const message = document.querySelector('#message');
const list = document.querySelector('#list');
const reload = document.querySelector('#reload');
let jobs = [];
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
    const keywords = job.keywords || {};
    const text = [job.company, job.title, job.location, job.term, job.ai_analysis?.overview, ...Object.values(keywords).flat(), ...(job.ai_analysis?.tech_stack || []).map(item => item.text), ...(job.ai_analysis?.frameworks || []).map(item => item.text)].join(' ').toLowerCase();
    return (status.value === 'all' || job.status === status.value) && text.includes(query);
  });
  summary.textContent = `${visible.length.toLocaleString()} of ${jobs.length.toLocaleString()} jobs`;
  rows.replaceChildren();
  list.hidden = visible.length === 0;
  message.hidden = visible.length > 0;
  message.textContent = jobs.length ? 'No jobs match your filters. Try another search or status.' : 'No internships available yet. Check back soon.';
  const fragment = document.createDocumentFragment();
  for (const job of visible) {
    const row = document.createElement('tr');
    const title = document.createElement('td');
    const details = element('button', job.title || 'Untitled role', 'role details-link');
    details.type = 'button';
    details.addEventListener('click', () => showDetails(job.id));
    title.append(element('span', job.company || 'Unknown company', 'company'), details);
    row.append(title, element('td', job.location || 'Not specified'), element('td', job.term || 'Not specified', job.term ? '' : 'muted'));
    const state = document.createElement('td');
    const jobStatus = ['open', 'closed'].includes(job.status) ? job.status : 'unknown';
    state.append(element('span', jobStatus, `badge ${jobStatus}`));
    row.append(state, element('td', job.posted_at || '—'));
    const action = document.createElement('td');
    const url = applicationUrl(job.url);
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
    const response = await fetch('/api/jobs', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data) || data.some(job => !job || typeof job !== 'object')) throw new Error('Invalid job data');
    jobs = data;
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
reload.addEventListener('click', load);
load();


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
    const response = await fetch(`/api/jobs/${id}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const job = await response.json();
    if (request !== detailRequest) return;
    detailContent.replaceChildren(
      element('p', job.company, 'company'),
      element('h2', job.title),
      element('p', [job.location, job.term, job.status].filter(Boolean).join(' · '), 'muted')
    );
    if (job.ai_analysis) {
      const analysis = job.ai_analysis;
      detailContent.append(element('h3', 'AI overview'), element('p', analysis.overview, 'ai-overview'));
      for (const [key, label] of [['key_requirements', 'Key requirements'], ['preferred_qualifications', 'Preferred qualifications'], ['tech_stack', 'Tech stack'], ['frameworks', 'Frameworks'], ['responsibilities', 'Responsibilities']]) {
        if (!analysis[key]?.length) continue;
        detailContent.append(element('h3', label));
        const items = document.createElement('ul');
        for (const item of analysis[key]) {
          const entry = document.createElement('li');
          entry.append(element('span', item.text));
          const evidence = document.createElement('details');
          evidence.className = 'evidence';
          evidence.append(element('summary', 'Source evidence'), element('p', item.evidence));
          entry.append(evidence);
          items.append(entry);
        }
        detailContent.append(items);
      }
    } else {
      detailContent.append(element('p', job.ai_error || 'AI overview has not been generated yet.', 'muted'));
    }
    const keywords = job.keywords || {};
    if (!job.ai_analysis) for (const [key, label] of [['languages', 'Languages'], ['frameworks', 'Frameworks'], ['tools_and_platforms', 'Tools & platforms'], ['concepts', 'Concepts']]) {
      if (keywords[key]?.length) {
        detailContent.append(element('h3', label));
        const chips = document.createElement('div');
        chips.className = 'chips';
        for (const keyword of keywords[key]) chips.append(element('span', keyword, 'keyword'));
        detailContent.append(chips);
      }
    }
    if (!job.ai_analysis && keywords.requirements?.length) {
      detailContent.append(element('h3', 'Key requirements'));
      const requirements = document.createElement('ul');
      for (const requirement of keywords.requirements) requirements.append(element('li', requirement));
      detailContent.append(requirements);
    }
    detailContent.append(element('h3', 'Job description'));
    detailContent.append(element('p', job.description || 'A description is not available for this listing yet. Visit the application page for the full posting.', 'description'));
    if (job.description) detailContent.append(element('p', 'Keywords are extracted mentions and may include preferred skills.', 'muted'));
    const url = applicationUrl(job.url);
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
    ? (total ? `Reading descriptions: ${processed} of ${total} listings. ${run.current_job || ''}` : 'Fetching the latest listings…')
    : run.status === 'success' ? 'Sync complete.' : 'Sync ended with errors. Successfully saved jobs are available; try syncing again.';
  syncCounts.textContent = `${run.inserted || 0} new · ${run.updated || 0} updated · ${run.skipped_duplicates || 0} duplicates · ${run.failed || 0} failed · ${run.enrichment_successes || 0} descriptions read · ${run.ai_successes || 0} AI overviews · ${run.ai_cached || 0} AI results reused · ${run.closed || 0} closed skipped · ${run.dead_links || 0} dead links hidden`;
  if (run.ai_not_configured) syncCounts.textContent += ' · Add a Google API key in AI settings to enable overviews.';
  if (run.ai_failed || run.ai_deferred) syncCounts.textContent += ` · ${(run.ai_failed || 0) + (run.ai_deferred || 0)} AI analyses unavailable; retry later.`;
}

async function pollSync() {
  clearTimeout(pollTimer);
  try {
    const response = await fetch('/api/ingestion-runs/latest', { cache: 'no-store' });
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

const settingsDialog = document.querySelector('#ai-settings');
const settingsMessage = document.querySelector('#ai-settings-message');
const keyInput = document.querySelector('#ai-key');
document.querySelector('#ai-settings-button').addEventListener('click', async () => {
  settingsMessage.textContent = '';
  settingsDialog.showModal();
  try {
    const response = await fetch('/api/settings/ai');
    if (!response.ok) throw new Error();
    const config = await response.json();
    document.querySelector('#ai-configuration-status').textContent = config.configured ? 'A Google key is saved. You can replace it below.' : 'No Google key has been saved yet.';
  } catch { settingsMessage.textContent = 'Could not read AI settings.'; }
});
document.querySelector('#close-ai-settings').addEventListener('click', () => settingsDialog.close());
settingsDialog.addEventListener('close', () => { keyInput.value = ''; });
document.querySelector('#ai-settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const save = document.querySelector('#save-ai-settings');
  save.disabled = true;
  try {
    const response = await fetch('/api/settings/ai', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: keyInput.value.trim(), free_tier_confirmed: document.querySelector('#free-tier').checked }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Unable to save key.');
    keyInput.value = '';
    document.querySelector('#ai-configuration-status').textContent = 'Google key saved.';
    settingsMessage.textContent = 'Saved. Close settings and click Sync & enrich to generate AI overviews.';
  } catch (error) { settingsMessage.textContent = error.message; }
  finally { save.disabled = false; }
});
