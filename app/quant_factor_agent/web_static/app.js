const state = { health: null, inventory: null, factors: [], currentRun: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const pageNames = {
  learn: "Learn Factor Research", research: "AI Quant Research", runs: "Research Runs",
  factors: "Factor Library", multi: "Multi-Factor Model", backtest: "Backtest",
  paper: "Paper Trading", knowledge: "Knowledge & Data", connections: "Connections"
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json", ...(options.headers || {})}, ...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error || `HTTP ${response.status}`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.style.background = error ? "#a52f3c" : "#102038";
  node.classList.add("show"); setTimeout(() => node.classList.remove("show"), 3200);
}

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", {maximumFractionDigits: digits});
}

function fmtPct(value, digits = 2) { return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(digits)}%`; }

function showPage(name) {
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.page === name));
  $("#page-title").textContent = pageNames[name] || name;
  if (window.location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "runs") loadRuns();
  if (name === "factors" || name === "multi") loadFactors();
  if (name === "paper") loadPaper();
  if (name === "knowledge") loadInventory();
}

async function initialize() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
  bindEvents();
  const initialPage = window.location.hash.replace("#", "");
  if (pageNames[initialPage]) showPage(initialPage);
  await Promise.allSettled([loadHealth(), loadInventory(), loadRuns(), loadFactors(), loadPaper()]);
}

async function loadHealth() {
  try {
    const health = await api("/api/health"); state.health = health;
    $("#health-dot").style.background = "#47cf9c";
    $("#model-status").textContent = health.model_configured ? `Model · ${health.model}` : "Model · not configured";
    $("#connection-llm").textContent = health.model_configured ? health.model : "Not configured";
    $("#rag-status").textContent = `RAG · ${fmtNumber(health.knowledge.chunks, 0)} chunks`;
  } catch (error) { $("#health-dot").style.background = "#e26270"; }
}

async function loadInventory() {
  try {
    const data = await api("/api/data/inventory"); state.inventory = data;
    const summary = data.summary || {};
    $("#data-metrics").innerHTML = [
      ["Local files", fmtNumber(summary.total_files, 0)],
      ["Downloaded reports", `${fmtNumber(summary.downloaded_pdf_count, 0)} / ${fmtNumber(summary.report_catalog_count, 0)}`],
      ["Table schemas", fmtNumber(summary.table_catalog_count, 0)],
      ["Local size", `${fmtNumber((summary.total_bytes || 0) / 1024 / 1024 / 1024)} GB`]
    ].map(([label, value]) => `<div class="metric"><span>${label}</span><b>${value}</b></div>`).join("");
    const readiness = data.readiness || {};
    $("#data-readiness").innerHTML = Object.entries(readiness).map(([name, item]) =>
      `<div class="connection-list"><div><b>${escapeHtml(name)}</b><span class="status-text" style="color:${item.ready ? '#0d8c82' : '#b35e26'}">${item.ready ? 'READY' : 'NOT READY'}</span></div></div>`
    ).join("");
    $("#download-state").textContent = summary.partial_download_count ? `${summary.partial_download_count} downloads active` : "Download stable";
    $("#missing-data").innerHTML = `<b>当前缺口</b><br>${(data.missing || []).map(escapeHtml).join("<br>") || "未发现关键缺口"}`;
  } catch (error) { $("#data-readiness").innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`; }
}

function bindEvents() {
  $("#run-lesson").addEventListener("click", runLesson);
  $("#research-form").addEventListener("submit", startResearch);
  $("#refresh-runs").addEventListener("click", loadRuns);
  $("#index-rag").addEventListener("click", indexRag);
  $("#rag-search-form").addEventListener("submit", searchRag);
  $("#model-form").addEventListener("submit", configureModel);
  $("#multi-form").addEventListener("submit", analyzeMulti);
  $("#paper-form").addEventListener("submit", placePaperOrder);
}

async function runLesson() {
  const output = $("#lesson-output"); output.className = "empty"; output.textContent = "Quant Engine 正在计算…";
  try {
    const result = await api("/api/learn/experiment", {method: "POST", body: "{}"});
    const e = result.evaluation;
    output.className = "research-result";
    output.innerHTML = `<p>${escapeHtml(result.lesson)}</p><code>${escapeHtml(result.formula)}</code><div class="metric-mini">
      <div><span>Rank IC</span><b>${fmtNumber(e.mean_rank_ic, 4)}</b></div><div><span>ICIR</span><b>${fmtNumber(e.icir, 3)}</b></div>
      <div><span>Positive IC</span><b>${fmtPct(e.positive_ic_rate)}</b></div><div><span>Spread ann.</span><b>${fmtPct(e.top_bottom_spread_annualized)}</b></div></div>
      <p><b>为什么滞后：</b>${escapeHtml(result.why_lag)}</p><small>${escapeHtml(result.data)}</small>`;
  } catch (error) { output.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`; }
}

async function startResearch(event) {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  const live = form.get("live") === "on";
  const payload = {
    objective: form.get("objective"), agent_mode: live ? "live" : "mock", market: form.get("market"),
    universe: String(form.get("universe") || "").split(",").map(v => v.trim()).filter(Boolean),
    start_date: form.get("start_date") || null, end_date: form.get("end_date") || null,
    frequency: form.get("frequency"), holding_period: Number(form.get("holding_period")),
    factor_type: form.get("factor_type"), data_provider: form.get("data_provider"),
    use_local_reports: form.get("use_local_reports") === "on", auto_approve: true, max_iterations: 2
  };
  $("#research-mode-tag").textContent = live ? "Live AI" : "Mock safe";
  setPipeline(0); $("#run-status").textContent = "Starting";
  $("#research-output").className = "empty large"; $("#research-output").textContent = "正在创建可审计研究任务…";
  try {
    const job = await api("/api/research/runs", {method: "POST", body: JSON.stringify(payload)});
    await pollJob(job.job_id, renderResearch, stage => setPipeline(stage));
  } catch (error) { researchError(error); }
}

function setPipeline(stage) {
  const items = $$("#pipeline > div"); items.forEach((node, index) => {node.classList.toggle("done", index < stage); node.classList.toggle("active", index === stage);});
  $("#run-status").textContent = stage >= 6 ? "Completed" : `Stage ${Math.min(stage + 1, 6)} / 6`;
}

async function pollJob(jobId, completed, progress) {
  let ticks = 0;
  while (ticks < 900) {
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === "completed") { progress?.(6); completed(job.result); return; }
    if (job.status === "failed") throw new Error(job.error || "任务失败");
    progress?.(Math.min(5, Math.floor(ticks / 3))); ticks += 1;
    await new Promise(resolve => setTimeout(resolve, 1300));
  }
  throw new Error("任务等待超时，可在 Research Runs 查看后端记录");
}

function researchError(error) {
  $("#run-status").textContent = "Failed";
  $("#research-output").className = "notice error"; $("#research-output").textContent = error.message;
  toast(error.message, true);
}

function renderResearch(result) {
  state.currentRun = result; const h = result.hypothesis || {}; const e = result.evaluation || {}; const impl = result.implementation || {};
  const citations = result.citations || [];
  const dataContext = result.data_context || [];
  $("#research-output").className = "research-result";
  $("#research-output").innerHTML = `<div class="result-title"><div><small>${escapeHtml(result.run_id)}</small><h3>${escapeHtml(h.title || "Research completed")}</h3></div><span class="tag">${escapeHtml(result.status)}</span></div>
    <p>${escapeHtml(h.thesis || "")}</p><div class="metric-mini">
      <div><span>Rank IC</span><b>${fmtNumber(e.mean_rank_ic, 4)}</b></div><div><span>ICIR</span><b>${fmtNumber(e.icir, 3)}</b></div>
      <div><span>Sharpe</span><b>${fmtNumber(e.sharpe, 3)}</b></div><div><span>Max drawdown</span><b>${fmtPct(e.max_drawdown)}</b></div></div>
    <h3>Economic rationale</h3><p>${escapeHtml(h.economic_rationale || "")}</p>
    <h3>Required data</h3><div class="citation-list">${dataContext.length ? dataContext.slice(0,5).map(d => `<div class="citation"><b>${escapeHtml(d.table)} · ${escapeHtml(d.title || '')}</b><small>${d.export_ready ? 'LOCAL CSV READY' : 'METADATA ONLY'} · ${fmtNumber(d.rows,0)} rows catalogued</small></div>`).join("") : `<div class="notice">${(h.required_fields || []).map(escapeHtml).join(', ') || 'No structured fields declared'}</div>`}</div>
    <h3>Quantile test</h3><div class="quantiles">${(e.quantile_returns_annualized || []).map((value,index) => `<div><span>Q${index+1}</span><i style="height:${Math.max(8,Math.min(82,Math.abs(value)*140))}px;${value<0?'background:#c8616c':''}"></i><small>${fmtPct(value)}</small></div>`).join("")}</div>
    <h3>Validated factor code</h3><pre>${escapeHtml(impl.code || "No implementation")}</pre>
    <h3>Citations</h3><div class="citation-list">${citations.length ? citations.map(c => `<div class="citation"><b>${escapeHtml(c.title)}</b><small>${escapeHtml(c.path || "demo memory")}${c.page ? ` · p.${c.page}` : ""}</small></div>`).join("") : '<div class="notice">本次没有本地 citation；请先完成 RAG 索引并勾选参考本地研报。</div>'}</div>
    <button class="secondary" id="save-current-factor">保存到 Factor Library</button>`;
  $("#save-current-factor").addEventListener("click", () => saveFactor(result.run_id));
  loadRuns();
}

async function loadRuns() {
  try {
    const runs = await api("/api/runs?limit=100");
    $("#runs-body").innerHTML = runs.length ? runs.map(run => `<tr><td><code>${escapeHtml(run.run_id.slice(0, 10))}</code></td><td>${escapeHtml(run.objective.slice(0, 72))}</td><td><span class="status-text">${escapeHtml(run.status)}</span></td><td>${run.iteration}/${run.max_iterations}</td><td>${escapeHtml(new Date(run.updated_at).toLocaleString())}</td><td><button class="secondary view-run" data-id="${run.run_id}">查看</button></td></tr>`).join("") : '<tr><td colspan="6">暂无运行记录</td></tr>';
    $$(".view-run").forEach(button => button.addEventListener("click", () => viewRun(button.dataset.id)));
    $("#backtest-runs").innerHTML = runs.filter(r => ["accepted","rejected"].includes(r.status)).slice(0, 8).map(r => `<div class="citation"><b>${escapeHtml(r.objective.slice(0, 90))}</b><small>${r.status} · ${escapeHtml(r.run_id.slice(0, 12))}</small></div>`).join("") || "暂无完成的回测。";
  } catch (error) { $("#runs-body").innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`; }
}

async function viewRun(runId) {
  try {
    const bundle = await api(`/api/runs/${runId}`); const detail = $("#run-detail"); detail.classList.remove("hidden");
    detail.innerHTML = `<div class="panel-head"><h3>${escapeHtml(bundle.run.objective)}</h3><button class="secondary" id="save-detail-factor">保存因子</button></div><div class="metric-mini">${bundle.metrics.slice(0,8).map(m => `<div><span>${escapeHtml(m.key)}</span><b>${fmtNumber(m.value,4)}</b></div>`).join("")}</div><h3>Event trace</h3><div class="citation-list">${bundle.events.map(e => `<div class="citation"><b>${e.sequence}. ${escapeHtml(e.event_type)}</b><small>${escapeHtml(e.created_at)}</small></div>`).join("")}</div>`;
    $("#save-detail-factor").addEventListener("click", () => saveFactor(runId)); detail.scrollIntoView({behavior:"smooth"});
  } catch (error) { toast(error.message, true); }
}

async function saveFactor(runId) {
  try { await api("/api/factors", {method:"POST",body:JSON.stringify({run_id:runId})}); toast("因子已保存"); await loadFactors(); }
  catch (error) { toast(error.message, true); }
}

async function loadFactors() {
  try {
    state.factors = await api("/api/factors");
    $("#factor-grid").innerHTML = state.factors.length ? state.factors.map(f => `<article class="factor-card"><span class="tag">${escapeHtml(f.implementation.spec.output_frequency)}</span><h3>${escapeHtml(f.name)}</h3><small>${escapeHtml(f.factor_id.slice(0,12))}</small><code>${escapeHtml(f.implementation.spec.description)}</code><div class="metric-mini"><div><span>Rank IC</span><b>${fmtNumber(f.evaluation.validation?.mean_rank_ic,4)}</b></div><div><span>Coverage</span><b>${fmtPct(f.evaluation.validation?.coverage)}</b></div></div></article>`).join("") : '<div class="empty">完成研究后，将验证过的因子保存到这里。</div>';
    $("#multi-factor-list").innerHTML = state.factors.length ? state.factors.map(f => `<label><input type="checkbox" name="factor" value="${f.factor_id}"><span><b>${escapeHtml(f.name)}</b><small>${escapeHtml(f.implementation.spec.description)}</small></span></label>`).join("") : '<div class="notice">Factor Library 暂无可选因子。</div>';
  } catch (error) { toast(error.message, true); }
}

async function analyzeMulti(event) {
  event.preventDefault(); const form = new FormData(event.currentTarget); const factorIds = form.getAll("factor");
  if (factorIds.length < 2) return toast("至少选择两个因子", true);
  const output = $("#multi-output"); output.className = "empty large"; output.textContent = "正在检查相关性、冗余和组合表现…";
  try {
    const result = await api("/api/multifactor/analyze", {method:"POST",body:JSON.stringify({factor_ids:factorIds,method:form.get("method"),data_provider:"synthetic"})});
    output.className = "research-result"; output.innerHTML = `<h3>Combined performance</h3><div class="metric-mini"><div><span>Sharpe</span><b>${fmtNumber(result.backtest.sharpe,3)}</b></div><div><span>Annual return</span><b>${fmtPct(result.backtest.annualized_return)}</b></div><div><span>Max drawdown</span><b>${fmtPct(result.backtest.max_drawdown)}</b></div><div><span>Turnover</span><b>${fmtPct(result.backtest.one_way_turnover)}</b></div></div><h3>Weights</h3><div class="citation-list">${Object.entries(result.weights).map(([k,v]) => `<div class="citation"><b>${escapeHtml(k)}</b><small>${fmtPct(v)}</small></div>`).join("")}</div><h3>Redundancy</h3><div class="notice">${result.redundancy.length ? result.redundancy.map(r => `${escapeHtml(r.left)} ↔ ${escapeHtml(r.right)}: ${fmtNumber(r.correlation,3)}`).join("<br>") : "没有超过 0.80 阈值的因子对"}</div>`;
  } catch (error) { output.className="notice error"; output.textContent=error.message; }
}

async function indexRag() {
  $("#index-rag").disabled = true; $("#index-rag").textContent = "索引中…";
  try {
    const job = await api("/api/knowledge/index", {method:"POST",body:JSON.stringify({})});
    await pollJob(job.job_id, async result => {toast(`索引完成：${result.documents} 文档 / ${result.chunks} chunks`); await loadHealth();}, null);
  } catch (error) { toast(error.message, true); }
  finally { $("#index-rag").disabled=false; $("#index-rag").textContent="增量建立 RAG 索引"; }
}

async function searchRag(event) {
  event.preventDefault(); const query = new FormData(event.currentTarget).get("query"); const output=$("#rag-results"); output.textContent="检索中…";
  try { const data=await api("/api/knowledge/search",{method:"POST",body:JSON.stringify({query,limit:6})}); output.className="citation-list"; output.innerHTML=data.results.length?data.results.map(r=>`<div class="citation"><b>${escapeHtml(r.title)}${r.page?` · p.${r.page}`:""}</b><small>${escapeHtml(r.source_path||"")}</small><p>${escapeHtml(r.text.slice(0,240))}…</p></div>`).join(""):'<div class="notice">没有命中，请先索引或更换关键词。</div>'; }
  catch(error){output.innerHTML=`<div class="notice error">${escapeHtml(error.message)}</div>`;}
}

async function configureModel(event) {
  event.preventDefault(); const formElement=event.currentTarget; const form=new FormData(formElement); const output=$("#model-result"); output.className="notice"; output.textContent="正在真实调用模型检查连接…";
  const payload={provider:form.get("provider"),base_url:form.get("base_url"),model:form.get("model"),api_key:form.get("api_key"),api_style:"auto",test_connection:true};
  try {const result=await api("/api/model/config",{method:"POST",body:JSON.stringify(payload)}); output.className="notice success";output.textContent=`连接成功：${result.provider}:${result.model}。Key 仅在当前服务进程内存中。`;formElement.querySelector('[name="api_key"]').value="";await loadHealth();}
  catch(error){output.className="notice error";output.textContent=error.message;}
}

async function loadPaper() {
  try {const [account,positions,orders]=await Promise.all([api("/api/paper/account"),api("/api/paper/positions"),api("/api/paper/orders")]);$("#paper-account").innerHTML=[["Cash",account.cash],["Market value",account.market_value],["Equity",account.equity],["Mode","PAPER"]].map(([k,v])=>`<div class="metric"><span>${k}</span><b>${typeof v==='number'?`$${fmtNumber(v)}`:v}</b></div>`).join("");$("#paper-positions").className=positions.length?"citation-list":"empty";$("#paper-positions").innerHTML=positions.length?positions.map(p=>`<div class="citation"><b>${escapeHtml(p.symbol)} · ${fmtNumber(p.quantity,4)} shares</b><small>Avg $${fmtNumber(p.average_price)} · PnL $${fmtNumber(p.unrealized_pnl)}</small></div>`).join(""):"暂无模拟持仓。";$("#paper-orders").className=orders.length?"citation-list":"empty";$("#paper-orders").innerHTML=orders.length?orders.slice(0,20).map(o=>`<div class="citation"><b>${escapeHtml(o.side.toUpperCase())} ${fmtNumber(o.quantity,4)} ${escapeHtml(o.symbol)}</b><small>${escapeHtml(o.status)} · ${escapeHtml(o.order_id.slice(0,10))} · $${fmtNumber(o.fill_price||o.limit_price)}</small></div>`).join(""):"暂无订单。";}catch(error){toast(error.message,true);}
}

async function placePaperOrder(event) {
  event.preventDefault();const form=new FormData(event.currentTarget);const symbol=String(form.get("symbol")).toUpperCase();const price=Number(form.get("price"));
  try {await api("/api/paper/prices",{method:"POST",body:JSON.stringify({prices:{[symbol]:price}})});await api("/api/paper/orders",{method:"POST",body:JSON.stringify({symbol,side:form.get("side"),quantity:Number(form.get("quantity")),order_type:"market"})});toast("模拟订单已通过规则并成交");await loadPaper();}catch(error){toast(error.message,true);}
}

document.addEventListener("DOMContentLoaded", initialize);
