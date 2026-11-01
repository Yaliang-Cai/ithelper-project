'use strict';
const $ = id => document.getElementById(id);
let user = null, csrf = null, current = null, generation = 0, timer = null, sending = false;
const statusNames = {
  investigating:'处理中', waiting_user:'等待你的补充', waiting_approval:'等待审批',
  waiting_external:'等待外部恢复', verifying:'操作后待验证', resolved:'已确认解决',
  handed_off:'已保存模拟交接', denied:'已拒绝', cancelled:'已取消',
  pending:'待审批', approved:'已批准', rejected:'已拒绝', expired:'已过期',
  confirmed:'已确认', succeeded:'已执行（模拟）', denied_confirmation:'已拒绝'
};
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function error(text, kind = 'general') { $('error').textContent = text; $('error').hidden = !text; $('error').dataset.kind = kind; }
function messageOf(e) { return e.message || '请求失败，请稍后重试。'; }
async function api(path, options = {}) {
  const headers = { ...(options.body ? {'Content-Type':'application/json'} : {}), ...(options.method && options.method !== 'GET' && csrf ? {'X-CSRF-Token':csrf} : {}) };
  let response;
  try { response = await fetch(path, {...options, headers, credentials:'same-origin'}); }
  catch (_) { throw new Error('网络连接中断，请检查服务后重试。'); }
  let data;
  try { data = await response.json(); } catch (_) { throw new Error('服务暂时无法返回结果，请稍后重试。'); }
  if (!response.ok) {
    const e = new Error(data.error?.message || (response.status === 422 ? '输入不符合要求，请检查后重试。' : '请求失败，请重试。'));
    e.status = response.status;
    if (response.status === 401) showLogin();
    throw e;
  }
  return data;
}
function write(path, body) { return api(path, {method:'POST', body:JSON.stringify(body)}); }
function hidePanels() { for (const id of ['login-panel','employee-panel','approver-panel','debug-panel']) $(id).hidden = true; }
function showLogin() {
  generation++; clearTimeout(timer); user = null; csrf = null; current = null;
  hidePanels(); $('login-panel').hidden = false; $('navigation').hidden = true;
  $('logout').hidden = true; $('identity').textContent = '';
  // Remove employee/debug DOM at identity boundaries, including hidden content.
  for (const id of ['messages','issues','approvals','debug-output','runs','history']) $(id).replaceChildren();
}
function date(value) { return value ? new Date(value * 1000).toLocaleString() : '未提供'; }
function shortId(value) { const [prefix, suffix] = value.split('-'); return suffix ? `${prefix}-${suffix.slice(0,8)}` : value; }
function reference(card, label, value) { field(card, label, shortId(value)); card.append(details('查看完整' + label, value)); }
function effective(item, field) {
  const value = item[field];
  return item.expires_at * 1000 <= Date.now() && ['pending','approved','confirmed'].includes(value) ? 'expired' : value;
}
function details(title, body) {
  const element = node('details'); element.append(node('summary', title), node('div', body, 'source'));
  return element;
}
function field(card, label, value) { if (value !== undefined && value !== null) card.append(node('p', `${label}：${value}`)); }
function actionButton(label, action) {
  const button = node('button', label);
  button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true; error('');
    try { await action(); } catch (e) { error(messageOf(e)); }
    finally { button.disabled = false; }
  });
  return button;
}
function renderState(state) {
  const messages = $('messages');
  // Reuse stable message DOM so polling does not reset reading/keyboard focus.
  const existing = new Map([...messages.children].map(item => [item.dataset.id, item]));
  for (const m of state.messages) {
    if (!existing.has(m.id)) {
      const item = node('article', undefined, 'message ' + (m.role === 'user' ? 'user' : 'assistant'));
      item.dataset.id = m.id;
      item.append(node('strong', m.role === 'user' ? '你' : 'IT 助手'), node('p', m.content));
      messages.append(item);
      messages.scrollTop = messages.scrollHeight;
    }
  }
  const issues = $('issues');
  const uncertainty = state.unassigned_feedback || [];
  const uncertainIds = new Set(uncertainty.flatMap(item => item.candidate_issue_ids));
  const signature = JSON.stringify([state.issues,state.confirmations,state.approvals,state.handoffs,state.actions,state.evidence,uncertainty,state.confirmations.map(c => effective(c, 'decision')),state.approvals.map(a => effective(a, 'status'))]);
  if (issues.dataset.signature !== signature) {
    issues.dataset.signature = signature;
    issues.replaceChildren(node('h2', '问题进度'));
    if (uncertainty.length) issues.append(node('p', '反馈范围待澄清：尚不能确认所有问题已解决。请说明反馈针对哪个问题；相关状态保留上次验证结果。', 'warning'));
    if (!state.issues.length) issues.append(node('p','描述问题后，这里会显示独立的处理进度。','muted'));
    for (const issue of state.issues) {
      const card = node('section', undefined, 'card');
      const label = statusNames[issue.status] || '状态待核对';
      card.append(node('h3', issue.goal), node('p', uncertainIds.has(issue.id) ? '上次验证：' + label + '（反馈范围待澄清）' : label, 'status'));
      reference(card, '问题编号', issue.id);
      field(card, '资源', issue.resource_id);
      for (const a of state.actions.filter(a => a.issue_id === issue.id)) {
        card.append(node('p', '已执行（模拟） · ' + shortId(a.id)));
        card.append(details('查看实际操作回执', JSON.stringify(a.receipt, null, 2)));
      }
      for (const c of state.confirmations.filter(c => c.issue_id === issue.id)) {
        const decision = effective(c, 'decision');
        const executed = state.actions.some(a => a.receipt?.confirmation_id === c.id);
        const label = decision === 'confirmed' && executed ? '已确认且已执行（模拟）' : ({pending:'等待你的明确确认', confirmed:'已确认，尚需执行', denied:'已拒绝', cancelled:'已取消', expired:'已过期'}[decision] || '状态待核对');
        card.append(node('h3', '有影响的操作确认'), node('p', c.impact, 'warning'));
        field(card, '目标', c.target); reference(card, '确认编号', c.id);
        field(card, '有效期至', date(c.expires_at)); card.append(node('p', label));
        if (decision === 'pending') {
          const buttons = node('div', undefined, 'toolbar');
          const choose = async value => { await write(`/confirmations/${c.id}/decision`, {decision:value}); await refresh(); };
          buttons.append(actionButton('确认并允许中断', () => choose('confirmed')), actionButton('拒绝此操作', () => choose('denied')));
          card.append(buttons);
        } else if (decision === 'confirmed' && !state.actions.some(a => a.receipt?.confirmation_id === c.id)) {
          card.append(actionButton('撤销尚未执行的确认', async () => { await write(`/confirmations/${c.id}/decision`, {decision:'cancelled'}); await refresh(); }));
        }
      }
      for (const a of state.approvals.filter(a => a.issue_id === issue.id)) {
        const status = effective(a, 'status');
        card.append(node('h3', '精确资源审批'), node('p', statusNames[status] || '状态待核对'));
        reference(card, '审批编号', a.id); field(card, '范围', `${a.resource_id} / ${a.permission}`);
        field(card, '有效期至', date(a.expires_at));
        const executed = state.actions.some(action => action.receipt?.approval_id === a.id);
        if (['pending','approved'].includes(status) && !executed) card.append(actionButton('取消该申请', async () => { await write(`/approvals/${a.id}/decision`, {decision:'cancelled'}); await refresh(); }));
      }
      for (const h of state.handoffs.filter(h => h.issue_refs?.includes(issue.id))) {
        card.append(node('p', '模拟交接已保存')); reference(card, '交接编号', h.id); field(card, '团队', h.team);
      }
      // Sources are exclusively persisted owned evidence records. Model URLs,
      // Markdown and HTML are never interpreted or used as links.
      const evidence = state.evidence.filter(e => !e.scope?.issue_id || e.scope.issue_id === issue.id);
      if (evidence.length) {
        const sources = node('details'); sources.append(node('summary', `相关来源（${evidence.length}）`));
        for (const e of evidence) {
          const source = node('div', undefined, 'source');
          source.append(node('strong', `${e.id} · ${e.source} · ${e.record_id} · 版本 ${e.revision}`));
          source.append(node('p', typeof e.content === 'string' ? e.content : JSON.stringify(e.content, null, 2)));
          sources.append(source);
        }
        card.append(sources);
      }
      issues.append(card);
    }
  }
  const active = state.events.filter(e => ['queued','running'].includes(e.status));
  const last = state.runs.at(-1);
  if (last && ['failed','budget_exhausted'].includes(last.status) && !active.some(e => e.id !== last.event_id)) {
    $('progress').textContent = `本轮未完成（${last.error_code || '处理异常'}）。现有状态已保留；已发生的操作以状态卡为准，可补充消息继续。`;
    if (active.length) $('progress').textContent += ' 后台正在等待恢复并保存本轮结果。';
  } else if (active.length) {
    const recovering = state.processing?.recovery_pending || active.some(e => e.status === 'running' && e.lease_until * 1000 <= Date.now());
    $('progress').textContent = recovering ? '上次处理已中断，等待后台恢复。已发生的操作以状态卡为准。' : '消息已保存，后台正在处理。你可以关闭后重新打开聊天。';
  } else $('progress').textContent = last ? '本轮回复已保存，请查看各问题进度；仍可能需要补充、审批或验证。' : '';
}
async function loadHistory() {
  const before = generation, history = await api('/conversations');
  if (before !== generation) return;
  $('history').replaceChildren(node('option', '选择聊天'));
  $('history').firstChild.value = '';
  for (const c of history) { const option = node('option', c.title + ' · ' + date(c.created_at)); option.value = c.id; $('history').append(option); }
  $('history').value = current || '';
}
function pendingKey() { return `helpdesk:pending:${user?.id}:${current}`; }
function restorePending() {
  const value = sessionStorage.getItem(pendingKey());
  if (value) {
    try { $('message').value = JSON.parse(value).content; $('send').textContent = '重试发送'; }
    catch (_) { sessionStorage.removeItem(pendingKey()); }
  } else { $('message').value = ''; $('send').textContent = '发送'; }
}
function selectChat(cid) {
  generation++; clearTimeout(timer); current = cid || null;
  $('chat-layout').hidden = !current; $('welcome').hidden = !!current; $('close-chat').hidden = !current;
  $('messages').replaceChildren(); $('issues').replaceChildren(); delete $('issues').dataset.signature;
  $('history').value = current || ''; error(''); restorePending();
  if (current) refresh();
}
async function refresh() {
  clearTimeout(timer);
  if (!current || !user) return;
  const before = generation, cid = current;
  try {
    const state = await api('/conversations/' + cid);
    if (before !== generation) return;
    renderState(state);
    if ($('error').dataset.kind === 'poll') error('');
  } catch (e) {
    if (before !== generation) return;
    error('读取进度失败：' + messageOf(e), 'poll');
    $('progress').textContent = '进度暂时无法获取。保留上次可见状态，正在等待重新连接；请勿据此推断操作成功。';
  } finally {
    if (before === generation && current) timer = setTimeout(refresh, 1500);
  }
}
async function loadApprovals() {
  const before = generation, items = await api('/approvals');
  if (before !== generation) return;
  const target = $('approvals'); target.replaceChildren();
  if (!items.length) target.append(node('p', '目前没有你有权处理的申请。'));
  for (const a of items) {
    const card = node('section', undefined, 'card'), status = effective(a, 'status');
    card.append(node('h2', a.resource_id), node('p', statusNames[status] || '状态待核对'));
    for (const [label,value] of [['申请编号',a.id],['申请人',a.actor_id],['问题编号',a.issue_id],['权限',a.permission],['理由',a.reason],['有效期至',date(a.expires_at)]]) field(card,label,value);
    if (status === 'pending') {
      const buttons = node('div', undefined, 'toolbar');
      const choose = async decision => { await write(`/approvals/${a.id}/decision`, {decision}); await loadApprovals(); };
      buttons.append(actionButton('批准只读申请', () => choose('approved')), actionButton('拒绝申请', () => choose('rejected')));
      card.append(buttons);
    }
    target.append(card);
  }
}
async function loadDebugRun() {
  const id = $('runs').value, before = generation;
  const result = id ? await api('/debug/runs/' + id) : {message:'尚无运行记录'};
  if (before === generation) $('debug-output').textContent = JSON.stringify(result, null, 2);
}
async function loadDebug() {
  const before = generation, runs = await api('/debug/runs');
  if (before !== generation) return;
  $('runs').replaceChildren();
  for (const r of [...runs].reverse()) { const option = node('option', `${r.id} · ${r.status}`); option.value = r.id; $('runs').append(option); }
  await loadDebugRun();
}
async function enter(data) {
  generation++; hidePanels(); user = data.user; csrf = data.csrf_token;
  $('identity').textContent = `${user.name} · ${user.id}`; $('logout').hidden = false; $('navigation').hidden = false;
  $('approver-link').hidden = !user.roles.includes('approver'); $('debug-link').hidden = !user.roles.includes('debug');
  if (location.pathname === '/approver') { $('approver-panel').hidden = false; await loadApprovals(); }
  else if (location.pathname === '/debug') { $('debug-panel').hidden = false; await loadDebug(); }
  else { $('employee-panel').hidden = false; selectChat(null); await loadHistory(); }
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); error('');
  const button = event.submitter; button.disabled = true;
  try {
    const result = await write('/auth/login', {username:$('username').value, password:$('password').value});
    history.replaceState(null, '', '/'); await enter(result);
  } catch (e) { error(messageOf(e)); } finally { button.disabled = false; }
});
$('logout').addEventListener('click', async () => {
  try { await write('/auth/logout', {}); showLogin(); history.replaceState(null, '', '/'); error(''); }
  catch (e) { error(messageOf(e)); }
});
$('new-chat').addEventListener('click', async () => {
  try { const c = await write('/conversations', {title:'IT 服务咨询'}); selectChat(c.id); await loadHistory(); $('message').focus(); }
  catch (e) { error(messageOf(e)); }
});
$('history').addEventListener('change', () => selectChat($('history').value));
$('close-chat').addEventListener('click', () => selectChat(null));
$('refresh').addEventListener('click', refresh);
$('message-form').addEventListener('submit', async event => {
  event.preventDefault(); if (sending || !current || !$('message').value.trim()) return;
  const content = $('message').value, before = generation, cid = current, key = pendingKey();
  let pending;
  try { pending = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { pending = null; }
  if (!pending || pending.content !== content) pending = {content, idempotency_key:crypto.randomUUID()};
  sessionStorage.setItem(key, JSON.stringify(pending));
  sending = true; $('send').disabled = true; error('');
  try {
    await write(`/conversations/${cid}/messages`, pending);
    sessionStorage.removeItem(key);
    if (before === generation) { $('message').value = ''; $('send').textContent = '发送'; await refresh(); }
  } catch (e) {
    if (before === generation) { error('发送结果尚未确认：' + messageOf(e) + ' 重试会复用同一消息编号。'); $('send').textContent = '重试发送'; }
  } finally { sending = false; $('send').disabled = false; }
});
$('refresh-approvals').addEventListener('click', () => loadApprovals().catch(e => error(messageOf(e))));
$('refresh-debug').addEventListener('click', () => loadDebug().catch(e => error(messageOf(e))));
$('runs').addEventListener('change', () => loadDebugRun().catch(e => error(messageOf(e))));
api('/me').then(enter).catch(e => { if (e.status !== 401) { showLogin(); error(messageOf(e)); } });
