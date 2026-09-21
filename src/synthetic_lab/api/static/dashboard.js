(() => {
  const initial = JSON.parse(document.getElementById('initial-runs')?.textContent || '[]');
  const state = { runs: initial, selected: null, timer: null, langfuseHost: null, evaluation: null };
  const $ = (id) => document.getElementById(id);
  function toast(message, kind = 'info') { const region = $('toast-region'); if (!region) return; const item = document.createElement('div'); item.className = `toast ${kind}`; item.textContent = message; region.appendChild(item); setTimeout(() => item.remove(), 4200); }
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label = (value) => String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  async function json(url, options) { const response = await fetch(url, options); if (!response.ok) throw new Error(await response.text()); return response.json(); }
  function statusClass(status) { return String(status || '').toLowerCase(); }
  function renderMetrics() {
    const visible = state.runs.filter((r) => !r.config_snapshot?.evaluation_id && !String(r.scenario_id).startsWith('evaluation:'));
    const active = visible.filter((r) => ['RUNNING','PAUSED'].includes(r.status)).length;
    const completed = visible.filter((r) => r.status === 'COMPLETED').length;
    $('metric-active').textContent = active;
    $('metric-completed').textContent = completed;
    $('metric-health').textContent = state.runs.length ? '100%' : '—';
  }
  function renderRuns() {
    renderMetrics();
    const list = $('runs-list');
    const visible = state.runs.filter((run) => !run.config_snapshot?.evaluation_id && !String(run.scenario_id).startsWith('evaluation:'));
    if (!visible.length) { list.innerHTML = '<div class="empty">No runs yet. Create one to start observing agents.</div>'; return; }
    list.innerHTML = visible.map((run) => `<div class="run-row ${state.selected === run.id ? 'selected' : ''}" data-run="${esc(run.id)}"><div class="run-id">${esc(run.id.slice(0, 18))}…</div><div class="run-scenario">${esc(label(run.scenario_id))}</div><div class="status ${statusClass(run.status)}"><i></i>${esc(run.status)}</div><div class="run-scenario">${new Date(run.created_at).toLocaleString()}</div><div class="run-actions">${run.status === 'CREATED' ? `<button class="small-button" data-action="start">Start agent</button>` : ''}${run.status === 'RUNNING' ? `<button class="small-button" data-action="pause">Pause</button>` : ''}${run.status === 'PAUSED' ? `<button class="small-button" data-action="resume">Resume</button>` : ''}${!['COMPLETED','FAILED','CANCELLED'].includes(run.status) ? `<button class="small-button" data-action="cancel">Cancel</button>` : ''}</div></div>`).join('');
    list.querySelectorAll('.run-row').forEach((row) => row.addEventListener('click', (event) => { if (!event.target.closest('button')) selectRun(row.dataset.run); }));
    list.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', async (event) => { event.stopPropagation(); const row = button.closest('.run-row'); const action = button.dataset.action; button.disabled = true; button.textContent = 'Working…'; try { await json(`/api/runs/${row.dataset.run}/${action}`, {method:'POST'}); toast(`${label(action)} completed`, 'success'); await refresh(); await selectRun(row.dataset.run); } catch (error) { toast(`Could not ${action} run: ${error.message}`, 'error'); await refresh(); } finally { button.disabled = false; } }));
  }
  async function selectRun(id) { state.selected = id; renderRuns(); $('activity-title').textContent = `Run ${id.slice(0, 12)}…`; try { const [summary, events, findings, metrics, reports] = await Promise.all([json(`/api/runs/${id}/summary`), json(`/api/runs/${id}/events?limit=300`), json(`/api/runs/${id}/findings`), json(`/api/runs/${id}/metrics`), json(`/api/runs/${id}/reports`)]); renderDetails(summary, events, findings, metrics, reports); } catch (error) { $('event-stream').innerHTML = `<div class="empty">Could not load run: ${esc(error.message)}</div>`; } }
  function renderDetails(summary, events, findings, metrics, reports) {
    $('event-count').textContent = `${events.length} events`; $('finding-count').textContent = findings.length; $('metric-findings').textContent = state.runs.reduce((total, r) => total, 0) + findings.length;
    $('run-latency').textContent = `${metrics.model_latency_ms || 0} ms`; $('run-failures').textContent = metrics.failures || 0; $('run-retries').textContent = metrics.retries || 0; $('run-tools').textContent = Object.entries(metrics.tool_usage || {}).map(([name, count]) => `${label(name)} ×${count}`).join(', ') || 'none';
    $('event-stream').innerHTML = events.length ? events.map((event) => { const detail = event.payload?.summary || event.payload?.tool_name || event.payload?.reason || event.payload?.query || (event.payload?.status ? `status: ${event.payload.status}` : 'durable agent event'); const trace = event.payload?.langfuse_trace_id && state.langfuseHost ? ` <a class="trace-link" href="${esc(state.langfuseHost + '/trace/' + event.payload.langfuse_trace_id)}" target="_blank" rel="noreferrer">trace ↗</a>` : ''; return `<div class="event ${esc(event.kind)}"><div class="event-time">${new Date(event.wall_time).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'})}</div><div class="event-mark"></div><div><div class="event-title">${esc(label(event.kind))} ${event.persona_id ? `<span class="muted">· ${esc(event.persona_id)}</span>` : ''}${trace}</div><div class="event-detail">${esc(detail)}</div></div></div>`; }).join('') : '<div class="empty">No events recorded yet. Start the run to see the agent loop here.</div>';
    $('findings-list').innerHTML = findings.length ? findings.map((finding, index) => `<div class="finding"><div class="finding-head"><strong>${esc(label(finding.invariant_id))}</strong><span class="finding-status">${esc(finding.status)}</span></div><p>Expected: ${esc(JSON.stringify(finding.expected))}<br>Actual: ${esc(JSON.stringify(finding.actual))}<br>Replay: ${esc(finding.replay_status)}</p><a class="trace-link" href="/api/runs/${encodeURIComponent(state.selected)}/reports" target="_blank" rel="noreferrer">Open evidence report ↗</a></div>`).join('') : '<div class="empty">No findings for the selected run.</div>';
    renderTraces(events);
    renderScreenshot();
  }
  function renderTraces(events) {
    const traces = new Map();
    events.forEach((event) => { const id = event.payload?.langfuse_trace_id; if (!id) return; const item = traces.get(id) || {id, persona:event.persona_id, events:[]}; item.events.push(event); traces.set(id, item); });
    $('trace-count').textContent = `${traces.size} trace${traces.size === 1 ? '' : 's'}`;
    $('trace-explorer').innerHTML = traces.size ? [...traces.values()].map((trace) => {
      const link = state.langfuseHost ? `${state.langfuseHost}/trace/${trace.id}` : null;
      const steps = trace.events.map((event) => `<span class="trace-step ${esc(event.kind)}">${esc(label(event.kind))}</span>`).join('<i>→</i>');
      return `<article class="trace-row"><div><strong>${esc(trace.persona || 'agent')}</strong><code>${esc(trace.id)}</code></div><div class="trace-steps">${steps}</div>${link ? `<a href="${esc(link)}" target="_blank" rel="noreferrer">Inspect trace ↗</a>` : '<span class="muted">Configure Langfuse to open</span>'}</article>`;
    }).join('') : '<div class="empty">This run has no exported traces yet.</div>';
  }
  function renderScreenshot() {
    const card = $('screenshot-card'); const image = $('final-screenshot');
    card.classList.add('hidden'); image.removeAttribute('src');
    if (!state.selected) return;
    image.onload = () => card.classList.remove('hidden');
    image.onerror = () => card.classList.add('hidden');
    image.src = `/api/runs/${encodeURIComponent(state.selected)}/artifacts/final-page?t=${Date.now()}`;
  }
  async function refresh() { try { state.runs = await json('/api/runs?limit=100'); $('refresh-status').textContent = `Updated ${new Date().toLocaleTimeString()}`; renderRuns(); if (state.selected) await selectRun(state.selected); await refreshEvaluation(); } catch (error) { $('refresh-status').textContent = 'API unavailable'; } }
  async function refreshEvaluation() {
    try { const rows = await json('/api/evaluations?limit=1'); if (!rows.length) return; state.evaluation = rows[0]; renderEvaluation(rows[0]); } catch (_) { /* evaluation is optional */ }
  }
  function renderEvaluation(result) {
    $('evaluation-status').innerHTML = `<strong>${esc(label(result.scenario_id))}</strong><span>${esc(result.status)} · ${result.completed_trials}/${result.requested_trials} trials</span>`;
    const metrics = [['Completion','completion_rate','percent'],['Discovery','discovery_rate','percent'],['False positives','false_positive_rate','percent'],['Mean latency','mean_latency_ms','latency']];
    const maxLatency = Math.max(1, result.arms.memory_on.mean_latency_ms, result.arms.memory_off.mean_latency_ms);
    $('evaluation-chart').innerHTML = metrics.map(([title,key,type]) => {
      const off=result.arms.memory_off[key]||0,on=result.arms.memory_on[key]||0; const offWidth=type==='percent'?off*100:off/maxLatency*100; const onWidth=type==='percent'?on*100:on/maxLatency*100; const fmt=(v)=>type==='percent'?`${Math.round(v*100)}%`:`${Math.round(v)} ms`;
      return `<div class="chart-row"><strong>${title}</strong><div class="bar-label">Memory off <span>${fmt(off)}</span></div><div class="bar-track"><i style="width:${offWidth}%"></i></div><div class="bar-label">Memory on <span>${fmt(on)}</span></div><div class="bar-track on"><i style="width:${onWidth}%"></i></div></div>`;
    }).join('') + `<div class="evaluation-totals"><span>Memory off tokens <b>${result.arms.memory_off.input_tokens + result.arms.memory_off.output_tokens}</b></span><span>Memory on tokens <b>${result.arms.memory_on.input_tokens + result.arms.memory_on.output_tokens}</b></span><span>Estimated local cost <b>$${(result.arms.memory_off.estimated_cost_usd + result.arms.memory_on.estimated_cost_usd).toFixed(4)}</b></span></div>`;
  }
  async function setupObservability() { try { const config = await json('/api/observability/config'); state.langfuseHost = config.langfuse_host; $('langfuse-copy').textContent = config.langfuse_enabled ? 'Safe run metadata can be opened in your configured Langfuse workspace.' : (config.langfuse_sdk_installed ? `Langfuse SDK installed; add the three SUL_LANGFUSE_* values to send traces. ${config.message}` : config.message); if (config.langfuse_host) { const link = $('langfuse-link'); link.href = config.langfuse_host; link.classList.remove('hidden'); } if (state.selected) await selectRun(state.selected); } catch (_) { $('langfuse-copy').textContent = 'Observability configuration unavailable.'; } }
  $('create-run').addEventListener('click', async () => { const button = $('create-run'); const scenario_id = $('scenario-select').value; button.disabled = true; button.textContent = 'Creating…'; try { const created = await json('/api/runs', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({scenario_id})}); toast(`${label(scenario_id)} run created`, 'success'); await refresh(); await selectRun(created.id); } catch (error) { toast(`Could not create run: ${error.message}`, 'error'); } finally { button.disabled = false; button.textContent = '+ New run'; } });
  $('toggle-studio').addEventListener('click', () => { const form = $('scenario-form'); form.classList.toggle('hidden'); $('toggle-studio').textContent = form.classList.contains('hidden') ? 'Author scenario' : 'Close studio'; });
  $('scenario-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const button = $('create-custom-run'); button.disabled = true; button.textContent = 'Creating…';
    const selector = $('credential-selector').value.trim(); const envVar = $('credential-env').value.trim();
    if ((selector && !envVar) || (!selector && envVar)) { toast('Credential selector and environment variable must be supplied together.', 'error'); button.disabled = false; button.textContent = 'Create authored run'; return; }
    const authored = {product:{name:$('product-name').value.trim(),base_url:$('product-url').value.trim(),start_path:$('start-path').value.trim(),allowed_hosts:[],credential_fields:selector?[{selector,env_var:envVar}]:[]},persona:{kind:$('persona-kind').value.trim(),goal:$('persona-goal').value.trim(),allowed_tools:['navigate','click','fill','observe_page']},invariant:{id:$('invariant-id').value.trim(),kind:$('invariant-kind').value,expected:$('invariant-expected').value.trim()},max_steps:Number($('max-steps').value)};
    try { const created = await json('/api/runs', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({scenario_id:`custom:${authored.product.name}`,config_snapshot:{authored_scenario:authored}})}); toast('Authored product run created', 'success'); await refresh(); await selectRun(created.id); document.querySelector('#runs').scrollIntoView({behavior:'smooth'}); }
    catch (error) { toast(`Could not create scenario: ${error.message}`, 'error'); }
    finally { button.disabled = false; button.textContent = 'Create authored run'; }
  });
  $('start-evaluation').addEventListener('click', async () => { const button=$('start-evaluation'); const trials=Number($('evaluation-trials').value); button.disabled=true; button.textContent='Starting…'; try { const result=await json('/api/evaluations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({scenario_id:$('evaluation-scenario').value,trials,expect_fault:$('evaluation-expect-fault').checked,run_config:{agent_model:'qwen',fault:$('evaluation-expect-fault').checked?({trial_return:'trial_expires_day_5',payment_retry:'duplicate_charge',ownership_transfer:'owner_transfer_leak',interrupted_onboarding:'onboarding_resets',stale_task_status:'task_completion_stale'}[$('evaluation-scenario').value]):null}})}); state.evaluation=result; renderEvaluation(result); toast(`Real-Qwen evaluation started with ${trials} trials`,'success'); } catch(error) { toast(`Could not start evaluation: ${error.message}`,'error'); } finally {button.disabled=false;button.textContent='Run evaluation';} });
  $('refresh-button').addEventListener('click', refresh); setupObservability(); refreshEvaluation(); renderRuns(); state.timer = setInterval(refresh, 5000);
})();
