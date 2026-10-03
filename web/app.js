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
    const text = [job.company, job.title, job.location, job.term, ...Object.values(keywords).flat()].join(' ').toLowerCase();
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
    const keywords = job.keywords || {};
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
