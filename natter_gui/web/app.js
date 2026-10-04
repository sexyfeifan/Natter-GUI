"use strict";
const $ = id => document.getElementById(id);
let csrf = "", state = null, editId = null, logId = null, pollBusy = false, timer = null;
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
function showLogin() { csrf=""; $("login").hidden=false; $("console").hidden=true; for (const d of [$("editor"),$("log-dialog")]) if (d.open) d.close(); }
function showConsole() { $("login").hidden=true; $("console").hidden=false; refresh(); }
function editor(service=null) {
  editId = service?.id ?? null; $("service-form").reset(); $("preset").value="custom";
  $("editor-title").textContent = service ? "编辑服务" : "新增服务";
  if (service) for (const field of ["name","target_ip","target_port","bind_ip","bind_port","protocol","keepalive","upnp","retry_target","enabled"]) {
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
      for (const [address,result] of Object.entries(w.lan||{})) section.append(node("p","worker-note",`LAN ${address} · ${result}`));
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
    actions.append(actionButton(s.enabled?"停止":"启动",()=>act(s.id,s.enabled?"stop":"start")),actionButton("重启",()=>act(s.id,"restart")),actionButton("编辑",()=>editor(s)),actionButton("日志",async()=>{logId=s.id;$("log-title").textContent=s.name+" · 原版日志";$("log-dialog").showModal();await refreshLogs();}),actionButton("删除",async()=>{if(confirm(`删除「${s.name}」的端口映射？目标软件会保留。`)){await api(`/services/${s.id}`,"DELETE");toast("已删除服务；UPnP 映射按租期回收");await refresh();}},"delete"));
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
    const n=state.nat; $("nat-status").textContent=({idle:"尚未运行检测",running:"检测正在运行…",finished:"原版检测已完成",error:"检测未正常完成"})[n.state];
    $("nat-check").disabled=n.state==="running"; $("nat-raw").textContent=n.raw||"检测可能需要数十秒；最多运行 150 秒。";
    $("nat-results").replaceChildren(...n.results.map(r=>badge(`${r.protocol.toUpperCase()} · ${r.status} · ${r.detail}`,r.status==="OK"?"good":"warn")));
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
$("service-form").addEventListener("submit",async e=>{e.preventDefault();const f=e.target.elements,data={};for(const key of ["name","target_ip","bind_ip","protocol"])data[key]=f[key].value;for(const key of ["target_port","bind_port","keepalive"])data[key]=Number(f[key].value);for(const key of ["upnp","retry_target","enabled"])data[key]=f[key].checked;const b=e.submitter;b.disabled=true;try{await api(editId?`/services/${editId}`:"/services",editId?"PUT":"POST",data);$("editor").close();toast("服务配置已保存");await refresh();}catch(error){toast(error.message);}finally{b.disabled=false;}});
for (const n of document.querySelectorAll("[data-page]")) n.addEventListener("click",()=>{for(const p of document.querySelectorAll(".page"))p.hidden=p.id!=="page-"+n.dataset.page;for(const b of document.querySelectorAll("[data-page]"))b.classList.toggle("active",b===n);$("breadcrumb").textContent=n.textContent.trim();});
$("nat-check").addEventListener("click",async()=>{try{await api("/nat-check","POST");await refresh();}catch(e){toast(e.message);}});
$("update-check").addEventListener("click",async()=>{try{await api("/upstream-check","POST");await refresh();}catch(e){toast(e.message);}});
$("logout").addEventListener("click",async()=>{try{await api("/logout","POST");showLogin();}catch(e){toast(e.message);}});
$("export").addEventListener("click",async()=>{try{const value=await api("/export"),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"})),a=node("a");a.href=url;a.download="natter-services.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){toast(e.message);}});
$("import").addEventListener("change",async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>131072)throw new Error("配置文件超过 128 KiB");const data=JSON.parse(await file.text());const added=await api("/import","POST",data);toast(`已导入 ${added.length} 个服务，默认停用`);await refresh();}catch(error){toast(error.message);}finally{e.target.value="";}});
$("password-form").addEventListener("submit",async e=>{e.preventDefault();try{await api("/password","POST",{current_password:e.target.elements.current_password.value,new_password:e.target.elements.new_password.value});e.target.reset();showLogin();toast("密码已修改，请重新登录");}catch(error){toast(error.message);}});
api("/session").then(s=>{if(s.authenticated){csrf=s.csrf;showConsole();}}).catch(e=>{$("login-error").textContent=e.message;});
setInterval(refresh,3000);
