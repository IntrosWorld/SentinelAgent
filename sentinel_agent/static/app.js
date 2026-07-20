const $ = (selector) => document.querySelector(selector);
let currentRun = null;

async function checkHealth() {
  try {
    const data = await fetch('/api/health').then(r => r.json());
    const o = data.ollama;
    $('#health').textContent = o.online ? `OLLAMA ONLINE · ${o.model}${o.installed ? ' · INSTALLED' : ' · MODEL NOT PULLED'}` : 'OLLAMA OFFLINE · FALLBACK READY';
    $('#health').classList.toggle('online', o.online && o.installed);
  } catch { $('#health').textContent = 'API OFFLINE'; }
}

function render(run) {
  currentRun = run;
  $('#status').textContent = run.status.toUpperCase().replace('_', ' ');
  $('#status').className = `badge ${run.status}`;
  $('#timeline').innerHTML = run.events.map(e => `<div class="event"><small>${escapeHtml(e.kind)}</small><p>${escapeHtml(e.message)}</p></div>`).join('');
  $('#result').textContent = Object.keys(run.result).length ? JSON.stringify(run.result, null, 2) : `Step ${run.current_step} of ${run.plan?.actions.length || 0}`;
  if (run.status === 'waiting_approval') {
    $('#approval-text').textContent = `The agent wants to execute “${run.pending_action.name}”. This changes external state.`;
    $('#approval').showModal();
  }
}

function escapeHtml(value) {
  const node = document.createElement('div'); node.textContent = String(value); return node.innerHTML;
}

async function startRun() {
  $('#run').disabled = true;
  try {
    const disturbance = $('#disturbance').value;
    const response = await fetch(`/api/runs${disturbance ? `?disturbance=${disturbance}` : ''}`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({instruction:$('#instruction').value, use_model:$('#use-model').checked})
    });
    if (!response.ok) throw new Error(await response.text());
    render(await response.json());
  } catch (error) { $('#result').textContent = error.message; }
  finally { $('#run').disabled = false; }
}

async function approval(approved) {
  $('#approval').close();
  const response = await fetch(`/api/runs/${currentRun.id}/approval`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({approved})});
  render(await response.json());
}

$('#run').addEventListener('click', startRun);
$('#approve').addEventListener('click', () => approval(true));
$('#reject').addEventListener('click', () => approval(false));
checkHealth();

