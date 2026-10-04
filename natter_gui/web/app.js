"use strict";
const $ = id => document.getElementById(id);
let csrf = "", state = null, editId = null, logId = null, pollBusy = false, timer = null;
let diagnosticsKey = "", scopeKey = "";
const runtimeLabels = {running:"运行中", starting:"启动中", restarting:"重启等待", stopped:"已停止"};
const wanLabels = {OPEN:"外网 OPEN", CLOSED:"外网 CLOSED", UNKNOWN:"外网 UNKNOWN", NOT_CHECKED:"外网未检测"};
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(timer); timer = setTimeout(() => $("toast").hidden = true, 5000); }
async function api(path, method = "GET", data) {
  const options = {method, credentials:"same-origin", headers:{}};
  if (method !== "GET") { options.headers["Content-Type"] = "application/json"; options.headers["X-CSRF-Token"] = csrf; options.body = JSON.stringify(data ?? {}); }
  const res = await fetch("/api" + path, options), result = await res.json();
  if (!res.ok) { if (res.status === 401 && path !== "/login") showLogin(); throw new Error(result.error || "请求失败"); }
  return result;
}
function node(tag, className, text) { const n = document.createElement(tag); if (className) n.className = className; if (text !== undefined) n.textContent = text; return n; }
function badge(text, type="") { return node("span", "badge " + type, text); }
function actionButton(text, callback, className="") { const b = node("button", className, text); b.type = "button"; b.addEventListener("click", async () => { b.disabled=true; try { await callback(); } catch(e) { toast(e.message); } finally { b.disabled=false; } }); return b; }
function formatDate(value) { return value ? new Date(value).toLocaleString() : "未记录"; }
function bytes(value) { if (value === null || value === undefined) return "不可用"; if (value < 1024) return value + " B"; const units=["KiB","MiB","GiB","TiB"]; let i=-1; do {value/=1024;i++;} while(value>=1024 && i<units.length-1); return value.toFixed(1)+" "+units[i]; }
function display(value, fallback="不可用") { return value === null || value === undefined || value === "" ? fallback : String(value); }
function navigate(page) { document.querySelector(`[data-page="${page}"]`).click(); }
function table(headers, rows) {
  const wrap=node("div","table-scroll"), t=node("table","diagnostic-table"), head=node("thead"), tr=node("tr"), body=node("tbody");
  for(const text of headers) tr.append(node("th","",text)); head.append(tr); t.append(head);
  for(const row of rows) { const r=node("tr"); for(const value of row) {const cell=node("td");cell.append(value instanceof Node?value:node("span","",display(value,"—")));r.append(cell);} body.append(r); }
  t.append(body);wrap.append(t);return wrap;
}
function facts(entries) { const list=node("dl","diagnostic-facts"); for(const [key,value] of entries)list.append(node("dt","",key),node("dd","",display(value)));return list; }
function probeLabel(value) {
  const labels={OPEN:"连接成功",CLOSED:"连接被拒绝",UNKNOWN:"无法判定",SKIPPED:"已跳过",NOT_CHECKED:"未检测"};
  const box=node("div","probe-result");box.append(badge(labels[value?.status]||"未检测",value?.status==="OPEN"?"good":value?.status==="CLOSED"?"bad":"warn"));
  if(value?.connect_ms!==undefined)box.append(node("small","",`${value.connect_ms} ms · TCP 建连`));
  if(value?.detail)box.append(node("small","",value.detail));return box;
}
async function runDiagnostics(sid) {
  await api("/diagnostics","POST",sid?{service_id:sid}:{});navigate("diagnostics");toast("网络体检已开始，服务继续运行");await refresh();
}
function renderDiagnostics() {
  const scopes=state.services.map(s=>[s.id,s.name]), key=JSON.stringify(scopes);
  if(scopeKey!==key) { const selected=$("diagnostics-scope").value;$("diagnostics-scope").replaceChildren(node("option","","全部服务与设备环境"));$("diagnostics-scope").firstChild.value="";for(const [id,name]of scopes){const option=node("option","",name);option.value=id;$("diagnostics-scope").append(option);}$("diagnostics-scope").value=scopes.some(s=>s[0]===selected)?selected:"";scopeKey=key; }
  const d=state.diagnostics||{state:"idle"}, signature=JSON.stringify(d);
  $("diagnostics-run").disabled=d.state==="running";$("diagnostics-scope").disabled=d.state==="running";$("diagnostics-export").disabled=!d.report;
  if(diagnosticsKey===signature)return;diagnosticsKey=signature;
  $("diagnostics-status").textContent=d.state==="running"?"正在采样与检查…通常在一分钟内结束":d.state==="error"?"检测异常："+d.error:d.report?`${d.stale?"历史快照 · 服务已变化，请重新检测":"检测快照"} · ${formatDate(d.report.checked_at)} · ${d.report.duration_seconds} 秒`:"尚未运行体检";
  $("diagnostics-report").hidden=$("diagnostics-summary").hidden=!d.report;$("diagnostics-empty").hidden=!!d.report;
  if(!d.report)return;
  const report=d.report,e=report.environment,route=e.effective_route,mem=e.memory;
  const memory=mem?`${((mem.total_bytes-mem.available_bytes)/mem.total_bytes*100).toFixed(1)}%`:"不可用";
  $("diagnostics-summary").replaceChildren();
  for(const [label,value,note]of [["CPU 采样占用",e.cpu_busy_percent===null?"不可用":e.cpu_busy_percent+"%",`${display(e.cpu_count)} 核 · 短时采样`],["内存使用",memory,mem?`${bytes(mem.total_bytes-mem.available_bytes)} / ${bytes(mem.total_bytes)}`:"需要 Linux /proc"],["面板实际出口",route.available?(route.gateway||"直连"):"不可用",route.available?`${route.interface} · 路由表 ${route.table}`:route.reason],["本次检测服务",report.services.length,"原版结果与 GUI 探测分别展示"]]) {const card=node("article");card.append(node("span","",label),node("strong","",value),node("small","",note));$("diagnostics-summary").append(card);}
  const env=$("diagnostics-environment");env.replaceChildren(facts([["运行环境",`${e.platform} / ${e.architecture} · Python ${e.python}`],["面板账户 UID",e.uid],["设备运行时间",e.uptime_seconds===null?"不可用":(e.uptime_seconds/3600).toFixed(1)+" 小时"],["系统默认出口",e.default_routes.map(r=>`${r.gateway||"直连"} via ${r.interface}`).join("；")||"未取得"],["面板 UID 出口",route.available?`${route.gateway||"直连"} via ${route.interface} · 源地址 ${display(route.source)}`:route.reason],["负载平均值",e.load_average?.map(v=>v.toFixed(2)).join(" / ")||"不可用"]]));
  for(const note of e.notes)env.append(node("p","footnote",note));
  const details=node("details"), summary=node("summary","","查看 IPv4 策略路由");details.append(summary,node("pre","",JSON.stringify(e.rules,null,2)));env.append(details);
  const interfaces=$("diagnostics-interfaces");interfaces.replaceChildren();
  if(e.interfaces.length)interfaces.append(table(["网卡 / IPv4","状态 / MTU","本地链路","采样接收 / 发送","错误 / 丢弃"],e.interfaces.map(i=>[`${i.name}\n${i.addresses.join("\n")||"无 IPv4"}`,`${i.state} / ${display(i.mtu)}`,i.link_mbps?`${i.link_mbps} Mbps · ${display(i.duplex)}`:"未取得协商速率",`${display(i.rx_mbps)} / ${display(i.tx_mbps)} Mbps`,`${display(i.rx_errors)} / ${display(i.tx_errors)} 错误\n${display(i.rx_dropped)} / ${display(i.tx_dropped)} 丢弃`])));else interfaces.append(node("p","","当前环境未取得网卡信息。"));
  const services=$("diagnostics-services");services.replaceChildren();
  if(!report.services.length)services.append(node("article","panel","没有配置服务；已完成设备环境采样。"));
  for(const s of report.services) {
    const card=node("article","panel diagnostic-service"), heading=node("div","panel-heading");heading.append(node("h2","",s.name),badge("GUI 探测 + 原版快照"));card.append(heading,node("p","",`目标 ${s.target} · ${s.enabled?"已启用":"已停用"} · ${formatDate(s.checked_at)}`));
    const target=node("div","target-probe");target.append(node("span","",s.workers.some(w=>w.protocol==="tcp")?"目标 TCP 端口":"目标 UDP 端口"),probeLabel(s.target_probe));
    if(s.target_probe.http)target.append(node("small","",s.target_probe.http.code?`HTTP ${s.target_probe.http.code} · 目标应用已响应，不代表登录或播放成功`:`HTTP 无法判定 · ${s.target_probe.http.detail}`));card.append(target);
    for(const w of s.workers) {
      const block=node("div","diagnostic-worker"), title=node("div","worker-head");title.append(node("strong","",w.protocol.toUpperCase()),badge(runtimeLabels[w.runtime]||w.runtime,w.runtime==="running"?"good":""));block.append(title);
      const mapping=w.mapping_active&&w.mapping?`${w.mapping.public_ip}:${w.mapping.public_port}`:"无活动映射";
      const upnp=!w.upnp.requested?"未启用":w.upnp.error?"原版日志有 UPnP 错误":w.upnp.router?`发现路由 ${w.upnp.router}，租约未独立核实`:"已启用，尚无路由发现记录";
      block.append(facts([["当前公网入口",mapping],["实际绑定地址",w.local_address?`${w.local_address.ip}:${w.local_address.port}`:"未取得"],["进程 / 自动重启",`${display(w.pid,"—")} / ${w.restarts} 次`],["线程 / 上限",w.protocol==="tcp"?`${display(w.threads)} / ${w.thread_limit}`:display(w.threads)],["映射更新时间",formatDate(w.mapping?.updated_at)],["UPnP",upnp]]));
      if(w.forward_error)block.append(node("p","core-warning",`转发错误累计 ${w.forward_error_count} 次 · ${formatDate(w.forward_error.at)} · ${w.forward_error.message}`));
      if(w.upnp.error)block.append(node("p","core-warning",w.upnp.error));
      const original=w.protocol==="udp"?badge("原版没有 UDP WAN 检查","warn"):badge(w.original_wan,w.original_wan==="OPEN"?"good":w.original_wan==="CLOSED"?"bad":"warn");
      block.append(table(["检测项","结果","检测来源 / 含义"],[["本地转发端口",probeLabel(w.bind_probe),w.protocol==="tcp"?"GUI 从本机发起 TCP 连接":"UDP 需协议回包或应用验证"],["公网地址回环",probeLabel(w.public_lan_probe),w.protocol==="tcp"?"GUI 从内网连接公网入口；失败不等于外网失败":"未执行通用 UDP 连通检查"],["原版 WAN",original,`原版运行日志快照 · ${formatDate(w.original_checked_at)}`]]));
      const conn=w.connections;
      if(conn?.available){if(conn.items.length){const details=node("details");details.append(node("summary","",`查看 TCP 连接快照（显示 ${conn.items.length} 条，最多 16 条）`),table(["对端","TCP 延迟","发送 / 接收积压","重传数据 / 接收窗口受限"],conn.items.map(c=>[c.peer,c.rtt_ms===undefined?"未取得":c.rtt_ms+" ms",`${bytes(c.send_queue)} / ${bytes(c.receive_queue)}`,`${bytes(c.retransmitted_bytes)} / ${c.receive_window_limited_percent===undefined?"未记录":c.receive_window_limited_percent+"%"}`])));block.append(details);}else block.append(node("p","footnote","采样时没有此监听端口的已建立 TCP 连接。"));}
      else if(conn)block.append(node("p","footnote",conn.reason));
      card.append(block);
    }
    const verification=s.manual_verification;card.append(node("p","footnote",verification?`${verification.stale?"历史":"当前"}人工外网验证：${verification.success?"成功":"未成功"} · ${formatDate(verification.updated_at)}`:"人工外网验证：尚未记录；本页 GUI 探测不替代独立外网测试。"));services.append(card);
  }
}
function showLogin() { csrf=""; $("login").hidden=false; $("console").hidden=true; for (const d of [$("editor"),$("log-dialog")]) if (d.open) d.close(); }
function showConsole() { $("login").hidden=true; $("console").hidden=false; refresh(); }
function editor(service=null) {
  editId = service?.id ?? null; $("service-form").reset(); $("preset").value="custom";
  $("editor-title").textContent = service ? "编辑服务" : "新增服务";
  if (service) for (const field of ["name","target_ip","target_port","bind_ip","bind_port","protocol","keepalive","tcp_thread_limit","upnp","retry_target","enabled"]) {
    const element=$("service-form").elements[field]; if (element.type==="checkbox") element.checked=service[field]; else element.value=service[field];
  }
  $("editor").showModal(); $("service-form").elements.name.focus();
}
async function act(id, action) { await api(`/services/${id}/action`,"POST",{action}); toast("服务状态已更新"); await refresh(); }
function renderServices() {
  if (!state) return;
  const services=state.services, search=$("search").value.toLowerCase();
  $("count-total").textContent=services.length;
  $("count-running").textContent=services.filter(s=>s.workers.some(w=>w.runtime==="running")).length;
  $("count-open").textContent=services.filter(s=>s.workers.some(w=>w.runtime==="running" && w.wan==="OPEN")).length;
  $("count-unknown").textContent=services.filter(s=>s.enabled && s.workers.some(w=>["UNKNOWN","NOT_CHECKED"].includes(w.wan))).length;
  $("list-count").textContent=services.length; $("empty").hidden=services.length!==0;
  $("services").replaceChildren();
  for (const s of services.filter(s=>(s.name+" "+s.target_ip).toLowerCase().includes(search))) {
    const card=node("article","service-card"), heading=node("div","card-heading"), name=node("div","card-name"), text=node("div");
    text.append(node("h2","",s.name),node("small","",`${s.target_ip}:${s.target_port}`));
    name.append(node("span","service-icon",s.protocol==="both"?"⇄":"↗"),text);
    heading.append(name,badge(s.enabled?"已启用":"已停用",s.enabled?"good":"")); card.append(heading);
    for (const w of s.workers) {
      const section=node("div","worker"), header=node("div","worker-head");
      header.append(node("span","",w.protocol.toUpperCase()),badge(runtimeLabels[w.runtime]||w.runtime,w.runtime==="running"?"good":w.runtime==="stopped"?"":"warn"));
      section.append(header);
      if (w.mapping && w.mapping_active) {
        const address=`${w.mapping.public_ip}:${w.mapping.public_port}`, line=node("div","address");
        if (w.protocol==="tcp" && [8096,32400].includes(s.target_port)) {
          const link=node("a","",address+" ↗"); link.href=`http://${address}${s.target_port===32400?"/web":"/"}`; link.target="_blank"; link.rel="noopener noreferrer"; line.append(link);
        } else line.textContent=address;
        section.append(line);
      } else section.append(node("div","worker-note",w.runtime==="stopped"?"服务停止，当前没有活动映射":"等待上游返回映射信息…"));
      section.append(badge(w.protocol==="udp"?"原版未提供 UDP WAN 检查":(wanLabels[w.wan]||w.wan),w.wan==="OPEN"?"good":w.wan==="CLOSED"?"bad":"warn"));
      for (const [address,result] of Object.entries(w.lan||{})) {const label=address===`${s.target_ip}:${s.target_port}`?"目标服务":w.local_address&&address===`${w.local_address.ip}:${w.local_address.port}`?"本地转发":"公网地址回环";section.append(node("p","worker-note",`${label} · LAN ${address} · ${result}`));}
      if(w.upnp?.router)section.append(node("p","worker-note",`UPnP 发现路由 ${w.upnp.router} · 租约未独立核实`));
      if (w.protocol==="tcp")section.append(node("p","worker-note",`TCP 线程：${display(w.threads)} / ${w.thread_limit}`));
      if(w.forward_error)section.append(node("p","core-warning",`转发错误累计 ${w.forward_error_count} 次 · 最近 ${formatDate(w.forward_error.at)} · ${w.forward_error.message.includes("Too many threads")?"已达到当前线程上限，新连接可能被拒绝；可在编辑服务中提高 TCP 线程上限。":w.forward_error.message}`));
      if (w.core_warning) section.append(node("p","core-warning",w.core_warning));
      if (w.last_error) section.append(node("p","core-warning",w.last_error));
      if (w.pid) section.append(node("p","worker-note",`PID ${w.pid} · 自动重启 ${w.restarts} 次`));
      card.append(section);
    }
    if (s.manual_verification) card.append(node("p","worker-note",`${s.manual_verification.stale?"历史人工验证":"人工外网验证"}：${s.manual_verification.success?"成功":"未成功"} · ${new Date(s.manual_verification.updated_at).toLocaleString()}`));
    const verify=node("div","verify-actions");
    verify.append(actionButton("记录外网成功",async()=>{await api(`/services/${s.id}/verify`,"POST",{success:true,note:"用户通过外网连接确认"}); await refresh();}),actionButton("记录未连通",async()=>{await api(`/services/${s.id}/verify`,"POST",{success:false});await refresh();}));
    card.append(verify);
    const actions=node("div","card-actions");
    actions.append(actionButton(s.enabled?"停止":"启动",()=>act(s.id,s.enabled?"stop":"start")),actionButton("重启",()=>act(s.id,"restart")),actionButton("编辑",()=>editor(s)),actionButton("日志",async()=>{logId=s.id;$("log-title").textContent=s.name+" · 原版日志";$("log-dialog").showModal();await refreshLogs();}),actionButton("诊断",async()=>{await runDiagnostics(s.id);$("diagnostics-scope").value=s.id;}),actionButton("删除",async()=>{if(confirm(`删除「${s.name}」的端口映射？目标软件会保留。`)){await api(`/services/${s.id}`,"DELETE");toast("已删除服务；UPnP 映射按租期回收");await refresh();}},"delete"));
    card.append(actions); $("services").append(card);
  }
}
async function refreshLogs() { if (!logId) return; const logs=await api(`/services/${logId}/logs`); $("logs").textContent=Object.entries(logs).map(([p,t])=>`[${p.toUpperCase()}]\n${t||"暂无日志"}`).join("\n\n"); }
async function refresh() {
  if (!csrf || pollBusy) return; pollBusy=true;
  try {
    const next=await api("/state"), changed=JSON.stringify(next.services)!==JSON.stringify(state?.services); state=next;
    $("connection").textContent="已连接"; $("side-version").textContent="v"+state.gui_version;
    $("side-core-version").textContent=state.upstream.tag;
    if (changed) renderServices();
    $("gui-version").textContent="v"+state.gui_version; $("core-version").textContent=state.upstream.tag;
    $("core-sha").textContent=state.upstream.commit; $("platform").textContent=state.platform;
    renderDiagnostics();
    const n=state.nat; $("nat-status").textContent=({idle:"尚未运行检测",running:"检测正在运行…",finished:"检测已结束，请查看各项结果",error:"检测未正常完成"})[n.state]+(n.finished_at?" · "+formatDate(n.finished_at):"");
    $("nat-check").disabled=n.state==="running"; $("nat-raw").textContent=n.raw||"检测可能需要数十秒；最多运行 150 秒。";
    $("nat-results").replaceChildren(...n.results.map(r=>{const card=node("div","nat-result"),heading=node("div","panel-heading");heading.append(node("strong","",r.protocol.toUpperCase()),badge(r.status,r.status==="OK"?"good":"warn"));card.append(heading,node("h3","",r.title||r.detail),node("p","",r.explanation||"查看原版输出。"),node("small","",r.detail));return card;}));
    const u=state.update; $("update-check").disabled=u.state==="running";
    $("update-state").textContent=u.state==="finished"?`上游最新 Release：${u.tag} · ${u.tag===state.upstream.tag?"与当前核心相同":"存在版本差异，请查看发布说明"}`:u.state==="running"?"正在查询 GitHub…":u.state==="error"?"检查失败："+u.error:"尚未检查上游更新";
  } catch(e) { $("connection").textContent="连接中断"; if(csrf) toast(e.message); } finally {pollBusy=false;}
}
$("login-form").addEventListener("submit",async e=>{e.preventDefault();try {const r=await api("/login","POST",{password:e.target.elements.password.value});csrf=r.csrf;e.target.reset();$("login-error").textContent="";state=null;showConsole();}catch(error){$("login-error").textContent=error.message;}});
for(const id of ["add","empty-add"]) $(id).addEventListener("click",()=>editor());
for(const id of ["editor-close","editor-cancel"]) $(id).addEventListener("click",()=>$("editor").close());
$("log-close").addEventListener("click",()=>$("log-dialog").close()); $("log-refresh").addEventListener("click",()=>refreshLogs().catch(e=>toast(e.message)));
$("search").addEventListener("input",renderServices);
$("preset").addEventListener("change",e=>{const p={emby:["Emby",8096,"tcp",34569],plex:["Plex",32400,"tcp",34570],qb:["qBittorrent",64046,"both",64046]}[e.target.value];if(p){const f=$("service-form").elements;f.name.value=p[0];f.target_port.value=p[1];f.protocol.value=p[2];f.bind_port.value=p[3];}});
$("service-form").addEventListener("submit",async e=>{e.preventDefault();const f=e.target.elements,data={};for(const key of ["name","target_ip","bind_ip","protocol"])data[key]=f[key].value;for(const key of ["target_port","bind_port","keepalive","tcp_thread_limit"])data[key]=Number(f[key].value);for(const key of ["upnp","retry_target","enabled"])data[key]=f[key].checked;const b=e.submitter;b.disabled=true;try{await api(editId?`/services/${editId}`:"/services",editId?"PUT":"POST",data);$("editor").close();toast("服务配置已保存");await refresh();}catch(error){toast(error.message);}finally{b.disabled=false;}});
for (const n of document.querySelectorAll("[data-page]")) n.addEventListener("click",()=>{for(const p of document.querySelectorAll(".page"))p.hidden=p.id!=="page-"+n.dataset.page;for(const b of document.querySelectorAll("[data-page]"))b.classList.toggle("active",b===n);$("breadcrumb").textContent=n.textContent.trim();});
$("nat-check").addEventListener("click",async()=>{try{await api("/nat-check","POST");await refresh();}catch(e){toast(e.message);}});
$("diagnostics-run").addEventListener("click",async()=>{const b=$("diagnostics-run");b.disabled=true;try{await runDiagnostics($("diagnostics-scope").value);}catch(e){toast(e.message);}finally{b.disabled=state?.diagnostics?.state==="running";}});
$("diagnostics-export").addEventListener("click",()=>{if(!state?.diagnostics?.report)return;const value={gui_version:state.gui_version,upstream:state.upstream,nat:state.nat,diagnostics:state.diagnostics},url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"})),a=node("a");a.href=url;a.download="natter-diagnostics.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$("update-check").addEventListener("click",async()=>{try{await api("/upstream-check","POST");await refresh();}catch(e){toast(e.message);}});
$("logout").addEventListener("click",async()=>{try{await api("/logout","POST");showLogin();}catch(e){toast(e.message);}});
$("export").addEventListener("click",async()=>{try{const value=await api("/export"),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"})),a=node("a");a.href=url;a.download="natter-services.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){toast(e.message);}});
$("import").addEventListener("change",async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>131072)throw new Error("配置文件超过 128 KiB");const data=JSON.parse(await file.text());const added=await api("/import","POST",data);toast(`已导入 ${added.length} 个服务，默认停用`);await refresh();}catch(error){toast(error.message);}finally{e.target.value="";}});
$("password-form").addEventListener("submit",async e=>{e.preventDefault();try{await api("/password","POST",{current_password:e.target.elements.current_password.value,new_password:e.target.elements.new_password.value});e.target.reset();showLogin();toast("密码已修改，请重新登录");}catch(error){toast(error.message);}});
api("/session").then(s=>{if(s.authenticated){csrf=s.csrf;showConsole();}}).catch(e=>{$("login-error").textContent=e.message;});
setInterval(refresh,3000);
