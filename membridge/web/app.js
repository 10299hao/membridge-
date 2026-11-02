const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('token')) {
  sessionStorage.setItem('membridge-token', fragment.get('token'));
  history.replaceState(null, '', location.pathname);
}
const token = sessionStorage.getItem('membridge-token') || '';
let state = null;
let refreshing = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function pill(text, style = 'neutral') { return node('span', text, 'pill ' + style); }
function dateText(seconds) {
  const date = new Date(seconds * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : 'Unavailable';
}
async function api(url) {
  const response = await fetch(url, {headers: {Authorization: 'Bearer ' + token}});
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || 'Unable to load project data');
  return value;
}
function emptyRow(columns, message) {
  const row = node('tr');
  const cell = node('td', message, 'empty');
  cell.colSpan = columns;
  row.append(cell);
  return row;
}
function reviewFor(memory) {
  return state?.verification?.memories.find(item => item.memory_id === memory.id);
}
function issueText(review) {
  return (review?.issues || []).map(issue => issue.code.replaceAll('_', ' ') + (issue.file ? ': ' + issue.file : '')).join('\n');
}
async function details(memory) {
  const dialog = $('evidence-dialog');
  const content = $('dialog-content');
  $('dialog-title').textContent = 'Memory details';
  content.replaceChildren(node('p', memory.text, 'detail-claim'), node('p', memory.source_tool + ' · ' + memory.source_session + ' · ' + dateText(memory.created), 'detail-meta'));
  const review = reviewFor(memory);
  if (review?.state === 'needs_review') content.append(node('p', issueText(review), 'detail-warning'));
  const source = node('div');
  source.append(node('p', 'Loading source evidence…', 'caption'));
  content.append(source);
  dialog.showModal();
  try {
    const value = await api('/api/evidence/' + encodeURIComponent(memory.id));
    source.replaceChildren();
    for (const message of value.messages) source.append(node('h3', message.role + ' · ' + message.id), node('pre', message.content));
  } catch (error) {
    source.replaceChildren(node('p', error.message, 'detail-warning'));
  }
}
const typeLabels = {decision:'Decision', todo:'Task', file_change:'File change', preference:'Preference', fact:'Fact'};
function memoryRow(memory) {
  const row = node('tr');
  const type = node('td');
  type.append(pill(typeLabels[memory.type] || memory.type, memory.type));
  const text = node('td');
  text.append(node('p', memory.text, 'memory-copy'));
  if (reviewFor(memory)?.state === 'needs_review') text.append(node('span', 'Repository changed · review source', 'review-note'));
  const source = node('td');
  const id = node('span', memory.source_session, 'source-id');
  id.title = memory.source_session;
  source.append(node('span', memory.source_tool, 'source-tool'), id);
  const status = node('td');
  status.append(pill(memory.status, memory.status));
  const time = node('td', dateText(memory.created), 'saved-date');
  const action = node('td');
  const button = node('button', 'Details ↗', 'details-button');
  button.type = 'button';
  button.addEventListener('click', () => details(memory));
  action.append(button);
  row.append(type, text, source, status, time, action);
  return row;
}
function renderMemories() {
  if (!state) return;
  const query = $('search').value.trim().toLowerCase();
  const type = $('type-filter').value;
  const status = $('status-filter').value;
  const rows = [...state.memories].sort((a,b) => b.created-a.created).filter(memory =>
    (type === 'all' || memory.type === type) && (status === 'all' || memory.status === status) &&
    [memory.text, memory.source_tool, memory.source_session].join(' ').toLowerCase().includes(query));
  $('visible-count').textContent = rows.length + (rows.length === 1 ? ' record' : ' records');
  $('memory-list').replaceChildren(...(rows.length ? rows.map(memoryRow) : [emptyRow(6, state.memories.length ? 'No memories match these filters.' : 'No saved memories yet. Save a conversation using the MemBridge CLI.')]));
}
function render() {
  $('project-name').textContent = state.project;
  $('project-path').textContent = state.directory;
  $('count-memories').textContent = state.memories.length;
  $('count-tasks').textContent = state.memories.filter(memory => memory.type === 'todo' && memory.status === 'active').length;
  $('count-sessions').textContent = state.sessions.length;
  $('count-review').textContent = state.verification.memories.filter(memory => memory.state === 'needs_review').length;
  const fields = state.checkpoint?.fields || {};
  $('current-task').textContent = fields.current_task?.[0]?.text.replace(/^(?:current task|task|goal)\s*:\s*/i, '') || 'No saved checkpoint';
  $('checkpoint-source').textContent = state.checkpoint?.source_tool || 'No saved session';
  $('checkpoint-details').replaceChildren();
  for (const [key, label] of [['next_steps','Next steps'],['blockers','Blockers'],['verification','Reported verification'],['completed','Completed work']]) {
    if (!fields[key]?.length) continue;
    const row = node('div', undefined, 'checkpoint-detail' + (key === 'blockers' ? ' blocker' : ''));
    const text = fields[key].map(item => item.text.replace(/^(?:next step|next action|todo|blocker|tests?|completed)\s*:\s*/i, '')).join('\n');
    row.append(node('strong', label), node('p', text));
    $('checkpoint-details').append(row);
  }
  $('sync-list').replaceChildren(...Object.entries(state.sync).map(([source,status]) => {
    const row = node('div', undefined, 'sync-row');
    row.append(node('strong', source === 'codex' ? 'Codex' : 'OpenCode'), pill(status.state.replaceAll('-',' '), status.state === 'running' ? 'running' : 'neutral'));
    if (status.errors) row.title = status.errors + ' reported errors';
    return row;
  }));
  const queued = state.jobs.filter(job => ['pending','processing'].includes(job.state)).length;
  const failed = state.jobs.filter(job => job.state === 'failed').length;
  $('job-status').textContent = queued + ' pending / processing · ' + failed + ' failed';
  $('retention-status').textContent = state.retention.retention_days ? state.retention.retention_days + ' days' : 'Keep indefinitely';
  renderMemories();
  $('history-list').replaceChildren(...(state.history.length ? [...state.history].sort((a,b) => b.created-a.created).map(memoryRow) : [emptyRow(6, 'No memory history yet.')]));
  $('session-list').replaceChildren(...(state.sessions.length ? [...state.sessions].sort((a,b) => b.updated-a.updated).map(session => {
    const row = node('tr');
    row.append(node('td', session.title || session.source_session, 'session-title'), node('td', session.source_tool), node('td', dateText(session.updated), 'saved-date'), node('td', session.id, 'session-id'));
    return row;
  }) : [emptyRow(4, 'No saved sessions yet.')]));
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  $('refresh').disabled = true;
  try {
    const next = await api('/api/state');
    state = next;
    render();
    $('updated-at').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    $('notice').hidden = true;
  } catch (error) {
    $('notice').textContent = error.message + (state ? ' Showing the last loaded data.' : '');
    $('notice').hidden = false;
    if (!state) {
      $('updated-at').textContent = 'Not connected';
      $('memory-list').replaceChildren(emptyRow(6, 'Project data could not be loaded.'));
      $('current-task').textContent = 'Checkpoint unavailable';
    }
  } finally {
    refreshing = false;
    $('refresh').disabled = false;
  }
}
function navigate(view) {
  const titles = {overview:'Project overview',sessions:'Saved sessions',history:'Memory history'};
  document.querySelectorAll('.view').forEach(element => element.hidden = element.id !== 'view-' + view);
  document.querySelectorAll('.nav').forEach(element => {
    const selected = element.dataset.view === view;
    element.classList.toggle('active', selected);
    if (selected) element.setAttribute('aria-current','page'); else element.removeAttribute('aria-current');
  });
  $('page-title').textContent = titles[view];
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.view)));
$('refresh').addEventListener('click', refresh);
$('search').addEventListener('input', renderMemories);
$('type-filter').addEventListener('change', renderMemories);
$('status-filter').addEventListener('change', renderMemories);
$('close-dialog').addEventListener('click', () => $('evidence-dialog').close());
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName) && !$('evidence-dialog').open) {
    event.preventDefault(); navigate('overview'); $('search').focus();
  }
});
refresh();
setInterval(() => {
  if (!document.hidden && !$('evidence-dialog').open && !document.activeElement?.closest('tbody')) refresh();
}, 15000);
