const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fmtDt(v){if(!v||v==='—')return v||'—';const d=new Date(v);if(isNaN(d))return String(v);return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})+' · '+d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true})}
function fmtTime(v){if(!v)return '';const d=new Date(v);if(isNaN(d))return String(v);return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true})}
function stateBadge(s){const map={WAITING_APPROVAL:'warning',COMPLETED:'darkgreen',READY_FOR_PROMOTION:'success',TEST_PASSED:'cyan',IMPLEMENTING:'info',BUILD_VALIDATED:'info',PLAN_APPROVED:'orange',REMEDIATING:'error',VARIANCE_REVIEW:'cyan',DIFFERENTIAL_TESTING:'cyan',ACE_ANALYSED:'purple',DORMANCY_ASSESSED:'teal',FRAMEWORK_BASELINED:'purple',INTAKE_VALIDATED:'teal',CREATED:'neutral',FAILED:'error',ROLLED_BACK:'error',WAITING_G0:'warning',WAITING_G1:'warning',WAITING_G2:'warning',WAITING_G3:'warning',WAITING_G4:'warning',WAITING_G5:'warning'};const cls=map[s]||'neutral';return `<span class="badge badge-${cls}"><span class="badge-dot"></span>${esc(s.replace(/_/g,' '))}</span>`}
function progressFillClass(state,pct){const s=String(state||'').toUpperCase();const p=s in STATE_PCT?STATE_PCT[s]:(pct||0);if(s==='COMPLETED'||p>=100)return ' darkgreen';if(s==='FAILED'||s==='ROLLED_BACK')return ' error';if(s==='READY_FOR_PROMOTION')return ' done';if(s==='TEST_PASSED')return ' cyan';if(s.startsWith('WAITING_'))return ' orange';if(s==='PLAN_APPROVED')return ' orange';if(s==='REMEDIATING')return ' error';if(['DIFFERENTIAL_TESTING','VARIANCE_REVIEW'].includes(s))return ' cyan';if(['FRAMEWORK_BASELINED','ACE_ANALYSED'].includes(s))return ' purple';if(['INTAKE_VALIDATED','DORMANCY_ASSESSED'].includes(s))return ' teal';if(['IMPLEMENTING','BUILD_VALIDATED'].includes(s))return ' blue';if(s==='CREATED')return ' neutral';return ''}
const STATE_PCT={'CREATED':0,'INTAKE_VALIDATED':8,'DORMANCY_ASSESSED':17,'FRAMEWORK_BASELINED':25,'ACE_ANALYSED':33,'PLAN_APPROVED':42,'IMPLEMENTING':50,'BUILD_VALIDATED':58,'DIFFERENTIAL_TESTING':67,'VARIANCE_REVIEW':75,'REMEDIATING':83,'READY_FOR_PROMOTION':92,'COMPLETED':100,'WAITING_G0':25,'WAITING_G1':25,'WAITING_G2':33,'WAITING_G3':42,'WAITING_G4':75,'WAITING_G5':92};
function dispPct(state,pct){const s=String(state||'').toUpperCase();if(s in STATE_PCT)return STATE_PCT[s];return pct||0}
function riskBadge(r){const m={high:'error',medium:'warning',low:'success'};const key=r?String(r).toLowerCase():'';if(!key||key==='none'||key==='—')return `<span style="color:var(--text-3);font-size:12px">—</span>`;const cls=m[key]||'neutral';return `<span class="badge badge-${cls}"><span class="badge-dot"></span>${esc(String(r).toUpperCase())}</span>`}
const pages=['dashboard','pipelines','approval-governance','approval-inbox','jobs','approvals','evidence','agent-registry','agent-health','agent-templates','specifications','comparisons','variances','rca','remediations','events','versions','clarifications','intakes','dormancy','frameworks','analyses','plans','generated','quality-runs','differential-packages','differential-evidence','audit-ledger','security-findings','metrics','service-registry','prompt-registry','model-registry','model-requests','knowledge-index','delivery-operations','notifications','observability-events','retention-policies','skill-registry'];
let refreshTimer;
const token=()=>sessionStorage.getItem('access_token')||'';
function claimsOf(t){try{const s=String(t).split('.');if(s.length!==3)return null;const b=s[1].replace(/-/g,'+').replace(/_/g,'/'),full=b+'='.repeat((4-b.length%4)%4);return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(full),c=>c.charCodeAt(0))))}catch(e){return null}}
async function api(u,o={}){
 // Send no Authorization header rather than an empty one. "Bearer " with no
 // token reads as a malformed token, and clients that strip the trailing space
 // send "Bearer", which the server reports as bearer_token_required -- both
 // look like a server fault when the real state is "not signed in".
 const h={'Content-Type':'application/json'},t=token();if(t)h.Authorization='Bearer '+t;
 const r=await fetch(u,{...o,headers:h});let d={};try{d=await r.json()}catch(e){}
 if(r.status===401){const e=Error(d.error||'unauthorized');e.reauth=true;throw e}
 if(r.status===403){const e=Error(d.error||'forbidden');e.forbidden=true;throw e}
 if(!r.ok)throw Error(d.error||'request_failed');return d}
function notice(m,b=false){$('#notice').innerHTML=m?`<div class="notice ${b?'bad':''}">${esc(m)}</div>`:''}
let session=null,githubState=null;
const initials=n=>String(n||'').trim().split(/\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase()||'?';
function renderIdentity(){
 const chip=$('#userChip'),gh=$('#ghIndicator'),newBtn=$('#new');
 if(!session){chip.hidden=true;gh.hidden=true;newBtn.hidden=true;$('#userMenu').hidden=true;return}
 const u=session.user;chip.hidden=false;newBtn.hidden=false;
 $('#userAvatar').textContent=initials(u.name);$('#userName').textContent=u.name;$('#userRole').textContent=u.role.replaceAll('_',' ');
 $('#menuName').textContent=u.name;$('#menuEmail').textContent=u.email;
 const c=claimsOf(token()),mins=c?Math.floor(((c.exp||0)*1000-Date.now())/60000):0;
 const statusHtml=s=>s==='ACTIVE'?`<span class="menu-status active">ACTIVE</span>`:esc(s||'—');
 $('#menuDetail').innerHTML=[['Primary role',u.role.replaceAll('_',' ')],['All roles',(u.roles||[]).join(', ')||'none'],
  ['Status',null,statusHtml(u.status)],['Last sign-in',u.last_login_at?fmtDt(u.last_login_at):'first session'],
  ['Session',mins>0?mins+' minutes left':'expired']].map(([k,v,raw])=>`<dt>${esc(k)}</dt><dd>${raw??esc(v??'—')}</dd>`).join('');
 // The indicator reflects the verified result of a real call to GitHub, not
 // merely whether a URL and token happen to be stored.
 const s=(githubState&&githubState.status)||'NOT_CONFIGURED';
 const look={CONNECTED:['ok','GitHub connected'],FAILED:['bad','GitHub error'],UNVERIFIED:['warn','GitHub unverified'],NOT_CONFIGURED:['warn','GitHub not connected']}[s]||['warn','GitHub'];
 gh.hidden=false;gh.className='ghchip '+look[0];
 if(s==='CONNECTED'&&githubState.account_login){
  gh.innerHTML=`<span class="ghchip-label">GitHub</span><span class="ghchip-sep">·</span><span class="ghchip-acct">${esc(githubState.account_login)}</span>`;
 }else{
  gh.textContent=look[1];
 }
 gh.title=s==='FAILED'&&githubState.last_error?githubState.last_error:(githubState&&githubState.url)||'No GitHub connection stored';
}
// ---- Signup role multi-select dropdown ------------------------------------
function rsClose(){
 const w=document.getElementById('roleSel'),p=document.getElementById('rolePanel');
 if(!w||!p||p.hidden)return;
 p.hidden=true;w.classList.remove('open');
 document.getElementById('roleTrigger')?.setAttribute('aria-expanded','false')}
function initRoleSelect(){
 const wrap=$('#roleSel'),trigger=$('#roleTrigger'),panel=$('#rolePanel'),
       list=$('#roleCheckboxes'),all=$('#roleAll'),txt=$('#roleTriggerText');
 if(!wrap||!trigger||!panel||!list||!txt)return;
 const opt=x=>`<label class="rsel-opt"><input type="checkbox" name="roles" value="${esc(x)}"><span class="rsel-box"></span><span class="rsel-txt">${esc(x.replaceAll('_',' '))}</span></label>`;
 const boxes=()=>[...list.querySelectorAll('input[name="roles"]')];
 function sync(){
  const b=boxes(),picked=b.filter(c=>c.checked);
  txt.textContent=!picked.length?'Select roles…'
   :picked.length===1?picked[0].value.replaceAll('_',' ')
   :picked.length+' roles selected';
  trigger.classList.toggle('placeholder',!picked.length);
  if(all){all.checked=!!b.length&&picked.length===b.length;all.indeterminate=picked.length>0&&picked.length<b.length}}
 api('/api/v2/auth/roles')
  .then(r=>{list.innerHTML=(r.items||[]).map(opt).join('');sync()})
  .catch(()=>{list.innerHTML=opt('MIGRATION_VIEWER');sync()});
 trigger.onclick=e=>{e.stopPropagation();
  const open=wrap.classList.toggle('open');panel.hidden=!open;
  trigger.setAttribute('aria-expanded',String(open))};
 list.onchange=sync;
 if(all)all.onchange=()=>{boxes().forEach(c=>{c.checked=all.checked});sync()};
 // Bound once for the page lifetime: authScreen re-renders its markup, so a
 // per-render listener would accumulate on every sign-in/sign-up swap.
 if(!window.__rselBound){window.__rselBound=true;
  document.addEventListener('click',e=>{const w=document.getElementById('roleSel');if(w&&!w.contains(e.target))rsClose()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')rsClose()})}}
function authScreen(mode,message,bad){
 $('#metrics').innerHTML='';$('#nav').innerHTML='';$('#title').textContent=mode==='signup'?'Create account':'Sign in';$('#panelBackdrop').classList.remove('open');$('#outputPanel').classList.remove('open');document.body.classList.add('auth-mode');
 const signup=mode==='signup';
 $('#view').innerHTML=`<div class="auth-page"><div class="auth-panel"><div class="auth-panel-deco" aria-hidden="true"><svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 480 600' fill='none' stroke='white' preserveAspectRatio='xMidYMid slice'><circle cx='460' cy='70' r='210' stroke-width='1' stroke-dasharray='8 18'/><rect x='322' y='-12' width='198' height='182' rx='1.5' stroke-width='0.8'/><line x1='362' y1='22' x2='480' y2='22' stroke-width='0.7'/><line x1='362' y1='22' x2='362' y2='138' stroke-width='0.7'/><line x1='356' y1='22' x2='368' y2='22' stroke-width='0.7'/><line x1='362' y1='16' x2='362' y2='28' stroke-width='0.7'/><line x1='-8' y1='558' x2='198' y2='382' stroke-width='0.6'/><polyline points='-8,528 78,528 78,476 162,476 162,542 208,542' stroke-width='0.9'/><circle cx='78' cy='528' r='3.5' fill='white' stroke='none'/><circle cx='162' cy='476' r='3.5' fill='white' stroke='none'/><circle cx='26' cy='314' r='22' stroke-width='0.7' stroke-dasharray='3 6'/><line x1='0' y1='314' x2='52' y2='314' stroke-width='0.65'/><line x1='26' y1='290' x2='26' y2='338' stroke-width='0.65'/><polyline points='432,562 468,562 468,600' stroke-width='1.3'/></svg></div><div class="auth-panel-inner"><div class="auth-panel-logo"><div class="auth-panel-logo-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.92)" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="6" x2="14" y2="6"/><line x1="2" y1="12" x2="14" y2="12"/><line x1="2" y1="18" x2="14" y2="18"/><polyline points="17 6 22 12 17 18"/></svg></div><span>ACE Modernization Accelerator</span></div><h1 class="auth-panel-headline">Enterprise ACE to<br>Spring Boot Migration</h1><p class="auth-panel-desc">AI-driven agents, governed workflows and full audit traceability — purpose-built for enterprise modernisation at scale.</p><div class="auth-panel-stats"><div class="auth-stat"><div class="auth-stat-n">13</div><div class="auth-stat-l">AI&nbsp;Agents</div></div><div class="auth-stat"><div class="auth-stat-n">6</div><div class="auth-stat-l">Gov&nbsp;Gates</div></div><div class="auth-stat"><div class="auth-stat-n">100%</div><div class="auth-stat-l">Audit&nbsp;Trail</div></div></div><ul class="auth-panel-list"><li>13 specialized AI migration agents</li><li>6-gate governance framework</li><li>Automated code generation &amp; testing</li><li>Full audit trail &amp; evidence package</li></ul><div class="auth-panel-badge">ACE · Enterprise Platform · CVS Health</div></div></div><div class="auth-right"><div class="authcard"><div class="auth-brand"><div class="auth-brand-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.92)" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="6" x2="14" y2="6"/><line x1="2" y1="12" x2="14" y2="12"/><line x1="2" y1="18" x2="14" y2="18"/><polyline points="17 6 22 12 17 18"/></svg></div><div><div class="auth-brand-name">ACE Modernization Accelerator</div><div class="auth-brand-sub">ACE · Enterprise Platform</div></div></div><div class="auth-body">${message?`<div class="notice ${bad?'bad':''}">${esc(message)}</div>`:''}<div class="authhead"><h2>${signup?'Create your account':'Welcome back'}</h2><p>${signup?'Registration stores your details in the factory database. Approver roles are granted by an administrator.':'Sign in with the email and password registered for this factory.'}</p></div><form id="authForm" class="authform">${signup?`<label>Full name<input name="name" required minlength="2" autocomplete="name" placeholder="Jane Smith"></label><div class="auth-roles-section"><span class="auth-roles-label">Roles</span><div class="rsel" id="roleSel"><button type="button" class="rsel-trigger placeholder" id="roleTrigger" aria-haspopup="true" aria-expanded="false"><span id="roleTriggerText">Select roles…</span><svg class="rsel-caret" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg></button><div class="rsel-panel" id="rolePanel" hidden><label class="rsel-opt rsel-opt-all"><input type="checkbox" id="roleAll"><span class="rsel-box"></span><span class="rsel-txt">Select all</span></label><div class="rsel-list" id="roleCheckboxes"></div></div></div><p class="authhint">Pick one or more. Approver roles are granted by an administrator after registration.</p></div>`:''}<label>Email<input name="email" type="email" required autocomplete="${signup?'email':'username'}" spellcheck="false" placeholder="you@cvshealth.com"></label><label>Password<input name="password" type="password" required minlength="12" autocomplete="${signup?'new-password':'current-password'}" placeholder="${signup?'Min. 12 characters':'••••••••••••'}"></label>${signup?'<p class="authhint">Minimum 12 characters.</p>':''}<p class="authhint bad" id="authFormError"></p><button id="authSubmit">${signup?'Create account':'Sign in'}</button></form><p class="authswap">${signup?'Already registered? <button type="button" id="swapAuth">Sign in</button>':'No account yet? <button type="button" id="swapAuth">Create one</button>'}</p></div></div></div></div>`;
 $('#swapAuth').onclick=()=>authScreen(signup?'login':'signup');
 if(signup)initRoleSelect();
 $('#authForm').onsubmit=async e=>{
  e.preventDefault();const btn=$('#authSubmit'),err=$('#authFormError');const f=Object.fromEntries(new FormData(e.target));
  btn.disabled=true;err.textContent='';
  try{
   if(signup){
    // Collect all checked role checkboxes into a roles array.
    const checkedRoles=[...document.querySelectorAll('#roleCheckboxes input[name="roles"]:checked')].map(cb=>cb.value);
    if(!checkedRoles.length){err.textContent='Select at least one role.';btn.disabled=false;return}
    const payload={name:f.name,email:f.email,password:f.password,roles:checkedRoles};
    await api('/api/v2/auth/signup',{method:'POST',body:JSON.stringify(payload)});
    return authScreen('login','Account created. Sign in to continue.')}
   const out=await api('/api/v2/auth/login',{method:'POST',body:JSON.stringify({email:f.email,password:f.password})});
   sessionStorage.setItem('access_token',out.access_token);session={user:out.user};
   await afterLogin()
  }catch(x){err.textContent=friendly(x.message);btn.disabled=false}};
}
function friendly(code){
 const m={invalid_credentials:'Email or password is incorrect.',email_already_registered:'That email is already registered.',
  unsupported_role:'Choose a role from the list.',valid_email_required:'Enter a valid email address.',
  name_required:'Enter your full name.',too_many_attempts_retry_shortly:'Too many attempts. Wait a minute and try again.',
  password_must_not_match_identity:'Password must not repeat your name or email.',account_not_active:'This account is not active.'};
 if(m[code])return m[code];
 if(code.startsWith('password_too_short'))return 'Password must be at least 12 characters.';
 if(code.startsWith('account_locked_until'))return 'Account locked after repeated failures. Try again shortly.';
 if(code.startsWith('role_requires_administrator_grant'))return 'That role must be granted by an administrator. Pick an operational role.';
 if(code==='gateway_model_alias_required_for_override')return 'Agent Override requires a model alias. Click Configure Override to set one.';
 return code.replaceAll('_',' ')}
async function afterLogin(){
 // Section 2 of the brief: once signed in, resolve the stored GitHub connection.
 // Always re-verify the token on every login — a PAT that was CONNECTED in a
 // previous session may have been revoked since.
 try{const me=await api('/api/v2/auth/me');
  // Guard: the decoded token's sub must match the account the server returned.
  // A mismatch means the token in sessionStorage belongs to a different account
  // than the one that was just authenticated — force re-login to prevent silent
  // identity confusion.
  const c=claimsOf(token());
  if(c&&c.sub&&me.user&&c.sub!==me.user.email){return signOut('Session identity mismatch. Please sign in again.',true)}
  session={user:me.user};githubState=me.integrations.github}
 catch(e){if(e.reauth){return signOut('Your session is no longer valid. Sign in again.',true)}}
 nav();renderIdentity();
 if(!githubState||!githubState.configured)return openGithub('Add your GitHub URL and token to continue.');
 // Always re-verify the stored GitHub token regardless of its last-known status.
 // A token that was CONNECTED in a previous session may have been revoked.
 try{githubState=await api('/api/v2/integrations/github/test',{method:'POST'});renderIdentity()}
 catch(e){if(e.reauth)return signOut('Your session is no longer valid. Sign in again.',true);return openGithub(githubErr(e))}
 if(githubState.status!=='CONNECTED')return openGithub(githubState.last_error||'GitHub connection could not be verified. Enter a valid token to continue.');
 render();notifStartPolling()}
function githubErr(e){
 // The service encodes reason|status|detail so the operator sees what GitHub
 // actually said rather than a generic failure.
 const p=String(e.message||'').split('|');
 if(p.length<3)return friendly(p[0]||'github_error');
 const reason={github_credentials_rejected:'GitHub rejected the token.',github_access_forbidden:'Token lacks the required access.',
  github_api_not_found_check_url:'No GitHub API at that URL. Check the address.',github_unreachable:'Could not reach GitHub.'}[p[0]]||p[0].replaceAll('_',' ');
 return `${reason} (${p[1]}) ${p[2]}`}
const GH_STATUS_LABEL={CONNECTED:'Connected',FAILED:'Connection failed',UNVERIFIED:'Unverified',NOT_CONFIGURED:'Not configured'};
function cachedGithubToken(){
 // Scoped to the signed-in account: sessionStorage is shared by every tab of
 // this origin, so without this guard, account A's token could be shown to
 // account B if the tab is reused to sign into a different account.
 const owner=session&&session.user&&session.user.email;
 if(!owner||sessionStorage.getItem('github_token_owner')!==owner)return '';
 return sessionStorage.getItem('github_token')||''}
function rememberGithubToken(value){
 const owner=session&&session.user&&session.user.email;if(!owner)return;
 sessionStorage.setItem('github_token',value);sessionStorage.setItem('github_token_owner',owner)}
function forgetGithubToken(){sessionStorage.removeItem('github_token');sessionStorage.removeItem('github_token_owner')}
function openGithub(message){
 const dlg=$('#githubDialog'),f=$('#githubForm'),configured=!!(githubState&&githubState.configured);
 $('#githubError').textContent=message||'';
 f.url.value=(githubState&&githubState.url)||'';
 // What you type here is remembered only in this browser tab, only for the
 // account that typed it -- the server itself never hands back a previously
 // stored token, so a fresh browser/session still has to enter it again.
 const cached=cachedGithubToken();
 f.token.value=cached;f.token.type='password';$('#githubTokenToggle').innerHTML='<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
 $('#githubCurrent').innerHTML=!configured?'No GitHub connection stored yet.'
  :`Currently configured: <b>${esc(githubState.url)}</b> &middot; ${esc(GH_STATUS_LABEL[githubState.status]||githubState.status)}`
   +(githubState.account_login?' as <b>'+esc(githubState.account_login)+'</b>':'')
   +(cached?'. The token below is what was entered in this session.'
           :githubState.token_fingerprint?`. Token on file ends with <code>${esc(githubState.token_fingerprint)}</code> &mdash; enter it again to change it.`:'.');
 $('#githubDisconnect').hidden=!configured;
 if(!dlg.open)dlg.showModal();}
async function signOut(message,bad){
 // Signing out ends this browser session only. It must not delete the stored
 // GitHub connection -- that used to happen here, which meant every sign-out
 // silently erased the credential and forced reconnecting on every sign-in,
 // defeating the entire point of storing it per account. Disconnecting is now
 // a deliberate action (the Disconnect button in the GitHub dialog).
 sessionStorage.removeItem('access_token');forgetGithubToken();session=null;githubState=null;notifStopPolling();renderIdentity();
 const d=$('#githubDialog');if(d.open)d.close();authScreen('login',message||'Signed out.',!!bad)}
const _I=n=>`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">${n}</svg>`;
const NAV_ICON={
 dashboard:_I('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),
 metrics:_I('<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>'),
 pipelines:_I('<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 01-9 9"/>'),
 jobs:_I('<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/>'),
 specifications:_I('<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'),
 plans:_I('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>'),
 generated:_I('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>'),
 'delivery-operations':_I('<rect x="1" y="3" width="15" height="13" rx="1"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>'),
 versions:_I('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>'),
 intakes:_I('<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>'),
 dormancy:_I('<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>'),
 frameworks:_I('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>'),
 analyses:_I('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
 clarifications:_I('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>'),
 'quality-runs':_I('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>'),
 comparisons:_I('<path d="M18 20V10M12 20V4M6 20v-6"/>'),
 'differential-packages':_I('<path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/>'),
 'differential-evidence':_I('<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>'),
 variances:_I('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'),
 rca:_I('<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>'),
 remediations:_I('<path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/>'),
 'approval-governance':_I('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'),
 'approval-inbox':_I('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z"/>'),
 approvals:_I('<polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>'),
 evidence:_I('<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>'),
 events:_I('<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>'),
 'audit-ledger':_I('<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>'),
 'security-findings':_I('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'),
 'agent-registry':_I('<rect x="2" y="8" width="20" height="12" rx="2"/><path d="M8 8V5a4 4 0 018 0v3"/><circle cx="12" cy="14" r="2"/>'),
 'agent-health':_I('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
 'agent-templates':_I('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>'),
 'knowledge-index':_I('<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>'),
 'service-registry':_I('<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>'),
 'prompt-registry':_I('<path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>'),
 'model-registry':_I('<circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/>'),
 'model-requests':_I('<path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3M6.343 6.343l-.707-.707M6.343 17.657l-.707.707M15.657 6.343l.707-.707M18 12a6 6 0 11-12 0 6 6 0 0112 0z"/>'),
 notifications:_I('<path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/>'),
 'observability-events':_I('<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>'),
 'retention-policies':_I('<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>'),
 'skill-registry':_I('<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>'),
};
const _defIcon=_I('<circle cx="12" cy="12" r="4"/>');
const NAV_GROUPS=[
 {label:'Overview',items:[{page:'dashboard',label:'Dashboard'},{page:'metrics',label:'Metrics'}]},
 {label:'Migration Work',items:[{page:'pipelines',label:'Pipelines'},{page:'jobs',label:'Jobs'},{page:'specifications',label:'Specifications'},{page:'plans',label:'Plans'},{page:'generated',label:'Generated Code'},{page:'delivery-operations',label:'Delivery'},{page:'versions',label:'Versions'},{page:'quality-runs',label:'Quality Runs'},{page:'agent-templates',label:'Agent Templates'}]},
 {label:'Governance',items:[{page:'approval-governance',label:'Gate Config'},{page:'approval-inbox',label:'Approval Inbox'},{page:'approvals',label:'Approvals'},{page:'evidence',label:'Evidence'},{page:'events',label:'Events'},{page:'audit-ledger',label:'Audit Ledger'},{page:'security-findings',label:'Security'}]},
 {label:'Intelligence',items:[{page:'agent-registry',label:'Agent Registry'},{page:'agent-health',label:'Agent Health'},{page:'knowledge-index',label:'Knowledge'}]},
 {label:'Platform / AI',items:[{page:'service-registry',label:'Services'},{page:'prompt-registry',label:'Prompts'},{page:'model-registry',label:'Models'},{page:'model-requests',label:'Model Requests'},{page:'skill-registry',label:'Skills'},{page:'notifications',label:'Notifications'},{page:'observability-events',label:'Observability'},{page:'retention-policies',label:'Retention'}]},
];
function nav(){
 document.body.classList.remove('auth-mode');
 $('#nav').innerHTML=NAV_GROUPS.map(g=>`<div class="nav-group"><div class="nav-group-label">${esc(g.label)}</div>${g.items.map(it=>`<button class="nav-item" data-p="${esc(it.page)}">${NAV_ICON[it.page]||_defIcon}${esc(it.label)}</button>`).join('')}</div>`).join('');
 document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>location.hash=b.dataset.p);
 navSetActive((location.hash||'#dashboard').slice(1))}
function navSetActive(page){
 const base=page.startsWith('pipelines/')?'pipelines':page;
 document.querySelectorAll('.nav-item').forEach(b=>{b.classList.toggle('active',b.dataset.p===base)})}
function table(items,actions=false){if(!items?.length)return '<div class="empty">No live records</div>';const keys=Object.keys(items[0]).filter(k=>k!=='config').slice(0,9);const tdCls=k=>/_at$|^(created|updated|started|finished|timestamp)$/.test(k)?'td-date':/^(id|name|service|workspace_dir|display_name|title|gate)$/.test(k)||keys.indexOf(k)===0?'td-name':'';const fmtCell=(k,v)=>{if(/_at$|^(created|updated|started|finished|timestamp)$/.test(k)&&typeof v==='string')return fmtDt(v);return typeof v==='object'?JSON.stringify(v):String(v??'')};return `<div class="table"><table><thead><tr>${keys.map(k=>`<th>${esc(k.replaceAll('_',' '))}</th>`).join('')}${actions?'<th>Actions</th>':''}</tr></thead><tbody>${items.map(x=>`<tr>${keys.map(k=>`<td class="${tdCls(k)}" title="${esc(typeof x[k]==='object'?JSON.stringify(x[k]):String(x[k]??''))}">${esc(fmtCell(k,x[k])).slice(0,80)}</td>`).join('')}${actions?`<td><button data-test="${esc(x.id)}">Validate</button></td>`:''}</tr>`).join('')}</tbody></table></div>`}
function gateCard(g){return `<article class="gate-card"><div class="sectionhead"><h3>${esc(g.gate)}</h3><span class="live-dot">LIVE</span></div><form class="gate-form"><input type="hidden" name="gate" value="${esc(g.gate)}"><div class="formgrid"><label>Enabled<select name="enabled"><option value="true" ${g.enabled?'selected':''}>Enabled</option><option value="false" ${!g.enabled?'selected':''}>Disabled</option></select></label><label>Approver role<input name="required_role" value="${esc(g.required_role)}"></label><label class="wide">Allowed decisions<input name="allowed_decisions" value="${esc(g.allowed_decisions.join(','))}"></label><label>Minimum evidence<input name="min_evidence" type="number" min="0" value="${g.min_evidence}"></label><label>Quorum<input name="quorum" type="number" min="1" value="${g.quorum}"></label><label>Timeout minutes<input name="timeout_minutes" type="number" min="0" value="${g.timeout_minutes}"></label><label>Escalation profile<input name="escalation_profile" value="${esc(g.escalation_profile)}"></label><label>Bypass role<input name="bypass_role" value="${esc(g.bypass_role)}"></label><label>Downstream effect<input name="downstream_effect" value="${esc(g.downstream_effect)}"></label></div><button>Save to live database</button><p class="gate-meta">Last persisted update: ${esc(g.updated_at?fmtDt(g.updated_at):'not yet configured')}</p></form></article>`}
async function approvalGovernance(){const [g,i,a]=await Promise.all([api('/api/v7/gates'),api('/api/v7/approval-inbox'),api('/api/v2/approvals')]);$('#view').innerHTML=`<div class="hero"><span class="live-dot">LIVE DATABASE</span><h2>Approval Governance</h2><p>Persisted gate policies and controls. Database snapshot: ${esc(g.generated_at)}</p></div><div class="live-summary"><b>${g.items.length}</b> gates <b>${i.items.length}</b> pending <b>${a.items.length}</b> decisions</div><div class="gate-grid">${g.items.map(gateCard).join('')}</div>`;document.querySelectorAll('.gate-form').forEach(f=>f.onsubmit=async e=>{e.preventDefault();try{const x=Object.fromEntries(new FormData(f));x.enabled=x.enabled==='true';x.allowed_decisions=x.allowed_decisions.split(',').map(v=>v.trim()).filter(Boolean);x.min_evidence=+x.min_evidence;x.quorum=+x.quorum;x.timeout_minutes=+x.timeout_minutes;await api('/api/v7/gates/configure',{method:'POST',body:JSON.stringify(x)});notice(`${x.gate} saved to live database`);approvalGovernance()}catch(err){notice(err.message,true)}})}
async function approvalInbox(){
 const d=await api('/api/v7/approval-inbox');
 const items=d.items||[];
 const rows=items.map(a=>`<div class="approval-item" style="padding:20px">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px">
   <div style="flex:1">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
     ${a.gate?`<span class="gate gate-active">${esc(a.gate)}</span>`:''}
     <span class="approval-service">${esc(a.service||a.pipeline_id||'')}</span>
     ${a.id?`<span class="badge badge-neutral">${esc(String(a.id).slice(0,12))}</span>`:''}
    </div>
    ${a.description?`<div style="font-size:12px;color:var(--text-2);margin-bottom:4px">${esc(a.description)}</div>`:''}
    <div style="display:flex;align-items:center;gap:12px;font-size:11px;color:var(--text-3)">
     ${a.created_at?`<span>${esc(fmtDt(a.created_at))}</span>`:''}
     ${a.role?`<span>Required role: <b style="color:var(--text-2)">${esc(a.role)}</b></span>`:''}
    </div>
   </div>
   <div style="display:flex;gap:8px;flex-shrink:0">
    <button class="btn-approve" data-approval-id="${esc(a.id||'')}">Approve</button>
    <button class="btn-reject">Request Changes</button>
   </div>
  </div>
 </div>`).join('');
 $('#view').innerHTML=`<div class="page-header"><div class="page-title">Approval Inbox</div><div class="page-sub">Gate approvals requiring your action based on your assigned roles</div></div>
  <div class="card"><div class="card-head"><div><div class="card-title">Pending (${items.length})</div></div><div style="display:flex;gap:6px">${['G0','G1','G2','G3','G4','G5'].map(g=>`<span class="gate gate-blocked">${g}</span>`).join('')}</div></div>
  <div class="card-body">${rows||'<div class="empty">No pending approvals</div>'}</div></div>`;
 document.querySelectorAll('.btn-approve[data-approval-id]').forEach(btn=>{btn.onclick=async()=>{try{await api('/api/v7/approval-packages/'+btn.dataset.approvalId+'/approve',{method:'POST',body:JSON.stringify({})});btn.closest('.approval-item').style.opacity='0.4';notice('Approval submitted — pipeline will resume automatically')}catch(e){notice(e.message,true)}}})}
// ---- Clarifications page --------------------------------------------------
async function clarificationsPage(){
 const [cd,jd]=await Promise.all([api('/api/v2/clarifications'),api('/api/v2/jobs').catch(()=>({items:[]}))]);
 const all=cd.items||[];
 const jobMap={};(jd.items||[]).forEach(j=>{jobMap[j.id]=j.service||j.workspace_dir||j.id});
 const open=all.filter(c=>c.status==='OPEN');
 const answered=all.filter(c=>c.status!=='OPEN');
 const stageBadge=stage=>{const colors={ACE_ANALYSIS:'purple',INTAKE:'info',DORMANCY:'indigo',FRAMEWORK_BASELINE:'indigo',MAPPING:'orange',PLAN:'orange',IMPLEMENTATION:'info',BUILD:'info',TESTING:'purple',VARIANCE:'warning',REMEDIATION:'warning'};return`<span class="badge badge-${colors[stage]||'neutral'}"><span class="badge-dot"></span>${esc((stage||'').replace(/_/g,' '))}</span>`};
 const clarifCard=(c,isOpen)=>{
  const svc=esc(jobMap[c.job_id]||c.job_id||'—');
  const questionType=esc((c.question||'').replace(/_/g,' '));
  return`<div class="clarif-item ${isOpen?'clarif-open':'clarif-done'}" data-clarif-id="${esc(c.id)}">
   <div class="clarif-header">
    <span class="clarif-service">${svc}</span>
    ${stageBadge(c.stage||'')}
    ${isOpen?'<span class="badge badge-warning"><span class="badge-dot"></span>OPEN</span>':'<span class="badge badge-success"><span class="badge-dot"></span>ANSWERED</span>'}
    <span class="clarif-time">${fmtDt(c.created_at)}</span>
   </div>
   <div class="clarif-type">${questionType}</div>
   ${c.impact?`<div class="clarif-impact">Impact: ${esc(c.impact)}</div>`:''}
   ${isOpen?`<div class="clarif-form">
    <textarea class="clarif-textarea" placeholder="Enter your answer…" rows="3"></textarea>
    <div style="display:flex;align-items:center;gap:10px;margin-top:8px">
     <button class="btn-approve clarif-submit-btn">Submit Answer</button>
     <span class="clarif-msg" style="font-size:12px;color:var(--success-text);display:none"></span>
    </div>
   </div>`:
   `<div class="clarif-answered-block">
    <span class="clarif-answered-label">Answer</span>
    <span class="clarif-answered-text">${esc(c.answer||'')}</span>
    ${c.answered_at?`<span class="clarif-answered-meta">${esc(fmtDt(c.answered_at))}${c.answered_by?' · '+esc(c.answered_by):''}</span>`:''}
   </div>`}
  </div>`};
 $('#view').innerHTML=`<div class="page-header page-header-row"><div><div class="page-title">Clarifications</div><div class="page-sub">Pipeline blocking questions requiring human input</div></div></div>
  <div style="display:flex;flex-direction:column;gap:16px">
   ${open.length>0
    ?`<div class="card" style="border-color:rgba(245,158,11,.35)"><div class="card-head" style="background:rgba(245,158,11,.06);border-bottom-color:rgba(245,158,11,.2)"><div style="display:flex;align-items:center;gap:8px"><div class="approval-alert-dot"></div><div><div class="card-title" style="color:var(--warning-text)">Open (${open.length})</div><div class="card-sub">${open.length} question${open.length!==1?'s':''} blocking pipeline progression</div></div></div></div><div class="card-body">${open.map(c=>clarifCard(c,true)).join('')}</div></div>`
    :`<div class="card"><div class="card-head"><div style="display:flex;align-items:center;gap:8px"><div style="width:28px;height:28px;border-radius:50%;background:var(--success-bg);display:flex;align-items:center;justify-content:center;color:var(--success-text);flex-shrink:0"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></div><div><div class="card-title" style="color:var(--success-text)">No open clarifications</div><div class="card-sub">All questions answered — pipelines running freely</div></div></div></div></div>`}
   ${answered.length?`<div class="card"><div class="card-head"><div class="card-title">Answered (${answered.length})</div></div><div class="card-body">${answered.slice(0,30).map(c=>clarifCard(c,false)).join('')}</div></div>`:''}
  </div>`;
 document.querySelectorAll('.clarif-submit-btn').forEach(btn=>{
  const item=btn.closest('.clarif-item');
  const cid=item?.dataset.clarifId;
  if(!cid)return;
  btn.onclick=async()=>{
   const ta=item.querySelector('.clarif-textarea');
   const answer=(ta?.value||'').trim();
   if(!answer){notice('Please enter an answer before submitting',true);return}
   btn.disabled=true;btn.textContent='Submitting…';
   try{
    await api('/api/v2/clarifications/'+cid+'/answer',{method:'POST',body:JSON.stringify({answer})});
    item.classList.remove('clarif-open');item.classList.add('clarif-done');
    const form=item.querySelector('.clarif-form');
    if(form)form.outerHTML=`<div class="clarif-answered-block"><span class="clarif-answered-label">Answer</span><span class="clarif-answered-text">${esc(answer)}</span><span class="clarif-answered-meta">just now</span></div>`;
    const wb=item.querySelector('.badge-warning');
    if(wb)wb.outerHTML='<span class="badge badge-success"><span class="badge-dot"></span>ANSWERED</span>';
    item.style.borderLeftColor='var(--success)';
    notice('Answer submitted. If this was the last open clarification, the pipeline will resume automatically.');
   }catch(e){notice(e.message,true);btn.disabled=false;btn.textContent='Submit Answer'}}});
 refreshTimer=setInterval(clarificationsPage,15000)}
// ---- New migration wizard -------------------------------------------------
let wizard=null;
const WIZARD_STEPS=['Migration & Workspace','IBM ACE Repository','Migration Framework Repository','Spring Boot Output','Review','Select Agents'];
function wizardDefault(){
 return {step:0,
  job:{service:'',owner:'',workspace_dir:'',environment:'DEV',project_scope:'GLOBAL'},
  ace:{repo_url:'',branch:'main'},
  frameworkEnabled:false,framework:{repo_url:'',branch:'main'},
  targetPush:false,target:{repo_url:'',branch:'main'},service_name:'',package_name:'',
  agents:null,selected:new Set(),impact:null,error:'',templateMode:'manual',templateId:null,templateName:'',templateAgentCount:0}}
function migrationWizard(){
 if(!wizard)wizard=wizardDefault();
 $('#metrics').innerHTML='';$('#title').textContent='New migration';
 renderWizardStep()}
function renderWizardStep(){
 const n=wizard.step;
 $('#view').innerHTML=`<div class="wizard"><div class="wizardsteps">${WIZARD_STEPS.map((s,i)=>`<span class="${i===n?'active':i<n?'done':''}">${esc(s)}</span>`).join('')}</div><div class="card wizardcard" id="wizardBody"></div></div>`;
 const body=$('#wizardBody');
 const renderers=[wizardStepWorkspace,wizardStepAceRepo,wizardStepFrameworkRepo,wizardStepTarget,wizardStepReview,wizardStepAgents];
 renderers[n](body);
 const h2=body.querySelector('h2');
 const hint=h2&&h2.nextElementSibling;
 if(h2&&hint&&hint.classList.contains('hint')){
  const hdr=document.createElement('div');hdr.className='wiz-header';
  body.insertBefore(hdr,h2);hdr.appendChild(h2);hdr.appendChild(hint)}}
function wizardNav(body,{back,nextLabel,onNext}){
 const nav=document.createElement('div');nav.className='wizardnav';
 nav.innerHTML=`<div>${back?'<button type="button" id="wizBack" class="btn-secondary">← Back</button>':''}</div><div class="right"><button type="button" id="wizCancel" class="btn-secondary">Cancel</button><button type="button" id="wizNext" class="btn-primary">${esc(nextLabel||'Next →')}</button></div>`;
 body.appendChild(nav);
 if(back)$('#wizBack').onclick=()=>{wizard.step--;renderWizardStep()};
 $('#wizCancel').onclick=()=>{wizard=null;location.hash='dashboard'};
 $('#wizNext').onclick=onNext}
function wizardError(msg){const p=$('#wizardBody').querySelector('.authhint.bad')||(() => {const e=document.createElement('p');e.className='authhint bad';$('#wizardBody').prepend(e);return e})();p.textContent=msg||''}
function wizFieldErr(inputId,errId,msg){const inp=$('#'+inputId),err=$('#'+errId);inp.classList.toggle('invalid',!!msg);err.textContent=msg||'';err.classList.toggle('show',!!msg)}
function wizardStepWorkspace(body){
 const j=wizard.job;
 body.innerHTML=`<h2>Migration &amp; Workspace</h2><p class="hint">Identifies the migration job and the workspace directory everything else in this pipeline is confined to.</p>
  <div class="wiz-section">
   <div class="wiz-section-title">Job Identity</div>
   <div class="wiz-field-group">
    <div class="formgrid">
     <label>Service name<input id="wService" value="${esc(j.service)}" placeholder="orders-service" required><span class="field-error" id="errService"></span></label>
     <label>Owner<input id="wOwner" value="${esc(j.owner)}" placeholder="jane.doe" required><span class="field-error" id="errOwner"></span></label>
    </div>
    <label>Workspace directory<input id="wWorkspace" value="${esc(j.workspace_dir)}" placeholder="orders-migration" required><span class="field-error" id="errWorkspace"></span></label>
    <p class="hint" style="margin:2px 0 0">A subfolder name under the server's workspace root. Do not include a leading <code>workspace/</code> prefix.</p>
   </div>
  </div>
  `;
 wizardNav(body,{back:false,onNext:()=>{
  j.service=$('#wService').value.trim();j.owner=$('#wOwner').value.trim();j.workspace_dir=$('#wWorkspace').value.trim().replace(/^\/|\/$/g,'');
  wizFieldErr('wService','errService',j.service?'':'Service name is required');
  wizFieldErr('wOwner','errOwner',j.owner?'':'Owner is required');
  wizFieldErr('wWorkspace','errWorkspace',j.workspace_dir?'':'Workspace directory is required');
  if(!j.service||!j.owner||!j.workspace_dir)return;
  wizard.step++;renderWizardStep()}})}
function wizardStepAceRepo(body){
 const a=wizard.ace;
 body.innerHTML=`<h2>IBM ACE Repository</h2><p class="hint">The source ACE integration repository this migration analyses. Cloned using your connected GitHub account.</p>
  <div class="wiz-section">
   <div class="wiz-section-title">Source Repository</div>
   <div class="wiz-field-group">
    <label>Repository URL<input id="wAceUrl" type="url" value="${esc(a.repo_url)}" placeholder="https://github.com/org/ace-integration" required><span class="field-error" id="errAceUrl"></span></label>
    <label>Branch<input id="wAceBranch" value="${esc(a.branch)}" placeholder="main" required></label>
   </div>
  </div>`;
 wizardNav(body,{back:true,onNext:()=>{
  a.repo_url=$('#wAceUrl').value.trim();a.branch=$('#wAceBranch').value.trim()||'main';
  const urlOk=/^https:\/\//.test(a.repo_url);
  wizFieldErr('wAceUrl','errAceUrl',urlOk?'':'Must be a valid https:// URL');
  if(!urlOk)return;
  wizard.step++;renderWizardStep()}})}
function wizardStepFrameworkRepo(body){
 const f=wizard.framework;
 body.innerHTML=`<h2>Migration Framework Repository</h2><p class="hint">Optional. A reference repository the framework baseline agent extracts target conventions from. Leave unchecked to skip framework baselining for this run.</p>
  <div class="wiz-section">
   <div class="wiz-section-title">Framework Baselining</div>
   <div class="wiz-field-group">
    <label class="toggle"><input type="checkbox" id="wFwEnabled"${wizard.frameworkEnabled?' checked':''}><span class="track"></span>Include a framework repository</label>
    <div id="wFwFields" ${wizard.frameworkEnabled?'':'hidden'} style="display:flex;flex-direction:column;gap:10px">
     <label>Repository URL<input id="wFwUrl" type="url" value="${esc(f.repo_url)}" placeholder="https://github.com/org/spring-framework-template"><span class="field-error" id="errFwUrl"></span></label>
     <label>Branch<input id="wFwBranch" value="${esc(f.branch)}" placeholder="main"></label>
    </div>
   </div>
  </div>`;
 $('#wFwEnabled').onchange=e=>{$('#wFwFields').hidden=!e.target.checked};
 wizardNav(body,{back:true,onNext:()=>{
  wizard.frameworkEnabled=$('#wFwEnabled').checked;
  f.repo_url=$('#wFwUrl').value.trim();f.branch=$('#wFwBranch').value.trim()||'main';
  const urlOk=!wizard.frameworkEnabled||/^https:\/\//.test(f.repo_url);
  wizFieldErr('wFwUrl','errFwUrl',urlOk?'':'Must be a valid https:// URL');
  if(!urlOk)return;
  wizard.step++;renderWizardStep()}})}
function wizardStepTarget(body){
 const t=wizard.target;
 body.innerHTML=`<h2>Spring Boot Output</h2><p class="hint">Where the generated Spring Boot code should end up.</p>
  <div class="wiz-section">
   <div class="wiz-section-title">Output Destination</div>
   <div class="wiz-field-group">
    <label class="toggle"><input type="checkbox" id="wPush"${wizard.targetPush?' checked':''}><span class="track"></span>Push generated code to a repository (otherwise written to the workspace output folder)</label>
    <div id="wPushFields" ${wizard.targetPush?'':'hidden'} style="display:flex;flex-direction:column;gap:10px">
     <label>Repository URL<input id="wTargetUrl" type="url" value="${esc(t.repo_url)}" placeholder="https://github.com/org/orders-service"><span class="field-error" id="errTargetUrl"></span></label>
     <label>Branch<input id="wTargetBranch" value="${esc(t.branch)}" placeholder="migration/spring-boot"></label>
    </div>
   </div>
  </div>
  <div class="wiz-section">
   <div class="wiz-section-title">Java Project Identity</div>
   <div class="formgrid">
    <label>Service name<input id="wServiceName" value="${esc(wizard.service_name)}" placeholder="orders-service" required><span class="field-error" id="errServiceName"></span></label>
    <label>Package name<input id="wPackageName" value="${esc(wizard.package_name)}" placeholder="com.example.orders" required><span class="field-error" id="errPackageName"></span></label>
   </div>
  </div>`;
 $('#wPush').onchange=e=>{$('#wPushFields').hidden=!e.target.checked};
 wizardNav(body,{back:true,onNext:()=>{
  wizard.targetPush=$('#wPush').checked;
  t.repo_url=$('#wTargetUrl').value.trim();t.branch=$('#wTargetBranch').value.trim()||'main';
  wizard.service_name=$('#wServiceName').value.trim();wizard.package_name=$('#wPackageName').value.trim();
  const urlOk=!wizard.targetPush||/^https:\/\//.test(t.repo_url);
  wizFieldErr('wTargetUrl','errTargetUrl',urlOk?'':'Must be a valid https:// URL');
  wizFieldErr('wServiceName','errServiceName',wizard.service_name?'':'Service name is required');
  wizFieldErr('wPackageName','errPackageName',wizard.package_name?'':'Package name is required');
  if(!urlOk||!wizard.service_name||!wizard.package_name)return;
  wizard.step++;renderWizardStep()}})}
function wizardStepReview(body){
 const j=wizard.job,a=wizard.ace,f=wizard.framework,t=wizard.target;
 const hasTmpl=wizard.templateMode==='template'&&wizard.templateId;
 const hasManual=wizard.agents&&wizard.templateMode==='manual'&&wizard.selected.size>0;
 const agentCfgSection=hasTmpl
  ?`<div class="reviewsection"><dl class="reviewgrid"><dt>Agent config</dt><dd><span class="badge badge-info" style="font-size:11px;margin-right:6px">Template</span>${esc(wizard.templateName||wizard.templateId)} &middot; ${wizard.templateAgentCount||wizard.selected.size} agent${(wizard.templateAgentCount||wizard.selected.size)!==1?'s':''}</dd></dl></div>`
  :hasManual?`<div class="reviewsection"><dl class="reviewgrid"><dt>Agent config</dt><dd><span class="badge badge-neutral" style="font-size:11px;margin-right:6px">Manual</span>${wizard.selected.size} agent${wizard.selected.size!==1?'s':''} selected</dd></dl></div>`:''
 body.innerHTML=`<h2>Review</h2><p class="hint">Check the details below then configure the agents for this migration.</p>
  <div class="reviewsection"><dl class="reviewgrid"><dt>Service</dt><dd>${esc(j.service)}</dd><dt>Owner</dt><dd>${esc(j.owner)}</dd><dt>Workspace</dt><dd>${esc(j.workspace_dir)}</dd></dl></div>
  <div class="reviewsection"><dl class="reviewgrid"><dt>ACE repo</dt><dd>${esc(a.repo_url)} @ ${esc(a.branch)}</dd></dl></div>
  <div class="reviewsection"><dl class="reviewgrid"><dt>Framework repo</dt><dd>${wizard.frameworkEnabled?esc(f.repo_url)+' @ '+esc(f.branch):'Not included'}</dd></dl></div>
  <div class="reviewsection"><dl class="reviewgrid"><dt>Spring output</dt><dd>${wizard.targetPush?'Push to '+esc(t.repo_url)+' @ '+esc(t.branch):'Workspace output folder'}</dd><dt>Service / package</dt><dd>${esc(wizard.service_name)} / ${esc(wizard.package_name)}</dd></dl></div>
  ${agentCfgSection}`;
 wizardNav(body,{back:true,nextLabel:'Configure agents →',onNext:showAgentConfigModal})}
async function wizardLoadAgents(){
 if(wizard.agents)return wizard.agents;
 const r=await api('/api/v2/agent-registry');
 wizard.agents=r.items.filter(x=>x.agent_id!=='migration-orchestrator'&&x.enabled&&x.status==='APPROVED')
  .map(x=>({...x,dependencies:JSON.parse(x.dependencies||'[]')}));
 wizard.selected=new Set(wizard.agents.map(x=>x.agent_id));
 return wizard.agents}
async function wizardRefreshImpact(){
 try{wizard.impact=await api('/api/v2/pipelines/preview',{method:'POST',body:JSON.stringify({selected_agent_ids:[...wizard.selected]})})}
 catch(e){wizard.impact=null}}
// ── Agent card grid: static grouping + icons ──────────────────────────────
const AGENT_STAGE_MAP={
 'repository-intake':'Setup','dormancy-intelligence':'Setup','framework-baseline':'Setup',
 'ace-analysis':'Analysis','migration-specification':'Analysis',
 'planning':'Generation','code-generation':'Generation','test-engineering':'Generation',
 'variance-analysis':'Quality Assurance','rca':'Quality Assurance','remediation':'Quality Assurance',
 'governance':'Governance & Platform','knowledge-evidence':'Governance & Platform',
 'migration-orchestrator':'Governance & Platform'};
const AGENT_STAGE_ORDER={'Setup':0,'Analysis':1,'Generation':2,'Quality Assurance':3,'Governance & Platform':4};
const AGENT_CODE_MAP={'migration-orchestrator':'A01','repository-intake':'A02','dormancy-intelligence':'A03','framework-baseline':'A04','ace-analysis':'A05','migration-specification':'A06','planning':'A07','code-generation':'A08','test-engineering':'A09','variance-analysis':'A10','rca':'A11','remediation':'A12','governance':'A13','knowledge-evidence':'A14'};
const AGENT_ICONS_MAP={
 'repository-intake':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
 'dormancy-intelligence':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>`,
 'framework-baseline':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>`,
 'ace-analysis':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>`,
 'migration-specification':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/></svg>`,
 'planning':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>`,
 'code-generation':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
 'test-engineering':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M14.5 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>`,
 'variance-analysis':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
 'rca':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
 'remediation':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></svg>`,
 'governance':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
 'knowledge-evidence':`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/></svg>`};
const AC_CHECK=`<svg class="check-icon" width="9" height="9" fill="none" stroke="#fff" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`;
const AC_ICON_FALLBACK=`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="2" y="8" width="20" height="12" rx="2"/><path d="M8 8V5a4 4 0 018 0v3"/><circle cx="12" cy="14" r="2"/></svg>`;
const AGENT_CAT_CLASS={'Setup':'setup','Analysis':'analysis','Generation':'generation','Quality Assurance':'quality','Governance & Platform':'platform'};
function agentCard(a,skippedReason){
 const isSel=wizard.selected.has(a.agent_id);
 const isPruned=!!skippedReason;
 const stage=AGENT_STAGE_MAP[a.agent_id]||'';
 const catCls=AGENT_CAT_CLASS[stage]||'';
 const catChip=stage?`<span class="card-cat${catCls?' '+catCls:''}">${esc(stage)}</span>`:'';
 return `<div class="agent-card${isSel?' selected':''}${isPruned?' pruned':''}" data-agent="${esc(a.agent_id)}" role="checkbox" aria-checked="${isSel}" tabindex="0"><div class="card-header"><div class="card-icon">${AGENT_ICONS_MAP[a.agent_id]||AC_ICON_FALLBACK}</div><span class="card-name">${esc(a.display_name)}</span><div class="card-indicator">${AC_CHECK}</div></div><div class="card-desc">${esc(a.description||'')}${isPruned?`<br><em class="card-pruned-reason">Skipped: ${esc(skippedReason)}</em>`:''}</div>${catChip}</div>`}
async function wizardStepAgents(body){
 body.innerHTML='<h2>Select Agents</h2><p class="hint">Loading the agent catalogue...</p>';
 const agents=await wizardLoadAgents();
 await wizardRefreshImpact();
 renderAgentCards(body,agents)}
function renderAgentCards(body,agents){
 const skippedByAgent=Object.fromEntries((wizard.impact?.skipped||[]).map(x=>[x.agent_id,x.reason]));
 const stageMap={};
 agents.forEach(a=>{
  const stage=AGENT_STAGE_MAP[a.agent_id]||'Other';
  const order=AGENT_STAGE_ORDER[stage]??99;
  if(!stageMap[stage])stageMap[stage]={order,agents:[]};
  stageMap[stage].agents.push(a)
 });
 const groups=Object.entries(stageMap).sort((a,b)=>a[1].order-b[1].order);
 const allSel=wizard.selected.size===agents.length;
 body.innerHTML=`<h2>Select Agents</h2>
  <p class="hint">Disabling an agent also skips whatever depends on it.</p>
  <div class="ac-toolbar">
   <span class="ac-sel-count"><b>${wizard.selected.size}</b> of ${agents.length} selected</span>
   <div style="display:flex;gap:6px">
    <button type="button" id="acSelAll" class="btn-ghost-sm">${allSel?'Deselect all':'Select all'}</button>
    <button type="button" id="acSaveAsTmpl" class="btn-ghost-sm">Save as template</button>
   </div>
  </div>
  <div class="agent-groups" id="wAgentCards">
   ${groups.map(([stage,g])=>`<div class="agent-group"><div class="group-header"><span class="group-label">${esc(stage)}</span><span class="group-line"></span><span class="group-meta">${g.agents.length} agent${g.agents.length!==1?'s':''}</span></div><div class="group-grid">${g.agents.map(a=>agentCard(a,skippedByAgent[a.agent_id])).join('')}</div></div>`).join('')}
  </div>`;
 body.querySelector('#acSaveAsTmpl')?.addEventListener('click',()=>{
  showTemplateModal(null,agents,[...wizard.selected])});
 body.querySelectorAll('.agent-card').forEach(card=>{
  card.onclick=async()=>{
   const id=card.dataset.agent;
   if(wizard.selected.has(id))wizard.selected.delete(id);else wizard.selected.add(id);
   renderAgentCards(body,agents);
   await wizardRefreshImpact();
   renderAgentCards(body,agents)
  };
  card.onkeydown=e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();card.onclick()}}
 });
 const selAllBtn=body.querySelector('#acSelAll');
 if(selAllBtn)selAllBtn.onclick=async()=>{
  const nowAllSel=wizard.selected.size===agents.length;
  agents.forEach(a=>{if(nowAllSel)wizard.selected.delete(a.agent_id);else wizard.selected.add(a.agent_id)});
  renderAgentCards(body,agents);
  await wizardRefreshImpact();
  renderAgentCards(body,agents)
 };
 wizardNav(body,agentsNavOpts())}
function agentsNavOpts(){
 return {back:true,nextLabel:'Create pipeline',onNext:submitPipeline}}
async function submitPipeline(){
 const btn=$('#wizNext');if(btn)btn.disabled=true;
 const j=wizard.job,repos={ACE:{repo_url:wizard.ace.repo_url,branch:wizard.ace.branch,enabled:true}};
 if(wizard.frameworkEnabled)repos.FRAMEWORK={repo_url:wizard.framework.repo_url,branch:wizard.framework.branch,enabled:true};
 else{
  // Framework repository is disabled — remove framework-baseline from the
  // agent selection so it is skipped (and its dependents cascade-skipped)
  // rather than running against an empty directory and failing the pipeline.
  wizard.selected.delete('framework-baseline');
 }
 repos.TARGET={repo_url:wizard.target.repo_url,branch:wizard.target.branch,push_mode:wizard.targetPush?'PUSH_TO_REPO':'LOCAL_OUTPUT',enabled:true};
 try{
  const created=await api('/api/v2/pipelines',{method:'POST',body:JSON.stringify({
   job:{service:j.service,owner:j.owner,environment:j.environment,project_scope:j.project_scope,
        // The job's source_revision is set to the ACE branch now; once the
        // clone step actually runs, job_repositories.resolved_commit records
        // the real commit SHA it landed on.
        sourceRevision:wizard.ace.branch},
   workspace_dir:j.workspace_dir,repos,selected_agent_ids:[...wizard.selected],
   service_name:wizard.service_name,package_name:wizard.package_name})});
  wizard=null;location.hash='pipelines/'+created.id
 }catch(e){if(btn)btn.disabled=false;wizardError(e.message)}}

// ── Agent Configuration Modal ─────────────────────────────────────────────
async function showAgentConfigModal(){
 let mode=wizard.templateMode||'manual';
 let templates=null; // null = not yet loaded
 let selTmplId=wizard.templateId||null;
 const svgTmpl=`<svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>`;
 const svgManual=`<svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 010 14.14M4.93 19.07a10 10 0 010-14.14"/><path d="M15.54 8.46a5 5 0 010 7.07M8.46 15.54a5 5 0 010-7.07"/></svg>`;
 const svgChk=`<svg width="13" height="13" fill="none" stroke="#fff" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`;
 const cardHtml=()=>`
  <div class="acfg-card${mode==='template'?' acfg-card-sel':''}" data-mode="template">
   <div class="acfg-card-icon acfg-icon-tmpl">${svgTmpl}</div>
   <div class="acfg-card-content"><div class="acfg-card-title">Use Saved Template</div><div class="acfg-card-desc">Apply a previously saved agent template and skip manual agent selection.</div></div>
   <div class="acfg-card-check${mode==='template'?'':' acfg-check-hidden'}">${svgChk}</div>
  </div>
  <div class="acfg-card${mode==='manual'?' acfg-card-sel':''}" data-mode="manual">
   <div class="acfg-card-icon acfg-icon-manual">${svgManual}</div>
   <div class="acfg-card-content"><div class="acfg-card-title">Select Agents Manually</div><div class="acfg-card-desc">Choose agents individually using the existing agent selection experience.</div></div>
   <div class="acfg-card-check${mode==='manual'?'':' acfg-check-hidden'}">${svgChk}</div>
  </div>`;
 const tmplSlotHtml=()=>{
  if(mode!=='template')return'';
  if(templates===null)return`<div class="acfg-tmpl-loading"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" class="acfg-spin"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg> Loading templates…</div>`;
  if(!templates.length)return`<div class="acfg-tmpl-empty">No saved templates found. Create one from the Agent Templates page.</div>`;
  const t=selTmplId?templates.find(x=>x.id===selTmplId):null;
  return`<div class="acfg-tmpl-pick">
   <div class="acfg-tmpl-lbl">Select Template</div>
   <select id="acfgTmplSel" class="acfg-tmpl-sel">
    <option value="">— choose a template —</option>
    ${templates.map(tt=>{const cnt=(tt.agent_ids||[]).length;return`<option value="${esc(tt.id)}"${selTmplId===tt.id?' selected':''}>${esc(tt.name)} · ${cnt} agent${cnt!==1?'s':''}</option>`}).join('')}
   </select>
   ${t&&t.description?`<div class="acfg-tmpl-tdesc">${esc(t.description)}</div>`:''}
  </div>`};
 const dlg=document.createElement('dialog');
 dlg.id='_agentCfgDlg';
 dlg.innerHTML=`
  <div class="acfg-hdr">
   <div><div class="acfg-title">Agent Configuration</div><div class="acfg-sub">Choose how you would like to configure agents for this migration.</div></div>
   <button type="button" id="acfgX" class="icon-btn" style="flex-shrink:0"><svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </div>
  <div class="acfg-body">
   <div id="acfgCards">${cardHtml()}</div>
   <div id="acfgTmplSlot">${tmplSlotHtml()}</div>
  </div>
  <div class="acfg-ftr">
   <button type="button" id="acfgBack" class="btn-secondary">← Back to Review</button>
   <button type="button" id="acfgContinue" class="btn-primary"${mode==='template'&&!selTmplId?' disabled':''}>Continue →</button>
  </div>`;
 document.body.appendChild(dlg);
 dlg.showModal();
 const close=()=>{if(dlg.open)dlg.close();dlg.remove()};
 const updBtn=()=>{
  const btn=dlg.querySelector('#acfgContinue');if(!btn)return;
  const ready=mode==='manual'||(mode==='template'&&!!selTmplId);
  btn.disabled=!ready;
  btn.textContent=mode==='template'&&selTmplId?'Create Pipeline →':'Continue →'};
 const rerender=()=>{
  dlg.querySelector('#acfgCards').innerHTML=cardHtml();
  dlg.querySelector('#acfgTmplSlot').innerHTML=tmplSlotHtml();
  attachHandlers();updBtn()};
 const attachHandlers=()=>{
  dlg.querySelectorAll('.acfg-card').forEach(card=>{
   card.addEventListener('click',async()=>{
    const m=card.dataset.mode;if(m===mode)return;
    mode=m;selTmplId=mode==='template'?wizard.templateId||null:null;
    if(mode==='template'&&templates===null){
     rerender(); // show loading state
     const td=await api('/api/v2/agent-templates').catch(()=>({items:[]}));
     templates=td.items||[]}
    rerender()})});
  const sel=dlg.querySelector('#acfgTmplSel');
  if(sel)sel.addEventListener('change',()=>{
   selTmplId=sel.value||null;
   const t=templates&&selTmplId?templates.find(x=>x.id===selTmplId):null;
   let descEl=dlg.querySelector('.acfg-tmpl-tdesc');
   if(!descEl&&t&&t.description){
    const pick=dlg.querySelector('.acfg-tmpl-pick');
    if(pick){descEl=document.createElement('div');descEl.className='acfg-tmpl-tdesc';pick.appendChild(descEl)}}
   if(descEl)descEl.textContent=t&&t.description?t.description:'';
   updBtn()})};
 attachHandlers();
 dlg.querySelector('#acfgX').onclick=close;
 dlg.querySelector('#acfgBack').onclick=close;
 dlg.querySelector('#acfgContinue').onclick=async()=>{
  wizard.templateMode=mode;
  if(mode==='manual'){
   wizard.templateId=null;wizard.templateName='';wizard.templateAgentCount=0;
   close();wizard.step=5;renderWizardStep()
  }else{
   if(!selTmplId)return;
   const t=(templates||[]).find(x=>x.id===selTmplId);if(!t)return;
   const btn=dlg.querySelector('#acfgContinue');
   if(btn){btn.disabled=true;btn.textContent='Creating pipeline…'}
   const agents=await wizardLoadAgents().catch(()=>[]);
   wizard.templateId=t.id;wizard.templateName=t.name;
   wizard.templateAgentCount=(t.agent_ids||[]).length;
   wizard.selected=new Set((t.agent_ids||[]).filter(id=>agents.find(a=>a.agent_id===id)));
   close();renderWizardStep();submitPipeline()}};
 // If returning to this modal already in template mode, load templates straight away
 if(mode==='template'){
  const td=await api('/api/v2/agent-templates').catch(()=>({items:[]}));
  templates=td.items||[];rerender()}}

// ── Template Picker Modal ─────────────────────────────────────────────────
async function showTemplatePickerModal(templates){
 const agents=await wizardLoadAgents().catch(()=>[]);
 const agentMap=Object.fromEntries((agents||[]).map(a=>[a.agent_id,a]));
 let selTmpl=templates.find(t=>t.id===wizard.templateId)||templates[0]||null;
 const stageOrder=['Setup','Analysis','Generation','Quality Assurance','Governance & Platform'];
 const renderList=()=>!templates.length
  ?'<div class="tpick-empty">No saved templates found.<br>Create one from the Agent Templates page.</div>'
  :templates.map(t=>{
   const cnt=(t.agent_ids||[]).length;
   const isSel=selTmpl&&selTmpl.id===t.id;
   return`<div class="tpick-row${isSel?' tpick-row-sel':''}" data-tid="${esc(t.id)}">
    <div class="tpick-row-info"><div class="tpick-name">${esc(t.name)}</div>${t.description?`<div class="tpick-rdesc">${esc(t.description)}</div>`:''}</div>
    <span class="tpick-cnt">${cnt} agent${cnt!==1?'s':''}</span>
   </div>`}).join('');
 const renderDetail=()=>{
  if(!selTmpl)return'<div class="tpick-empty">Select a template to preview its agents.</div>';
  const ids=selTmpl.agent_ids||[];
  const groups={};
  ids.forEach(id=>{const s=AGENT_STAGE_MAP[id]||'Other';if(!groups[s])groups[s]=[];groups[s].push(id)});
  const sorted=stageOrder.filter(s=>groups[s]).concat(Object.keys(groups).filter(s=>!stageOrder.includes(s)));
  return`<div class="tpick-detail-name">${esc(selTmpl.name)}</div>
   ${selTmpl.description?`<div class="tpick-detail-sub">${esc(selTmpl.description)}</div>`:''}
   <div class="tpick-agent-count">${ids.length} agent${ids.length!==1?'s':''} included</div>
   <div class="tpick-agents">
    ${sorted.map(s=>`<div class="tpick-group-lbl">${esc(s)}</div>
     ${groups[s].map(id=>{const a=agentMap[id];return`<div class="tpick-agent-row">
      <span class="tpick-agent-ic">${AGENT_ICONS_MAP[id]||AC_ICON_FALLBACK}</span>
      <span class="tpick-agent-name">${esc(a?a.display_name:id)}</span>
     </div>`}).join('')}`).join('')}
   </div>`};
 const dlg=document.createElement('dialog');
 dlg.id='_tmplPickDlg';
 dlg.innerHTML=`
  <div class="tpick-hdr">
   <div><div class="tpick-title">Select Agent Template</div><div class="tpick-sub">${templates.length} template${templates.length!==1?'s':''} available</div></div>
   <button type="button" id="tpickX" class="icon-btn" style="flex-shrink:0"><svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </div>
  <div class="tpick-body">
   <div class="tpick-list" id="tpickList">${renderList()}</div>
   <div class="tpick-detail" id="tpickDetail">${renderDetail()}</div>
  </div>
  <div class="tpick-ftr">
   <button type="button" id="tpickBack" class="btn-secondary">← Back</button>
   <button type="button" id="tpickUse" class="btn-primary"${!selTmpl||!templates.length?' disabled':''}>Use Template →</button>
  </div>`;
 document.body.appendChild(dlg);
 dlg.showModal();
 const close=()=>{if(dlg.open)dlg.close();dlg.remove()};
 const rerender=()=>{
  dlg.querySelector('#tpickList').innerHTML=renderList();
  dlg.querySelector('#tpickDetail').innerHTML=renderDetail();
  dlg.querySelector('#tpickUse').disabled=!selTmpl;
  attachHandlers()};
 const attachHandlers=()=>{
  dlg.querySelectorAll('.tpick-row').forEach(row=>{
   row.addEventListener('click',()=>{selTmpl=templates.find(t=>t.id===row.dataset.tid)||null;rerender()})})};
 attachHandlers();
 dlg.querySelector('#tpickX').onclick=close;
 dlg.querySelector('#tpickBack').onclick=()=>{close();showAgentConfigModal()};
 dlg.querySelector('#tpickUse').onclick=()=>{
  if(!selTmpl)return;
  wizard.templateMode='template';
  wizard.templateId=selTmpl.id;
  wizard.templateName=selTmpl.name;
  wizard.templateAgentCount=(selTmpl.agent_ids||[]).length;
  wizard.selected=new Set((selTmpl.agent_ids||[]).filter(id=>!!(agentMap[id])));
  close();renderWizardStep();submitPipeline()}}

// ---- Agent registry: read-only list, live CRUD for admins -----------------
function canManageAgents(){const r=(session&&session.user&&session.user.roles)||[];return r.includes('AGENT_REGISTRY_ADMIN')||r.includes('GOVERNANCE_ADMIN')}
function csv(s){return String(s||'').split(',').map(v=>v.trim()).filter(Boolean)}
async function agentRegistryPage(){
 const items=(await api('/api/v2/agent-registry')).items||[];
 const canManage=canManageAgents();
 const statusBadge=s=>{const m={APPROVED:'badge-success',DRAFT:'badge-warning',RETIRED:'badge-neutral'};return`<span class="badge ${m[s]||'badge-neutral'}"><span class="badge-dot"></span>${esc(s||'—')}</span>`};
 const originBadge=o=>o&&o!=='CATALOGUE'?`<span class="badge badge-info" style="font-size:10px">${esc(o)}</span>`:`<span class="badge badge-neutral" style="font-size:10px">CATALOGUE</span>`;
 const enabledIcon=v=>v?`<span style="color:var(--success);font-size:13px;font-weight:700">✓</span>`:`<span style="color:var(--text-3);font-size:13px">—</span>`;
 const tableRows=!items.length?`<tr><td colspan="${canManage?10:9}" class="empty">No agents registered</td></tr>`
  :items.map(x=>`<tr>
   <td>${AGENT_CODE_MAP[x.agent_id]?`<span class="agent-code">${esc(AGENT_CODE_MAP[x.agent_id])}</span>`:`<span class="agent-code-none">—</span>`}</td>
   <td style="font-family:ui-monospace,monospace;font-size:12px;color:var(--text-2)">${esc(x.agent_id)}</td>
   <td class="td-name">${esc(x.display_name)}</td>
   <td><code>${esc(x.version)}</code></td>
   <td><code style="font-size:11px;color:var(--text-2)">${esc(x.required_role)}</code></td>
   <td><code>${esc(x.default_model_alias||'—')}</code></td>
   <td>${statusBadge(x.status)}</td>
   <td style="text-align:center">${enabledIcon(x.enabled)}</td>
   <td>${originBadge(x.origin)}</td>
   ${canManage?`<td><button class="btn-sm" data-edit-agent="${esc(x.agent_id)}" data-edit-version="${esc(x.version)}">Edit</button></td>`:''}</tr>`).join('');
 $('#view').innerHTML=`
  <div class="page-header page-header-row">
   <div><div class="page-title">Agent Registry</div><div class="page-sub">${items.length} agent${items.length!==1?'s':''} registered · governance contracts and routing rules</div></div>
   ${canManage?`<button id="addAgent" class="btn-primary"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Add agent</button>`:''}
  </div>
  <div class="card">
   <div class="card-head"><div><div class="card-title">Live Agent Catalogue</div><div class="card-sub">All registered agents, their contracts and current routing</div></div><span class="live-dot">LIVE</span></div>
   <div class="card-body"><div class="table-wrap"><table>
    <thead><tr><th>Code</th><th>Agent ID</th><th>Display Name</th><th>Version</th><th>Required Role</th><th>Model</th><th>Status</th><th>Enabled</th><th>Origin</th>${canManage?'<th></th>':''}</tr></thead>
    <tbody>${tableRows}</tbody>
   </table></div></div>
  </div>`;
 if(canManage){
  $('#addAgent').onclick=()=>openAgentDialog(null);
  document.querySelectorAll('[data-edit-agent]').forEach(b=>b.onclick=()=>{
   const row=items.find(x=>x.agent_id===b.dataset.editAgent&&x.version===b.dataset.editVersion);
   if(row)openAgentDialog(row)})}}
// ---- Agent Templates page -------------------------------------------------
async function agentTemplatesPage(){
 const [td,ad]=await Promise.all([api('/api/v2/agent-templates'),api('/api/v2/agent-registry')]);
 const items=td.items||[];
 const allAgents=(ad.items||[]).filter(x=>x.agent_id!=='migration-orchestrator'&&x.enabled&&x.status==='APPROVED');
 const agentById=Object.fromEntries(allAgents.map(a=>[a.agent_id,a]));
 const rows=!items.length
  ?`<tr><td colspan="5" class="empty">No templates yet — create one to reuse agent selections across migrations.</td></tr>`
  :items.map(t=>{
   const ids=t.agent_ids||[];
   const preview=ids.slice(0,3).map(id=>{const a=agentById[id];return esc(a?a.display_name:id)}).join(', ')+(ids.length>3?` +${ids.length-3} more`:'');
   return `<tr>
    <td class="td-name">${esc(t.name)}</td>
    <td style="font-size:12px;color:var(--text-2);max-width:260px">${esc(t.description||'—')}</td>
    <td><span class="badge badge-info">${ids.length} agent${ids.length!==1?'s':''}</span><div style="font-size:11px;color:var(--text-3);margin-top:3px">${preview}</div></td>
    <td class="td-date">${esc(fmtDt(t.created_at))}</td>
    <td style="white-space:nowrap">
     <button class="btn-sm" data-edit-tmpl="${esc(t.id)}">Edit</button>
     <button class="btn-sm" style="margin-left:4px;color:var(--error)" data-del-tmpl="${esc(t.id)}" data-del-name="${esc(t.name)}">Delete</button>
    </td>
   </tr>`;}).join('');
 $('#view').innerHTML=`
  <div class="page-header page-header-row">
   <div><div class="page-title">Agent Templates</div><div class="page-sub">${items.length} template${items.length!==1?'s':''} · reusable agent selections for new migrations</div></div>
   <button id="btnNewTmpl" class="btn-primary"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>New Template</button>
  </div>
  <div class="card">
   <div class="card-head"><div><div class="card-title">All Templates</div><div class="card-sub">Saved agent selections for the migration wizard</div></div><span class="live-dot">LIVE</span></div>
   <div class="card-body"><div class="table-wrap"><table>
    <thead><tr><th>Name</th><th>Description</th><th>Agents</th><th>Created</th><th>Actions</th></tr></thead>
    <tbody>${rows}</tbody>
   </table></div></div>
  </div>`;
 $('#btnNewTmpl').onclick=()=>showTemplateModal(null,allAgents);
 document.querySelectorAll('[data-edit-tmpl]').forEach(btn=>{
  const t=items.find(x=>x.id===btn.dataset.editTmpl);
  if(t)btn.onclick=()=>showTemplateModal(t,allAgents)});
 document.querySelectorAll('[data-del-tmpl]').forEach(btn=>{
  btn.onclick=async()=>{
   if(!confirm(`Delete template "${btn.dataset.delName}"? This cannot be undone.`))return;
   try{await api('/api/v2/agent-templates/'+btn.dataset.delTmpl,{method:'DELETE'});notice('Template deleted');agentTemplatesPage()}
   catch(e){notice(e.message,true)}}})}

function computeTemplatePruned(selectedSet,allAgents){
 const depMap={};
 allAgents.forEach(a=>{
  let deps=[];
  try{deps=Array.isArray(a.dependencies)?a.dependencies:JSON.parse(a.dependencies||'[]')}catch{}
  depMap[a.agent_id]=deps.filter(d=>allAgents.some(x=>x.agent_id===d));
 });
 const runnable=new Set(selectedSet);
 let changed=true;
 while(changed){
  changed=false;
  for(const a of allAgents){
   if(!runnable.has(a.agent_id))continue;
   const miss=(depMap[a.agent_id]||[]).find(d=>!runnable.has(d));
   if(miss){runnable.delete(a.agent_id);changed=true}
  }
 }
 const pruned={};
 for(const a of allAgents){
  if(selectedSet.has(a.agent_id)&&!runnable.has(a.agent_id)){
   const miss=(depMap[a.agent_id]||[]).find(d=>!runnable.has(d));
   if(miss){const da=allAgents.find(x=>x.agent_id===miss);pruned[a.agent_id]=da?.display_name||miss}
  }
 }
 return{runnable,pruned}}
async function showTemplateModal(template,allAgents,preselectedIds){
 const isEdit=!!(template&&template.id);
 const sortedAgents=allAgents.slice().sort((a,b)=>{
  const sa=AGENT_STAGE_MAP[a.agent_id]||'Other',sb=AGENT_STAGE_MAP[b.agent_id]||'Other';
  const oa=AGENT_STAGE_ORDER[sa]??99,ob=AGENT_STAGE_ORDER[sb]??99;
  if(oa!==ob)return oa-ob;
  return (a.display_name||'').localeCompare(b.display_name||'');});
 const selectedSet=new Set(preselectedIds!==undefined?preselectedIds:(template?template.agent_ids||[]:[]));
 const agentMap=Object.fromEntries(allAgents.map(a=>[a.agent_id,a]));
 const tbodyRows=sortedAgents.map(a=>{
  const sel=selectedSet.has(a.agent_id);
  const aid=esc(a.agent_id);
  const stage=AGENT_STAGE_MAP[a.agent_id]||'Other';
  const desc=esc(a.description||'');
  return `<tr class="ta-row${sel?' tmpl-sel':''}" data-aid="${aid}">
   <td class="ta-c-chk"><input type="checkbox" name="agent_ids" value="${aid}" tabindex="0"${sel?' checked':''}></td>
   <td class="ta-c-name"><span class="ta-ni"><span class="ic">${AGENT_ICONS_MAP[a.agent_id]||AC_ICON_FALLBACK}</span>${esc(a.display_name)}</span></td>
   <td class="ta-c-desc">${desc||'<em style="color:var(--text-3)">—</em>'}<em class="ta-prune-reason" style="display:none"></em></td>
   <td class="ta-c-stage"><span class="ta-stage-badge">${esc(stage)}</span></td>
  </tr>`;
 }).join('');
 const initPruned=computeTemplatePruned(selectedSet,allAgents).pruned;
 const initCount=[...selectedSet].filter(id=>!initPruned[id]).length;
 const dlg=document.createElement('dialog');
 dlg.id='_tmplDlg';
 dlg.innerHTML=`
  <div class="dlg-tmpl-hdr">
   <div>
    <div style="font-size:15px;font-weight:600;color:var(--text)">${isEdit?'Edit Template':'New Template'}</div>
    <div style="font-size:12px;color:var(--text-3);margin-top:2px">${isEdit?'Update the name, description, or agent selection':'Define a reusable agent selection for the migration wizard'}</div>
   </div>
   <button type="button" id="tmplX" class="icon-btn" style="flex-shrink:0">
    <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
   </button>
  </div>
  <form id="tmplForm">
   <div id="_tmplInner">
    <div class="dlg-tmpl-body">
     <p id="tmplErr" class="authhint bad" style="display:none;margin-bottom:14px"></p>
     <div class="tmpl-top-grid">
      <div><span class="dlg-tmpl-lbl">Template Name <span style="color:var(--error);text-transform:none">*</span></span><input name="name" required maxlength="120" placeholder="e.g. Full Migration Suite" value="${esc(template?.name||'')}"></div>
      <div><span class="dlg-tmpl-lbl">Description</span><input name="description" maxlength="300" placeholder="Optional — what this template is for" value="${esc(template?.description||'')}"></div>
     </div>
    </div>
    <div class="dlg-tmpl-scroll">
     <div class="tmpl-agents-hdr">
      <span class="dlg-tmpl-lbl" style="margin:0">Available Agents (${allAgents.length})</span>
      <button type="button" id="tmplSelAll" class="btn-ghost-sm">${selectedSet.size===allAgents.length?'Deselect all':'Select all'}</button>
     </div>
     <table class="tmpl-agent-tbl">
      <thead><tr><th></th><th>Agent</th><th>Description</th><th>Stage</th></tr></thead>
      <tbody>${tbodyRows}</tbody>
     </table>
    </div>
    <div class="dlg-tmpl-ftr">
     <span id="tmplSelCount" style="font-size:12px;color:var(--text-2);font-weight:500">Selected: <strong style="color:var(--primary)">${initCount}</strong> of ${allAgents.length} agents</span>
     <div style="display:flex;gap:8px">
      <button type="button" id="tmplCancel" class="btn-secondary">Cancel</button>
      <button type="submit" id="tmplSubmit" class="btn-primary">${isEdit?'Save Changes':'Create Template'}</button>
     </div>
    </div>
   </div>
  </form>`;
 document.body.appendChild(dlg);
 dlg.showModal();
 const tip=document.createElement('div');
 tip.className='ta-tooltip';
 tip.hidden=true;
 document.body.appendChild(tip);
 const applyPruning=()=>{
  const{pruned}=computeTemplatePruned(selectedSet,allAgents);
  dlg.querySelectorAll('tr.ta-row').forEach(row=>{
   const cb=row.querySelector('input[type=checkbox]');
   const aid=row.dataset.aid;
   const isPruned=!!pruned[aid];
   row.classList.toggle('tmpl-pruned',isPruned);
   row.classList.toggle('tmpl-sel',!isPruned&&selectedSet.has(aid));
   if(isPruned){cb.checked=false;cb.disabled=true}else{cb.disabled=false;cb.checked=selectedSet.has(aid)}
   const re=row.querySelector('.ta-prune-reason');
   if(re){if(isPruned){re.textContent='Skipped: depends on '+pruned[aid];re.style.display=''}else{re.textContent='';re.style.display='none'}}
  });
  const allSel=selectedSet.size===allAgents.length;
  const sb=dlg.querySelector('#tmplSelAll');
  if(sb)sb.textContent=allSel?'Deselect all':'Select all';
  const{pruned:p2}=computeTemplatePruned(selectedSet,allAgents);
  const n=[...selectedSet].filter(id=>!p2[id]).length;
  const el=dlg.querySelector('#tmplSelCount');
  if(el)el.innerHTML='Selected: <strong style="color:var(--primary)">'+n+'</strong> of '+allAgents.length+' agents'};
 dlg.querySelectorAll('tr.ta-row').forEach(row=>{
  const cb=row.querySelector('input[type=checkbox]');
  const a=agentMap[row.dataset.aid];
  row.addEventListener('click',e=>{
   if(e.target.closest('.ta-c-chk'))return;
   if(row.classList.contains('tmpl-pruned'))return;
   const aid=row.dataset.aid;
   if(selectedSet.has(aid))selectedSet.delete(aid);else selectedSet.add(aid);
   applyPruning();});
  cb.addEventListener('change',()=>{
   const aid=row.dataset.aid;
   if(cb.checked)selectedSet.add(aid);else selectedSet.delete(aid);
   applyPruning()});
  row.addEventListener('mouseenter',()=>{
   if(!a)return;
   const safeList=v=>{if(!v)return null;if(Array.isArray(v))return v.map(x=>esc(String(x)));try{const p=JSON.parse(v);return Array.isArray(p)?p.map(x=>esc(String(x))):[esc(String(v))];}catch{return[esc(String(v))];} };
   const fmtList=arr=>arr&&arr.length?arr.map(x=>`<li>${x}</li>`).join(''):'<li style="color:rgba(255,255,255,.45)">None specified</li>';
   const stage=AGENT_STAGE_MAP[a.agent_id]||'Other';
   tip.innerHTML=`<div class="ta-tooltip-arr"></div>
    <div class="ta-tooltip-name">${esc(a.display_name)}</div>
    <div class="ta-tooltip-row"><span class="ta-tooltip-lbl">Purpose</span><span>${esc(a.description||'—')}</span></div>
    <div class="ta-tooltip-section"><div class="ta-tooltip-lbl">Inputs</div><ul class="ta-tooltip-list">${fmtList(safeList(a.accepted_inputs))}</ul></div>
    <div class="ta-tooltip-section"><div class="ta-tooltip-lbl">Outputs</div><ul class="ta-tooltip-list">${fmtList(safeList(a.produced_outputs))}</ul></div>
    <div class="ta-tooltip-section"><div class="ta-tooltip-lbl">Dependencies</div><ul class="ta-tooltip-list">${fmtList(safeList(a.dependencies))}</ul></div>
    <div class="ta-tooltip-row"><span class="ta-tooltip-lbl">Stage</span><span class="ta-stage-tip">${esc(stage)}</span></div>`;
   tip.hidden=false;
   const r=row.getBoundingClientRect(),vw=window.innerWidth,vh=window.innerHeight;
   const w=tip.offsetWidth,h=tip.offsetHeight;
   let left=r.left+40,top=r.bottom+8;
   if(left+w+8>vw)left=vw-w-8;
   if(top+h+8>vh)top=r.top-h-8;
   tip.style.left=Math.max(8,left)+'px';
   tip.style.top=Math.max(8,top)+'px';
  });
  row.addEventListener('mouseleave',()=>{tip.hidden=true});});
 dlg.querySelector('#tmplSelAll')?.addEventListener('click',()=>{
  const allSel=selectedSet.size===allAgents.length;
  if(allSel)selectedSet.clear();else allAgents.forEach(a=>selectedSet.add(a.agent_id));
  applyPruning()});
 applyPruning();
 const close=()=>{if(dlg.open)dlg.close();tip.remove();dlg.remove()};
 dlg.querySelector('#tmplX').onclick=close;
 dlg.querySelector('#tmplCancel').onclick=close;
 dlg.addEventListener('close',()=>{tip.remove();dlg.remove()});
 dlg.querySelector('#tmplForm').onsubmit=async e=>{
  e.preventDefault();
  const btn=dlg.querySelector('#tmplSubmit'),err=dlg.querySelector('#tmplErr');
  const f=new FormData(e.target);
  const name=(f.get('name')||'').trim();
  const description=(f.get('description')||'').trim();
  const{pruned}=computeTemplatePruned(selectedSet,allAgents);
  const agent_ids=[...selectedSet].filter(id=>!pruned[id]);
  if(!agent_ids.length){err.style.display='';err.textContent='Select at least one agent.';return}
  btn.disabled=true;err.style.display='none';
  try{
   if(isEdit){
    await api('/api/v2/agent-templates/'+template.id,{method:'PUT',body:JSON.stringify({name,description,agent_ids})});
    notice('Template updated')
   }else{
    await api('/api/v2/agent-templates',{method:'POST',body:JSON.stringify({name,description,agent_ids})});
    notice('Template created')
   }
   close();agentTemplatesPage()
  }catch(x){err.style.display='';err.textContent=friendly(x.message);btn.disabled=false}}}

async function modelRegistryPage(){
 const items=(await api('/api/v2/model-registry')).items||[];
 const canManage=canManageAgents();
 const statusBadge=s=>{const m={APPROVED:'badge-success',DRAFT:'badge-warning',RETIRED:'badge-neutral'};return`<span class="badge ${m[s]||'badge-neutral'}"><span class="badge-dot"></span>${esc(s||'—')}</span>`};
 const tableRows=!items.length?`<tr><td colspan="${canManage?8:7}" class="empty">No models registered</td></tr>`
  :items.map(x=>`<tr>
   <td style="font-family:ui-monospace,monospace;font-weight:600">${esc(x.name)}</td>
   <td><code>${esc(x.version||'—')}</code></td>
   <td>${statusBadge(x.status)}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(x.provider_profile||'—')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(x.region||'—')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(x.approved_by||'—')}</td>
   <td style="font-size:12px;color:var(--text-3)">${esc(fmtDt(x.created_at)||'—')}</td>
   ${canManage?`<td><button class="btn-sm" data-edit-model="${esc(x.name)}" data-edit-model-ver="${esc(x.version||'')}">Edit</button></td>`:''}</tr>`).join('');
 $('#view').innerHTML=`
  <div class="page-header page-header-row">
   <div><div class="page-title">Model Registry</div><div class="page-sub">${items.length} model${items.length!==1?'s':''} registered · aliases approved for agent routing</div></div>
   ${canManage?`<button id="addModel" class="btn-primary"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Add model</button>`:''}
  </div>
  <div class="card">
   <div class="card-head"><div><div class="card-title">Approved Model Aliases</div><div class="card-sub">Model registry entries used by agent LLM binding validation</div></div><span class="live-dot">LIVE</span></div>
   <div class="card-body"><div class="table-wrap"><table>
    <thead><tr><th>Name / Alias</th><th>Version</th><th>Status</th><th>Provider Profile</th><th>Region</th><th>Approved By</th><th>Created</th>${canManage?'<th></th>':''}</tr></thead>
    <tbody>${tableRows}</tbody>
   </table></div></div>
  </div>`;
 if(canManage){
  $('#addModel').onclick=()=>openModelDialog(null);
  document.querySelectorAll('[data-edit-model]').forEach(b=>b.onclick=()=>{
   const row=items.find(x=>x.name===b.dataset.editModel&&(x.version||'')===(b.dataset.editModelVer||''));
   if(row)openModelDialog(row)})}}
async function openModelDialog(row){
 const f=$('#modelForm');
 $('#modelDialogTitle').textContent=row?'Edit model':'Add model';
 $('#modelError').textContent='';
 f.reset();
 if(row){
  f.name.value=row.name;f.name.readOnly=true;
  f.version.value=row.version||'';
  f.status.value=row.status||'APPROVED';
  f.provider_profile.value=row.provider_profile||'';
  f.policy.value=row.policy||'';
  f.region.value=row.region||'';
 }else{f.name.readOnly=false;}
 $('#modelDialog').showModal()}
function skillStatusBadge(s){const m={APPROVED:'badge-success',DRAFT:'badge-warning',DEPRECATED:'badge-neutral'};return`<span class="badge ${m[s]||'badge-neutral'}"><span class="badge-dot"></span>${esc(s||'—')}</span>`}
async function skillRegistryPage(){
 const canManage=canManageAgents();
 const [skillsRes,runtimesRes]=await Promise.all([api('/api/v2/skill-registry'),api('/api/v2/skill-runtimes')]);
 const skills=skillsRes.items||[];
 const runtimes=runtimesRes.items||[];
 const runtimeMap=Object.fromEntries(runtimes.map(r=>[r.id,r]));
 const plusSvg=`<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
 const runtimeRows=!runtimes.length?`<tr><td colspan="${canManage?7:6}" class="empty">No runtimes configured</td></tr>`
  :runtimes.map(r=>`<tr>
   <td style="font-family:ui-monospace,monospace;font-weight:600">${esc(r.display_name||r.name)}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(r.language)} ${esc(r.language_version||'')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(r.framework||'—')} ${esc(r.framework_version_range||'')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(r.build_tool||'—')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(r.base_image||'—')}</td>
   <td><span class="badge ${r.enabled?'badge-success':'badge-neutral'}"><span class="badge-dot"></span>${r.enabled?'Enabled':'Disabled'}</span> <span style="font-size:11px;color:var(--text-3)">${r.skill_count||0} skill${r.skill_count!==1?'s':''}</span></td>
   ${canManage?`<td><button class="btn-sm" data-edit-runtime="${esc(r.id)}">Edit</button></td>`:''}</tr>`).join('');
 const skillRows=!skills.length?`<tr><td colspan="${canManage?7:6}" class="empty">No skills registered yet. Use Register Skill to add one.</td></tr>`
  :skills.map(x=>{const rt=runtimeMap[x.target_runtime_id]||{};return`<tr>
   <td style="font-family:ui-monospace,monospace;font-weight:600">${esc(x.skill_key)}<div class="td-svc-id">v${esc(x.version)}</div></td>
   <td style="font-size:12px">${esc(x.display_name||'—')}</td>
   <td>${skillStatusBadge(x.status)}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(rt.display_name||rt.name||'—')}</td>
   <td style="font-size:12px;color:var(--text-2)">${esc(x.source_path||'—')}</td>
   <td style="font-size:12px;color:var(--text-3)">${fmtDt(x.created_at)||'—'}</td>
   ${canManage?`<td><button class="btn-sm" data-edit-skill="${esc(x.id)}">Edit</button></td>`:''}</tr>`}).join('');
 $('#view').innerHTML=`
  <div class="page-header">
   <div class="page-title">Skill Registry</div>
   <div class="page-sub">${skills.length} skill${skills.length!==1?'s':''} registered · ${runtimes.length} runtime${runtimes.length!==1?'s':''} configured</div>
  </div>
  <div class="card" style="margin-bottom:20px">
   <div class="card-head">
    <div><div class="card-title">Target Runtimes</div><div class="card-sub">Execution environments available for skill targeting</div></div>
    <div style="display:flex;align-items:center;gap:12px"><span class="live-dot">LIVE</span>${canManage?`<button id="addRuntime" class="btn-secondary">${plusSvg}Add Runtime</button>`:''}</div>
   </div>
   <div class="card-body"><div class="table-wrap"><table>
    <thead><tr><th>Runtime</th><th>Language</th><th>Framework</th><th>Build Tool</th><th>Base Image</th><th>Status</th>${canManage?'<th></th>':''}</tr></thead>
    <tbody>${runtimeRows}</tbody>
   </table></div></div>
  </div>
  <div class="card">
   <div class="card-head">
    <div><div class="card-title">Registered Skills</div><div class="card-sub">Versioned migration procedures paired to target runtimes</div></div>
    <div style="display:flex;align-items:center;gap:12px"><span class="live-dot">LIVE</span>${canManage?`<button id="addSkill" class="btn-primary">${plusSvg}Register Skill</button>`:''}</div>
   </div>
   <div class="card-body"><div class="table-wrap"><table>
    <thead><tr><th>Skill Key / Version</th><th>Display Name</th><th>Status</th><th>Target Runtime</th><th>Source Path</th><th>Created</th>${canManage?'<th></th>':''}</tr></thead>
    <tbody>${skillRows}</tbody>
   </table></div></div>
  </div>`;
 if(canManage){
  $('#addSkill').onclick=()=>openSkillDialog(null,runtimes);
  $('#addRuntime').onclick=()=>openRuntimeDialog(null);
  document.querySelectorAll('[data-edit-skill]').forEach(b=>b.onclick=()=>{
   const row=skills.find(x=>x.id===b.dataset.editSkill);
   if(row)openSkillDialog(row,runtimes)});
  document.querySelectorAll('[data-edit-runtime]').forEach(b=>b.onclick=()=>{
   const row=runtimes.find(x=>x.id===b.dataset.editRuntime);
   if(row)openRuntimeDialog(row)})}}
async function openSkillDialog(row,runtimes){
 const f=$('#skillForm');
 $('#skillDialogTitle').textContent=row?'Edit Skill Status':'Register Skill';
 $('#skillError').textContent='';
 f.reset();
 const sel=$('#skillRuntimeSelect');
 sel.innerHTML=(runtimes||[]).map(r=>`<option value="${esc(r.id)}">${esc(r.display_name||r.name)}</option>`).join('');
 if(row){
  f.skill_key.value=row.skill_key;f.skill_key.readOnly=true;
  f.version.value=row.version;f.version.readOnly=true;
  f.display_name.value=row.display_name||'';
  f.description.value=row.description||'';
  f.status.value=row.status||'DRAFT';
  f.target_runtime_id.value=row.target_runtime_id||'';
  f.source_path.value=row.source_path||'';
  f.backend_types.value=row.backend_types?JSON.parse(row.backend_types||'[]').join(', '):'';
 }else{
  f.skill_key.readOnly=false;
  f.version.readOnly=false;}
 const dlg=$('#skillDialog');
 dlg.showModal();
 f.onsubmit=async e=>{
  e.preventDefault();
  const btn=$('#skillSubmit');btn.disabled=true;
  try{
   const vals=Object.fromEntries(new FormData(f));
   if(vals.backend_types)vals.backend_types=JSON.stringify(vals.backend_types.split(',').map(v=>v.trim()).filter(Boolean));
   if(row){
    await api('/api/v2/skill-registry/'+row.id,{method:'PUT',body:JSON.stringify({status:vals.status})});
    notice('Skill status updated');
   }else{
    await api('/api/v2/skill-registry',{method:'POST',body:JSON.stringify(vals)});
    notice('Skill registered');
   }
   dlg.close();skillRegistryPage();
  }catch(err){$('#skillError').textContent=err.message||'Error';btn.disabled=false;}}}
async function openRuntimeDialog(row){
 const f=$('#skillRuntimeForm');
 $('#skillRuntimeDialogTitle').textContent=row?'Edit Runtime':'Add Runtime';
 $('#skillRuntimeError').textContent='';
 f.reset();
 if(row){
  f.name.value=row.name;f.name.readOnly=true;
  f.display_name.value=row.display_name||'';
  f.language.value=row.language||'java';
  f.language_version.value=row.language_version||'';
  f.framework.value=row.framework||'';
  f.framework_version_range.value=row.framework_version_range||'';
  f.build_tool.value=row.build_tool||'';
  f.base_image.value=row.base_image||'';
 }else{f.name.readOnly=false;}
 const dlg=$('#skillRuntimeDialog');
 dlg.showModal();
 f.onsubmit=async e=>{
  e.preventDefault();
  const btn=$('#skillRuntimeSubmit');btn.disabled=true;
  try{
   const vals=Object.fromEntries(new FormData(f));
   if(row){vals.id=row.id;await api('/api/v2/skill-runtimes/'+row.id,{method:'PUT',body:JSON.stringify(vals)});}
   else{await api('/api/v2/skill-runtimes',{method:'POST',body:JSON.stringify(vals)});}
   notice(row?'Runtime updated':'Runtime added');
   dlg.close();skillRegistryPage();
  }catch(err){$('#skillRuntimeError').textContent=err.message||'Error';btn.disabled=false;}}}
async function openAgentDialog(row){
 const dlg=$('#agentDialog'),f=$('#agentForm');
 $('#agentDialogTitle').textContent=row?'Edit agent':'Add agent';$('#agentError').textContent='';const _sub=$('#agentDialog').querySelector('.dlg-sub');if(_sub)_sub.hidden=!!row;
 f.reset();f.dataset.editing=row?'1':'';
 $('#llmBindingSection').hidden=!row;$('#agentSummary').hidden=!row;$('#llmConfigureOverrideRow').hidden=true;$('#llmEffectivePreview').innerHTML='';$('#llmValidateResult').textContent='';
 if($('#overrideConfigDialog').open)$('#overrideConfigDialog').close();
 $('#drwAliasInput').value='';$('#drw_model_id').value='';$('#drw_model_revision').value='';$('#drw_provider_type').value='litellm_alias';$('#drw_reason_for_change').value='';$('#drw_capabilities').value='';$('#drw_licence_id').value='';$('#drw_data_region').value='';$('#drw_data_classification_allowed').value='';$('#drw_context_limit').value='';$('#drw_output_limit').value='';
 $('#drwError').textContent='';['drwAdvSettings','drwCompliance','drwLimits'].forEach(id=>{const el=$('#'+id);if(el&&el.hasAttribute('open'))el.removeAttribute('open')});
 if(row){
  const sMap={APPROVED:'badge-success',DRAFT:'badge-warning',RETIRED:'badge-neutral'};
  $('#agentSummary').innerHTML=`<div class="dlg-agent-name">${esc(row.display_name)}</div><div class="dlg-agent-sub"><span class="badge ${sMap[row.status]||'badge-neutral'}">${esc(row.status)}</span><span class="dlg-agent-sep"></span><span class="dlg-agent-ver">v${esc(row.version)}</span><span class="dlg-agent-sep"></span><span class="asumm-tag ${row.enabled?'enabled':'disabled'}">${row.enabled?'Enabled':'Disabled'}</span></div>`;
  const _ds=$('#agentDetailsSection'),_cs=$('#contractSection');if(_ds)_ds.removeAttribute('open');if(_cs)_cs.removeAttribute('open');
  $('#ocdAgentName').textContent=row.display_name||row.agent_id;
  f.agent_id.value=row.agent_id;f.agent_id.readOnly=true;
  f.version.value=row.version;f.version.readOnly=true;
  f.display_name.value=row.display_name;f.description.value=row.description||'';
  f.required_role.value=row.required_role;f.status.value=row.status||'APPROVED';
  f.accepted_inputs.value=(JSON.parse(row.accepted_inputs||'[]')).join(', ');
  f.produced_outputs.value=(JSON.parse(row.produced_outputs||'[]')).join(', ');
  f.tool_allowlist.value=(JSON.parse(row.tool_allowlist||'[]')).join(', ');
  f.dependencies.value=(JSON.parse(row.dependencies||'[]')).join(', ');
  f.enabled.checked=!!row.enabled;
  try{
   const [b,hist]=await Promise.all([
    api('/api/v2/agents/'+row.agent_id+'/llm-binding'),
    api('/api/v2/agents/'+row.agent_id+'/llm-binding/history').catch(()=>({items:[]}))
   ]);
   const mode=(b.binding&&b.binding.binding_mode)||'DEFAULT';
   f.querySelectorAll('[name="llm_mode"]').forEach(r=>{r.checked=r.value===mode});
   $('#llmConfigureOverrideRow').hidden=mode!=='AGENT_OVERRIDE';
   if(mode==='AGENT_OVERRIDE'&&b.binding){
    $('#drwAliasInput').value=b.binding.gateway_model_alias||'';
    $('#drw_model_id').value=b.binding.model_id||'';
    $('#drw_model_revision').value=b.binding.model_revision||'';
    $('#drw_provider_type').value=b.binding.provider_type||'litellm_alias';
    $('#drw_reason_for_change').value=b.binding.reason_for_change||'';
    $('#drw_capabilities').value=b.binding.capabilities||'';
    $('#drw_licence_id').value=b.binding.licence_id||'';
    $('#drw_data_region').value=b.binding.data_region||'';
    $('#drw_data_classification_allowed').value=b.binding.data_classification_allowed||'';
    $('#drw_context_limit').value=b.binding.context_limit||'';
    $('#drw_output_limit').value=b.binding.output_limit||'';}
   const bstatus=(b.binding&&b.binding.status)||'';
   $('#llmActionBar').hidden=!bstatus||mode!=='AGENT_OVERRIDE';
   $('#llmValidateBtn').hidden=bstatus!=='DRAFT';
   $('#llmActivateBtn').hidden=bstatus!=='DRAFT';
   $('#llmRevertBtn').hidden=bstatus!=='ACTIVE';
   $('#llmValidateResult').textContent='';
   $('#agentSubmit').textContent=mode==='AGENT_OVERRIDE'?'Save as Draft':'Save';
   {const _ea=b.effective_alias||b.default_model_alias||'(none)';const _src=b.binding_source||'DEFAULT';const _sc=bstatus==='ACTIVE'?'var(--success-text)':bstatus==='DRAFT'?'var(--warning-text)':'var(--text-2)';const _stxt=bstatus==='ACTIVE'?'active override':bstatus==='DRAFT'?'draft (pending)':_src==='DEFAULT'?'platform default':'override';$('#llmEffectivePreview').innerHTML=`<span class="llm-eff-label">Effective</span><code class="llm-eff-val">${esc(_ea)}</code><span class="llm-eff-status" style="color:${_sc}">${esc(_stxt)}</span>`;}
   const items=(hist&&hist.items)||[];
   const hs=$('#llmHistorySection');hs.hidden=!items.length;
   if(items.length){
    const canMgr=canManageAgents();
    $('#llmHistoryList').innerHTML=items.map((h,i)=>{
     const ts=(h.created_at||'').slice(0,19).replace('T',' ');
     const alias=h.gateway_model_alias?`<code style="font-size:11px;margin:0 2px">${esc(h.gateway_model_alias)}</code>`:'';
     const modeChip=h.binding_mode==='AGENT_OVERRIDE'?`<span style="font-size:10px;background:rgba(37,99,235,.1);color:var(--primary);border-radius:3px;padding:1px 5px;font-weight:600">OVERRIDE</span> `:'';
     const canRestore=canMgr&&h.binding_mode==='AGENT_OVERRIDE'&&(h.action==='SAVED'||h.action==='ACTIVATED');
     return `<div style="padding:8px 0;border-bottom:1px solid var(--border)" data-hist-idx="${i}">`+
      `<div style="display:flex;align-items:flex-start;gap:8px;justify-content:space-between">`+
      `<div><span style="font-size:11px;color:var(--text-3)">${esc(ts)}</span> ${modeChip}<strong style="font-size:12px">${esc(h.action)}</strong>`+
      (alias?' '+alias:'')+
      ` <span style="font-size:11px;color:var(--text-3)">by ${esc(h.actor||'system')}</span></div>`+
      (canRestore?`<button type="button" class="btn-sm dlg-hist-restore" data-idx="${i}" style="flex-shrink:0;font-size:11px;padding:3px 8px">Restore</button>`:``)+'</div>'+
      (h.model_id?`<div style="font-size:11px;color:var(--text-3);margin-top:2px">Model: <code style="font-size:10.5px">${esc(h.model_id)}</code></div>`:'')+
      (h.reason_for_change?`<div style="font-size:11px;color:var(--text-3);margin-top:2px;font-style:italic">${esc(h.reason_for_change)}</div>`:'')+'</div>';
    }).join('');
    if(canMgr)$('#llmHistoryList').querySelectorAll('.dlg-hist-restore').forEach(btn=>{
     const h=items[+btn.dataset.idx];
     btn.onclick=async()=>{
      const agentId=$('#agentForm').agent_id.value;
      const err=$('#drwError');err.textContent='';btn.disabled=true;
      try{
       await api('/api/v2/agents/'+agentId+'/llm-binding',{method:'POST',body:JSON.stringify({
        binding_mode:'AGENT_OVERRIDE',
        gateway_model_alias:h.gateway_model_alias||'',
        model_id:h.model_id||'',
        model_revision:h.model_revision||'',
        provider_type:h.provider_type||'litellm_alias',
        reason_for_change:'Restored from history ('+esc(h.created_at||'')+')'+
         (h.reason_for_change?' — orig: '+h.reason_for_change:'')
       })});
       $('#agentDialog').close();
       notice('Binding restored from history as draft — review and activate to apply');
       agentRegistryPage();
      }catch(e){err.textContent=friendly(e.message);btn.disabled=false}
     };
    });
   }
  }catch(e){$('#llmActionBar').hidden=true;$('#llmHistorySection').hidden=true;$('#llmEffectivePreview').innerHTML=`<span class="llm-eff-label">Effective</span><code class="llm-eff-val">${esc(row.default_model_alias||'(none)')}</code><span class="llm-eff-status">platform default</span>`}
 }else{const _ds=$('#agentDetailsSection'),_cs=$('#contractSection');if(_ds)_ds.setAttribute('open','');if(_cs)_cs.setAttribute('open','');f.agent_id.readOnly=false;f.version.readOnly=false;f.version.value='1.0.0';f.status.value='APPROVED';f.enabled.checked=true}
 dlg.showModal()}
$('#agentCancel').onclick=()=>$('#agentDialog').close();
$('#modelCancel').onclick=()=>$('#modelDialog').close();
$('#skillCancel').onclick=()=>$('#skillDialog').close();
$('#skillRuntimeCancel').onclick=()=>$('#skillRuntimeDialog').close();
$('#modelForm').onsubmit=async e=>{
 e.preventDefault();const btn=$('#modelSubmit'),err=$('#modelError'),f=e.target;btn.disabled=true;err.textContent='';
 try{
  const result=await api('/api/v2/model-registry',{method:'POST',body:JSON.stringify({
   name:f.name.value.trim(),
   version:f.version.value.trim()||'1.0',
   status:f.status.value,
   provider_profile:f.provider_profile.value.trim(),
   policy:f.policy.value.trim(),
   region:f.region.value.trim()
  })});
  $('#modelDialog').close();
  notice(`${result.name} registered as ${result.status}`);
  modelRegistryPage()
 }catch(x){err.textContent=friendly(x.message)}
 finally{btn.disabled=false}};
$('#agentDialog').addEventListener('close',()=>{if($('#overrideConfigDialog').open)$('#overrideConfigDialog').close();});
document.querySelectorAll('[name="llm_mode"]').forEach(r=>r.addEventListener('change',e=>{$('#llmConfigureOverrideRow').hidden=e.target.value!=='AGENT_OVERRIDE';$('#agentSubmit').textContent=e.target.value==='AGENT_OVERRIDE'?'Save as Draft':'Save'}));
// ── Model alias combobox ─────────────────────────────────────────────────────
(function(){
 const combo=$('#drwAliasCombo'),list=$('#drwAliasComboList');
 if(!combo||!list)return;
 const inp=combo.querySelector('.alias-combo-input');
 const OPTS=[
  {v:'reasoning-large',d:'Large reasoning model (Qwen3-32B)'},
  {v:'reasoning-small',d:'Fast reasoning model (Qwen3-8B)'},
  {v:'coding',         d:'Code generation model (Qwen3-Coder)'},
  {v:'embeddings',     d:'Embedding model'},
  {v:'semantic-review',d:'Semantic analysis model'}
 ];
 let active=-1;
 function render(q){
  const f=(q||'').toLowerCase().trim();
  const shown=f?OPTS.filter(o=>o.v.includes(f)||o.d.toLowerCase().includes(f)):OPTS;
  active=-1;
  list.innerHTML=shown.map(o=>`<div class="alias-opt" data-val="${esc(o.v)}" role="option" aria-selected="false"><span class="alias-opt-val">${esc(o.v)}</span><span class="alias-opt-desc">${esc(o.d)}</span></div>`).join('');
  list.hidden=!shown.length;
  inp.setAttribute('aria-expanded',String(!list.hidden));
 }
 function pick(val){inp.value=val;list.hidden=true;active=-1;inp.setAttribute('aria-expanded','false');}
 function setActive(i){
  const items=[...list.querySelectorAll('.alias-opt')];
  items.forEach((el,j)=>{el.classList.toggle('alias-opt-active',j===i);el.setAttribute('aria-selected',j===i?'true':'false');});
  if(i>=0&&items[i])items[i].scrollIntoView({block:'nearest'});
  active=i;
 }
 inp.addEventListener('focus',()=>render(inp.value));
 inp.addEventListener('input',()=>render(inp.value));
 inp.addEventListener('keydown',e=>{
  const items=[...list.querySelectorAll('.alias-opt')];
  if(e.key==='ArrowDown'){e.preventDefault();setActive(Math.min(active+1,items.length-1));}
  else if(e.key==='ArrowUp'){e.preventDefault();setActive(Math.max(active-1,0));}
  else if(e.key==='Enter'&&active>=0){e.preventDefault();pick(items[active].dataset.val);}
  else if(e.key==='Escape'){list.hidden=true;active=-1;inp.setAttribute('aria-expanded','false');inp.blur();}
 });
 list.addEventListener('mousedown',e=>{const o=e.target.closest('.alias-opt');if(o){e.preventDefault();pick(o.dataset.val);}});
 document.addEventListener('click',e=>{if(!combo.contains(e.target)){list.hidden=true;active=-1;inp.setAttribute('aria-expanded','false');}});
})();
$('#llmValidateBtn').onclick=async()=>{
 const agentId=$('#agentForm').agent_id.value;
 const res=$('#llmValidateResult');
 res.style.color='';res.textContent='Validating…';
 try{
  const r=await api('/api/v2/agents/'+agentId+'/llm-binding/validate',{method:'POST',body:'{}'});
  if(r.valid){
   res.style.color='var(--success,#2e7d32)';
   const w=r.warnings&&r.warnings.length?' ('+r.warnings.join('; ')+')':'';
   res.textContent='✓ Valid'+w;
  }else{
   res.style.color='var(--error,#c62828)';
   res.textContent='✗ '+(r.errors||[]).join('; ');
  }
 }catch(e){res.style.color='var(--error,#c62828)';res.textContent=friendly(e.message);}
};
$('#llmActivateBtn').onclick=async()=>{const agentId=$('#agentForm').agent_id.value;try{await api('/api/v2/agents/'+agentId+'/llm-binding/activate',{method:'POST',body:'{}'});$('#overrideConfigDialog').close();$('#agentDialog').close();notice(agentId+' LLM binding activated');agentRegistryPage()}catch(e){$('#drwError').textContent=friendly(e.message)}};
$('#llmRevertBtn').onclick=async()=>{const agentId=$('#agentForm').agent_id.value;try{await api('/api/v2/agents/'+agentId+'/llm-binding',{method:'DELETE'});$('#overrideConfigDialog').close();$('#agentDialog').close();notice(agentId+' reverted to default model');agentRegistryPage()}catch(e){$('#drwError').textContent=friendly(e.message)}};
// Override config dialog
$('#openOverrideDialogBtn').onclick=()=>$('#overrideConfigDialog').showModal();
$('#ocdCloseBtn').onclick=()=>$('#overrideConfigDialog').close();
$('#ocdDoneBtn').onclick=()=>$('#overrideConfigDialog').close();
$('#agentForm').onsubmit=async e=>{
 e.preventDefault();const btn=$('#agentSubmit'),err=$('#agentError'),f=e.target;btn.disabled=true;err.textContent='';
 try{
  const payload={agent_id:f.agent_id.value.trim().toLowerCase(),version:f.version.value.trim(),
   display_name:f.display_name.value.trim(),description:f.description.value.trim(),
   required_role:f.required_role.value.trim().toUpperCase(),status:f.status.value,
   accepted_inputs:csv(f.accepted_inputs.value),produced_outputs:csv(f.produced_outputs.value),
   tool_allowlist:csv(f.tool_allowlist.value),dependencies:csv(f.dependencies.value),enabled:f.enabled.checked};
  const result=await api('/api/v2/agent-registry',{method:'POST',body:JSON.stringify(payload)});
  if(f.dataset.editing){
   const llmMode=(f.querySelector('[name="llm_mode"]:checked')||{value:'DEFAULT'}).value;
   const isOverride=llmMode==='AGENT_OVERRIDE';
   if(isOverride&&!($('#drwAliasInput').value||'').trim()){err.textContent='Agent Override requires a model alias. Click Configure Override to set one.';btn.disabled=false;return;}
   await api('/api/v2/agents/'+payload.agent_id+'/llm-binding',{method:'POST',body:JSON.stringify({
    binding_mode:llmMode,
    gateway_model_alias:isOverride?($('#drwAliasInput').value||'').trim():'',
    model_id:isOverride?($('#drw_model_id').value||'').trim():'',
    model_revision:isOverride?($('#drw_model_revision').value||'').trim():'',
    provider_type:isOverride?($('#drw_provider_type').value||'litellm_alias'):'',
    reason_for_change:($('#drw_reason_for_change').value||'').trim(),
    capabilities:isOverride?($('#drw_capabilities').value||'').trim():'',
    licence_id:isOverride?($('#drw_licence_id').value||'').trim():'',
    data_region:isOverride?($('#drw_data_region').value||'').trim():'',
    data_classification_allowed:isOverride?($('#drw_data_classification_allowed').value||'').trim():'',
    context_limit:isOverride?parseInt($('#drw_context_limit').value||'0',10):0,
    output_limit:isOverride?parseInt($('#drw_output_limit').value||'0',10):0
   })});}
  $('#agentDialog').close();
  if(f.dataset.editing&&(f.querySelector('[name="llm_mode"]:checked')||{value:'DEFAULT'}).value==='AGENT_OVERRIDE'){
   notice(`${result.agent_id} — binding saved as draft. Reopen Edit to validate and activate.`);
  }else{
   notice(`${result.agent_id} ${result.version} saved`+(result.catalogue_file_synced?'':' (catalogue file not writable — live only until redeployed)'),!result.catalogue_file_synced);
  }
  agentRegistryPage()
 }catch(x){err.textContent=friendly(x.message)}
 finally{btn.disabled=false}};

// ---- Pipelines: list and live detail view ---------------------------------
const STEP_STATUS_LABEL={PENDING:'Pending',QUEUED:'Queued',RUNNING:'Running',COMPLETED:'Completed',FAILED:'Failed',SKIPPED:'Skipped',CANCELLED:'Cancelled',WAITING_APPROVAL:'Waiting approval'};
const STEP_KIND_LABEL={CLONE:'Clone',AGENT:'Agent',DELIVERY:'Delivery'};
const AGENT_DESC_MAP={
 'repository-intake':'Clones and indexes the ACE repository structure and artefacts.',
 'dormancy-intelligence':'Identifies unused integration flows, dead code, and inactive routes.',
 'framework-baseline':'Extracts Spring Boot conventions from the reference repository.',
 'ace-analysis':'Analyses ACE flows, mappings, and integration patterns.',
 'migration-specification':'Generates a structured specification for each integration component.',
 'planning':'Produces the step-by-step migration execution plan.',
 'code-generation':'Transforms ACE artefacts into Spring Boot code.',
 'test-engineering':'Generates unit and integration tests for the migrated code.',
 'variance-analysis':'Compares generated code against the specification for drift.',
 'rca':'Identifies root causes for detected variances.',
 'remediation':'Applies targeted corrections to close variance gaps.',
 'governance':'Enforces gate policies and records approval decisions.',
 'knowledge-evidence':'Captures evidence artefacts for audit and knowledge transfer.',
};
const STEP_STAGE_ORDER={'Setup':0,'Analysis':1,'Generation':2,'Quality Assurance':3,'Governance & Platform':4,'Delivery':5,'Other':99};
function stepStage(step){
 if(step.kind==='DELIVERY')return 'Delivery';
 if(step.kind==='CLONE')return 'Setup';
 return AGENT_STAGE_MAP[step.agent_id]||'Other'}
const CLONE_ICON=`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`;
const DELIVERY_ICON=`<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="1" y="3" width="15" height="13" rx="1"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>`;
function stepIcon(step){
 if(step.kind==='CLONE')return CLONE_ICON;
 if(step.kind==='DELIVERY')return DELIVERY_ICON;
 return AGENT_ICONS_MAP[step.agent_id]||AC_ICON_FALLBACK}
function stepDesc(step){
 if(step.kind==='CLONE'){const r=stepParams(step).role||'';return 'Clone '+(r==='ACE'?'ACE integration':r==='FRAMEWORK'?'framework reference':r==='TARGET'?'output target':'')+ ' repository.'}
 if(step.kind==='DELIVERY')return 'Push generated Spring Boot code to the target repository.';
 return AGENT_DESC_MAP[step.agent_id]||''}
function stepCard(step){
 const clickable=step.status==='COMPLETED'||step.status==='RUNNING'||step.status==='FAILED'||step.status==='WAITING_APPROVAL';
 const dur=step.started_at&&step.completed_at?((new Date(step.completed_at)-new Date(step.started_at))/1000).toFixed(1)+'s':'';
 return `<div class="step-card ${esc(step.status)}" data-step="${esc(step.id)}">
  <div class="step-card-top">
   <div class="step-icon">${stepIcon(step)}</div>
   <span class="step-name">${esc(stepLabel(step))}</span>
   <div class="step-status-dot status-dot-${esc(step.status)}"></div>
  </div>
  <div class="step-desc">${esc(stepDesc(step))}</div>
  <div class="step-footer">
   <span class="step-status-label ${esc(step.status)}">${esc(STEP_STATUS_LABEL[step.status]||step.status)}</span>
   ${clickable?`<span class="step-cta">View output →</span>`:dur?`<span class="step-duration">${esc(dur)}</span>`:''}
  </div>
 </div>`}
function syntaxHighlightJson(obj){
 return JSON.stringify(obj,null,2).replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,m=>{
  if(/^"/.test(m)){if(/:$/.test(m))return`<span class="jk">${m}</span>`;return`<span class="jv-str">${m}</span>`}
  if(/true|false/.test(m))return`<span class="jv-bool">${m}</span>`;
  if(/null/.test(m))return`<span class="jv-null">${m}</span>`;
  return`<span class="jv-num">${m}</span>`})}
async function pipelineList(){
 const items=(await api('/api/v2/pipelines')).items||[];
 const rows=!items.length?'<div class="empty">No pipelines yet. Use New migration to create one.</div>'
  :`<div class="table-wrap"><table><thead><tr><th>Workspace</th><th>Status</th><th>Created by</th><th>Created at</th></tr></thead><tbody>${items.map(x=>`<tr class="tr-clickable" data-id="${esc(x.id)}"><td class="td-name">${esc(x.workspace_dir)}</td><td>${stateBadge(x.status||'')}</td><td class="td-light">${esc(x.created_by)}</td><td class="td-date">${esc(fmtDt(x.created_at))}</td></tr>`).join('')}</tbody></table></div>`;
 $('#view').innerHTML=`<div class="page-header page-header-row"><div><div class="page-title">Migration Pipelines</div><div class="page-sub">Every pipeline created from the migration wizard</div></div><button class="btn-primary btn-new-mig"><svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Migration</button></div><div class="card"><div class="card-head"><div class="card-title">All Pipelines</div></div><div class="card-body">${rows}</div></div>`;
 $('#view').querySelector('.btn-new-mig')?.addEventListener('click',()=>location.hash='new-migration');
 document.querySelectorAll('#view tr[data-id]').forEach(tr=>{tr.onclick=()=>{location.hash='pipelines/'+tr.dataset.id}})}
function stepParams(step){
 // params is a JSON string in raw API responses but pre-parsed by the /output endpoint
 return typeof step.params==='string'?JSON.parse(step.params||'{}'):(step.params||{})}
function stepLabel(step){
 if(step.kind==='CLONE')return 'Clone '+(stepParams(step).role||'Repository');
 if(step.kind==='DELIVERY')return 'Push Generated Code';
 return step.agent_id.split('-').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ')}
async function pipelineDetail(id){
 const p=await api('/api/v2/pipelines/'+id);
 $('#title').textContent=p.workspace_dir||'Pipeline';
 const counts={};p.steps.forEach(s=>counts[s.status]=(counts[s.status]||0)+1);
 const done=(counts.COMPLETED||0),total=p.steps.length,pct=total?Math.round(done/total*100):0;
 const statusBadgeClass=s=>({COMPLETED:'completed',RUNNING:'running',FAILED:'failed',PENDING:'pending',QUEUED:'queued',SKIPPED:'skipped',CANCELLED:'cancelled',WAITING_APPROVAL:'warning'}[s]||'pending');
 const actions=[
  p.status==='DRAFT'?`<button id="pConfirm" class="btn-sm primary">Confirm &amp; Run</button>`:'',
  p.status==='READY'?`<button id="pRun" class="btn-sm primary">Run pipeline</button>`:'',
  p.status==='RUNNING'?`<button id="pPause" class="btn-sm">Pause</button>`:'',
  p.status==='PAUSED'?`<button id="pResume" class="btn-sm primary">Resume</button>`:'',
  p.status==='WAITING_APPROVAL'?`<button id="pPause" class="btn-sm">Pause</button>`:'',
  (p.status==='RUNNING'||p.status==='PAUSED'||p.status==='WAITING_APPROVAL')?`<button id="pCancel" class="btn-sm danger">Stop</button>`:'',
 ].join('');
 // Group steps by stage
 const stageMap={};
 p.steps.forEach(s=>{const sg=stepStage(s);const ord=STEP_STAGE_ORDER[sg]??99;if(!stageMap[sg])stageMap[sg]={order:ord,steps:[]};stageMap[sg].steps.push(s)});
 const stageGroups=Object.entries(stageMap).sort((a,b)=>a[1].order-b[1].order);
 $('#view').innerHTML=`
  <div class="pipeline-card pc-${p.status.toLowerCase()}">
   <div class="pipeline-card-top">
    <div>
     <div class="pipeline-id">${esc(p.workspace_dir)}</div>
     <div class="pipeline-meta">
      <span class="pipeline-status-chip psc-${p.status.toLowerCase()}">${esc(p.status)}</span>
      <span>By ${esc(p.created_by)}</span>
      <span>${esc(fmtDt(p.created_at))}</span>
     </div>
    </div>
    <div class="pipeline-actions">${actions}</div>
   </div>
   <div class="pipeline-progress-row">
    <div class="progress-bar-wrap"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
    <span class="progress-label">${done} / ${total} steps complete</span>
   </div>
   <div class="status-badges">${Object.entries(counts).map(([k,v])=>`<span class="badge badge-${statusBadgeClass(k)}">${v} ${esc(STEP_STATUS_LABEL[k]||k).toLowerCase()}</span>`).join('')}</div>
  </div>
  <div id="stageContainer">
   ${stageGroups.map(([sg,g])=>`<div class="stage-group"><div class="stage-label">${esc(sg)}</div><div class="step-grid">${g.steps.map(stepCard).join('')}</div></div>`).join('')}
  </div>`;
 if(p.status==='DRAFT')$('#pConfirm').onclick=async()=>{
  try{await api('/api/v2/pipelines/'+id+'/confirm',{method:'POST'});
      try{await api('/api/v2/pipelines/'+id+'/run',{method:'POST'})}catch(e2){}
      pipelineDetail(id)}catch(e){notice(e.message,true)}};
 if(p.status==='READY')$('#pRun').onclick=async()=>{try{await api('/api/v2/pipelines/'+id+'/run',{method:'POST'});pipelineDetail(id)}catch(e){notice(e.message,true)}};
 if(p.status==='RUNNING'||p.status==='WAITING_APPROVAL'){const pauseBtn=$('#pPause');if(pauseBtn)pauseBtn.onclick=async()=>{try{await api('/api/v2/pipelines/'+id+'/pause',{method:'POST'});pipelineDetail(id)}catch(e){notice(e.message,true)}};}
 if(p.status==='PAUSED')$('#pResume').onclick=async()=>{try{await api('/api/v2/pipelines/'+id+'/resume',{method:'POST'});pipelineDetail(id)}catch(e){notice(e.message,true)}};
 if(p.status==='RUNNING'||p.status==='PAUSED'||p.status==='WAITING_APPROVAL'){const cancelBtn=$('#pCancel');if(cancelBtn)cancelBtn.onclick=async()=>{
  if(!confirm('Stop this pipeline? Steps that have not started will be cancelled; a step already running will finish but no longer affects the outcome.'))return;
  try{await api('/api/v2/pipelines/'+id+'/cancel',{method:'POST'});pipelineDetail(id)}catch(e){notice(e.message,true)}};};
 document.querySelectorAll('[data-step]').forEach(el=>{
  const step=p.steps.find(s=>s.id===el.dataset.step);
  if(step&&(step.status==='COMPLETED'||step.status==='RUNNING'||step.status==='FAILED'||step.status==='WAITING_APPROVAL'))
   el.onclick=()=>openStepOutput(id,el.dataset.step,step)});
 if(p.status==='RUNNING'||p.status==='PAUSED'||p.status==='WAITING_APPROVAL'){clearInterval(refreshTimer);refreshTimer=setInterval(()=>pipelineDetail(id),5000)}}
function parseStepError(error,kind,agentId){
 const raw=error||'Unknown failure';
 const low=raw.toLowerCase();
 let title,desc,suggestions;
 if(low.includes('branch')&&(low.includes('not found')||low.includes('does not exist'))){
  title='Branch Not Found';
  desc='The repository was reached but the specified branch does not exist in the remote.';
  suggestions=['Verify the branch name in the ACE Repository step configuration','Confirm the branch exists in the remote repository','Try "main" or "master" if the default branch name differs'];
 }else if(low.includes('repocloneerror')||low.includes('git_command_failed')||low.includes('clone')){
  title='Repository Clone Failed';
  desc='The clone operation could not complete. This is typically caused by an incorrect URL, missing branch, or authentication problem.';
  suggestions=['Confirm the repository URL is correct and accessible','Verify your GitHub token is valid and has repo scope','Check that the configured branch exists in the repository'];
 }else if(low.includes('authentication')||low.includes('bad credentials')||low.includes('401')||low.includes('403')){
  title='Authentication Failed';
  desc='The request was rejected due to invalid or missing credentials.';
  suggestions=['Update your GitHub personal access token in GitHub connection settings','Ensure the token has the repo scope enabled','Check that the token has not expired'];
 }else if(low.includes('timeout')||low.includes('timed out')||low.includes('connection refused')||low.includes('network')){
  title='Connection Timed Out';
  desc='The operation could not complete because the remote host could not be reached in time.';
  suggestions=['Check network connectivity to the GitHub host','Verify the repository URL is reachable from this environment','Retry the step — transient network errors often self-resolve'];
 }else if(low.includes('not found')||low.includes('404')){
  title='Resource Not Found';
  desc='The requested resource could not be found on the remote.';
  suggestions=['Verify the repository URL is correct','Check that the repository exists and is accessible with your credentials'];
 }else if(low.includes('permission denied')||low.includes('access denied')||low.includes('forbidden')){
  title='Permission Denied';
  desc='Your account does not have sufficient permissions to perform this operation.';
  suggestions=['Ensure your GitHub token has the required scopes (repo)','Check repository access permissions for your account'];
 }else{
  title=kind==='CLONE'?'Repository Operation Failed':agentId?`${agentId.split('-').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ')} Failed`:'Step Failed';
  desc='An unexpected error occurred. Review the technical details below or check the Logs tab for more context.';
  suggestions=['Check the Logs tab for additional context','Retry the step to attempt recovery','Contact support if the issue persists'];
 }
 return{title,desc,suggestions,technical:raw}}
function renderStepError(detail){
 const{title,desc,suggestions,technical}=parseStepError(detail.error,detail.kind,detail.agent_id);
 const chevron=`<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>`;
 const errIcon=`<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
 return`<div class="err-summary">
  <div class="err-summary-top">
   <div class="err-icon">${errIcon}</div>
   <div><div class="err-title">${esc(title)}</div><div class="err-desc">${esc(desc)}</div></div>
  </div>
  ${suggestions.length?`<div class="err-actions"><div class="err-actions-label">Suggested actions</div><ul>${suggestions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></div>`:''}
  <div class="err-retry"><button id="opRetry" class="btn-sm primary">↺ Retry step</button></div>
 </div>
 <div class="err-technical">
  <button class="err-tech-toggle" id="errTechToggle">${chevron} Technical details</button>
  <div class="err-tech-body">${esc(technical)}</div>
 </div>`}
function renderArtefact(art){
 if(!art)return '';
 const chev=`<svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>`;
 let html=`<div class="artefact-title">${esc(art.title||'Agent Output')}</div>`;
 // Markdown content (framework-baseline, ace-analysis)
 if(art.markdown){
  html+=`<div class="artefact-md"><pre class="artefact-pre">${esc(art.markdown)}</pre></div>`}
 // YAML content (migration-specification)
 if(art.yaml){
  const vBadge=art.validation_status==='VALID'?'<span class="badge badge-success" style="margin-left:8px"><span class="badge-dot"></span>VALID</span>':'<span class="badge badge-error" style="margin-left:8px"><span class="badge-dot"></span>INVALID</span>';
  html+=`<div class="artefact-yaml-header">Migration Specification ${vBadge}</div><div class="artefact-yaml"><pre class="artefact-pre">${esc(art.yaml)}</pre></div>`;
  if(art.validation_errors&&art.validation_errors.length){html+=`<div class="artefact-errors"><div class="artefact-sub">Validation Errors</div><ul>${art.validation_errors.map(e=>`<li>${esc(e.path||'')} — ${esc(e.message||JSON.stringify(e))}</li>`).join('')}</ul></div>`}}
 // Plan: task table, mappings table, risk register
 if(art.format==='plan'){
  if(art.tasks&&art.tasks.length){
   html+=`<div class="artefact-sub">Implementation Tasks (${art.tasks.length})</div><div class="table-wrap"><table><thead><tr><th>ID</th><th>Source</th><th>Spring Pattern</th><th>Module</th><th>Boundary</th><th>Tests</th></tr></thead><tbody>${art.tasks.map(t=>`<tr><td><code>${esc(t.id)}</code></td><td style="font-size:11px">${esc(t.source)}</td><td><code>${esc(t.pattern)}</code></td><td>${esc(t.module)}</td><td><span class="badge badge-${t.boundary==='GENERATED'?'info':'warning'}">${esc(t.boundary)}</span></td><td style="font-size:11px">${(t.tests||[]).map(x=>esc(x)).join(', ')}</td></tr>`).join('')}</tbody></table></div>`}
  if(art.mappings&&art.mappings.length){
   html+=`<details class="op-raw-section"><summary>${chev} Construct Mappings (${art.mappings.length})</summary><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>Source</th><th>ACE Construct</th><th>Spring Pattern</th><th>Module</th></tr></thead><tbody>${art.mappings.map(m=>`<tr><td style="font-size:11px">${esc(m.source)}</td><td>${esc(m.construct)}</td><td><code>${esc(m.pattern)}</code></td><td>${esc(m.module)}</td></tr>`).join('')}</tbody></table></div></details>`}
  if(art.risk_register&&art.risk_register.length){
   html+=`<div class="artefact-sub">Risk Register (${art.risk_register.length})</div><div class="table-wrap"><table><thead><tr><th>Severity</th><th>Category</th><th>Statement</th><th>Owner</th><th>Status</th></tr></thead><tbody>${art.risk_register.map(r=>`<tr><td><span class="badge badge-${r.severity==='HIGH'?'error':r.severity==='MEDIUM'?'warning':'success'}">${esc(r.severity)}</span></td><td>${esc(r.category)}</td><td style="font-size:11px">${esc(r.statement)}</td><td>${esc(r.owner)}</td><td>${esc(r.status)}</td></tr>`).join('')}</tbody></table></div>`}}
 // Code generation: file list with content
 if(art.format==='code'&&art.files&&art.files.length){
  html+=`<div class="artefact-sub">Generated Files (${art.files.length})</div>`;
  art.files.forEach(f=>{html+=`<details class="op-raw-section"><summary>${chev} ${esc(f.path)}</summary><pre class="artefact-pre artefact-code" style="margin-top:6px">${esc(f.content)}</pre></details>`});
  if(art.quality){
   const qs=art.quality.status;const qcls=qs==='PASS'?'success':qs==='FAIL'?'error':'warning';
   html+=`<div class="artefact-sub">Quality Gate: <span class="badge badge-${qcls}">${esc(qs)}</span></div>`;
   if(art.quality.checks){html+=`<div class="table-wrap"><table><thead><tr><th>Check</th><th>Status</th></tr></thead><tbody>${art.quality.checks.map(c=>`<tr><td>${esc(c.name)}</td><td><span class="badge badge-${c.status==='PASS'?'success':c.status==='FAIL'?'error':'neutral'}">${esc(c.status)}</span></td></tr>`).join('')}</tbody></table></div>`}}}
 // Variances
 if(art.format==='variances'&&art.items&&art.items.length){
  html+=`<div class="table-wrap"><table><thead><tr><th>Path</th><th>Kind</th><th>Severity</th><th>Expected</th><th>Actual</th><th>Disposition</th></tr></thead><tbody>${art.items.map(v=>`<tr><td><code style="font-size:11px">${esc(v.path)}</code></td><td>${esc(v.kind)}</td><td><span class="badge badge-${v.severity==='HIGH'?'error':v.severity==='MEDIUM'?'warning':'success'}">${esc(v.severity)}</span></td><td style="font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis">${esc(v.expected)}</td><td style="font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis">${esc(v.actual)}</td><td>${esc(v.disposition)}</td></tr>`).join('')}</tbody></table></div>`}
 // RCA
 if(art.format==='rca'&&art.hypotheses&&art.hypotheses.length){
  html+=art.hypotheses.map(h=>`<div class="artefact-rca-item"><div class="artefact-rca-hyp">${esc(h.hypothesis)}</div><div class="artefact-rca-meta">Confidence: <strong>${(h.confidence*100).toFixed(0)}%</strong> · Scope: ${esc(h.scope||'—')} · ${esc(h.status)}</div></div>`).join('')}
 // Remediation
 if(art.format==='remediation'&&art.proposals&&art.proposals.length){
  html+=art.proposals.map(p=>`<div class="artefact-rem-item"><div class="artefact-sub">${esc(p.pattern||'Proposal')} <span class="badge badge-neutral">${esc(p.status)}</span></div><div style="font-size:12px;color:var(--text-2);margin:4px 0">${esc(typeof p.proposal==='string'?p.proposal:JSON.stringify(p.proposal))}</div>${p.tests&&p.tests.length?`<div style="font-size:11px;color:var(--text-3)">Regression tests: ${p.tests.map(t=>esc(t)).join(', ')}</div>`:''}</div>`).join('')}
 // Governance
 if(art.format==='governance'&&art.decision){
  html+=`<div class="artefact-sub">Gate Decisions</div><div class="table-wrap"><table><thead><tr><th>Target State</th><th>Decision</th></tr></thead><tbody>${Object.entries(art.decision).map(([k,v])=>`<tr><td>${esc(k)}</td><td><span class="badge badge-${v==='ALLOW'?'success':'error'}">${esc(v)}</span></td></tr>`).join('')}</tbody></table></div>`}
 // Knowledge
 if(art.format==='knowledge'&&art.cited_context&&art.cited_context.length){
  html+=`<div class="artefact-sub">Citations (${art.cited_context.length})</div><div class="table-wrap"><table><thead><tr><th>Kind</th><th>Reference</th><th>Version</th><th>Score</th></tr></thead><tbody>${art.cited_context.map(c=>`<tr><td>${esc(c.kind)}</td><td style="font-size:11px">${esc(c.reference)}</td><td>${esc(c.version)}</td><td>${c.score}</td></tr>`).join('')}</tbody></table></div>`}
 // Report-style sections (intake, dormancy)
 if(art.sections){
  art.sections.forEach(s=>{
   html+=`<div class="artefact-sub">${esc(s.heading)}</div>`;
   if(s.content)html+=`<div style="font-size:13px;color:var(--text);margin:4px 0 12px">${esc(s.content)}</div>`;
   if(s.items&&s.items.length)html+=`<ul class="artefact-list">${s.items.map(i=>`<li>${esc(typeof i==='string'?i:JSON.stringify(i))}</li>`).join('')}</ul>`;
   if(s.table)html+=`<div class="table-wrap"><table><thead><tr>${Object.keys(s.table[0]||{}).map(k=>`<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${s.table.map(r=>`<tr>${Object.values(r).map(v=>`<td>${esc(String(v))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
   if(s.json)html+=`<div class="op-json" style="margin:4px 0 12px">${syntaxHighlightJson(s.json)}</div>`})}
 return html}
function renderOutputPane(outputs,artefact){
 const chev=`<svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>`;
 // Formatted artefact content takes priority when available.
 const artHtml=renderArtefact(artefact);
 if(!Object.keys(outputs).length&&!artHtml)return'<p class="authhint">No output recorded for this step.</p>';
 const METRIC_KEYS={file_count:'Files',flow_count:'Flows',gap_count:'Gaps',findings_count:'Findings',lines_generated:'Lines Generated',test_count:'Test Cases',knowledge_items:'Knowledge Items',evidence_count:'Evidence Items',dormant_flows:'Dormant Flows',specification_count:'Specifications',plan_count:'Plans',class_count:'Classes',method_count:'Methods'};
 const metrics=Object.entries(METRIC_KEYS).filter(([k])=>typeof outputs[k]==='number').map(([k,label])=>({label,value:outputs[k]}));
 const metricsHtml=metrics.length?`<div class="op-metrics">${metrics.map(m=>`<div class="op-metric-card"><div class="op-metric-val">${m.value}</div><div class="op-metric-label">${esc(m.label)}</div></div>`).join('')}</div>`:'';
 const findings=Array.isArray(outputs.findings)?outputs.findings:Array.isArray(outputs.gaps)?outputs.gaps:Array.isArray(outputs.issues)?outputs.issues:[];
 let findingsHtml='';
 if(findings.length){
  findingsHtml=`<div class="op-findings"><div class="op-findings-title">Detected Findings</div><ul class="op-findings-list">${findings.map(f=>`<li>${esc(typeof f==='string'?f:(f.message||f.description||JSON.stringify(f)))}</li>`).join('')}</ul></div>`;
 }else if(typeof outputs.flow_count==='number'&&outputs.flow_count===0){
  const noteIco=`<svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
  findingsHtml=`<div class="op-finding-note">${noteIco} No Message Flows Detected</div>`;
 }
 const statusMsg=outputs.message||outputs.status_message||outputs.summary||'';
 const msgHtml=statusMsg?`<div class="op-output-message">${esc(statusMsg)}</div>`:'';
 return`${metricsHtml}${artHtml}${findingsHtml}${msgHtml}<details class="op-raw-section"><summary>${chev} Raw Output</summary><div class="op-json" style="margin-top:8px">${syntaxHighlightJson(outputs)}</div></details>`}
async function openStepOutput(pipelineId,stepId,step){
 let detail=step;
 try{detail=await api(`/api/v2/pipelines/${pipelineId}/steps/${stepId}/output`)}catch(e){}
 const panel=$('#outputPanel'),backdrop=$('#panelBackdrop');
 const statusColors={COMPLETED:'#12b76a',RUNNING:'#3157e5',FAILED:'#d92d20',PENDING:'#98a2b3',QUEUED:'#98a2b3',WAITING_APPROVAL:'#f79009'};
 const sc=statusColors[detail.status]||'#98a2b3';
 const outputs=typeof detail.outputs==='object'&&detail.outputs?detail.outputs:{};
 const params=stepParams(detail);
 const metaRows=[
  ['Kind',STEP_KIND_LABEL[detail.kind]||detail.kind],
  detail.agent_id?['Agent ID',detail.agent_id]:null,
  detail.started_at?['Started',fmtDt(detail.started_at)]:null,
  detail.completed_at?['Finished',fmtDt(detail.completed_at)]:null,
  detail.started_at&&detail.completed_at?['Duration',((new Date(detail.completed_at)-new Date(detail.started_at))/1000).toFixed(1)+'s']:null,
  detail.error?['Error',detail.error]:null,
  ...Object.entries(params).slice(0,6).map(([k,v])=>[k,typeof v==='object'?JSON.stringify(v):String(v??'')]),
  detail.model_alias?['Model Alias',detail.model_alias]:null,
  detail.binding_source?['Binding Source',detail.binding_source]:null,
  detail.model_mode?['Model Mode',detail.model_mode]:null,
  detail.model_reason?['Model Reason',detail.model_reason]:null,
 ].filter(Boolean);
 const logs=Array.isArray(detail.logs)?detail.logs:[];
 const logsHtml=logs.length
  ?logs.map(([t,lvl,msg])=>`<div class="log-line"><span class="log-time">${esc(t)}</span><span class="log-${esc(lvl)}">[${esc(lvl)}]</span> ${esc(msg)}</div>`).join('')
  :'<p class="authhint">No logs available for this step.</p>';
 const _dur=detail.started_at&&detail.completed_at?((new Date(detail.completed_at)-new Date(detail.started_at))/1000).toFixed(1)+'s':'';
 panel.innerHTML=`
  <div class="op-header">
   <div class="op-title-row">
    <div class="op-icon" style="background:${sc}18;color:${sc}">${stepIcon(detail)}</div>
    <div class="op-title">${esc(stepLabel(detail))}</div>
    <button class="op-close" id="opClose">✕</button>
   </div>
   <span class="op-status-chip ${esc(detail.status)}">${esc(STEP_STATUS_LABEL[detail.status]||detail.status)}</span>
   ${(detail.started_at||detail.completed_at||_dur)?`<div class="op-exec-meta">
    ${detail.started_at?`<div class="op-exec-item"><span class="op-exec-label">Started</span><span class="op-exec-val">${esc(fmtDt(detail.started_at))}</span></div>`:''}
    ${detail.completed_at?`<div class="op-exec-item"><span class="op-exec-label">Finished</span><span class="op-exec-val">${esc(fmtDt(detail.completed_at))}</span></div>`:''}
    ${_dur?`<div class="op-exec-item"><span class="op-exec-label">Duration</span><span class="op-exec-val">${esc(_dur)}</span></div>`:''}
   </div>`:''}
  </div>
  <div class="op-tabs">
   <button class="op-tab active" data-tab="output">Output</button>
   <button class="op-tab" data-tab="logs">Logs</button>
   <button class="op-tab" data-tab="meta">Metadata</button>
   ${detail.status==='FAILED'?`<button class="op-tab" data-tab="error">Error</button>`:''}
  </div>
  <div class="op-body">
   <div class="op-pane active" id="pane-output">
    ${renderOutputPane(outputs,detail.artefact)}
   </div>
   <div class="op-pane" id="pane-logs">${logsHtml}</div>
   <div class="op-pane" id="pane-meta">
    ${metaRows.map(([k,v])=>`<div class="meta-row"><div class="meta-key">${esc(k)}</div><div class="meta-val">${esc(String(v))}</div></div>`).join('')}
   </div>
   ${detail.status==='FAILED'?`<div class="op-pane" id="pane-error">${renderStepError(detail)}</div>`:''}
  </div>`;
 panel.classList.add('open');backdrop.classList.add('open');
 const close=()=>{panel.classList.remove('open');backdrop.classList.remove('open')};
 $('#opClose').onclick=close;backdrop.onclick=close;
 panel.querySelector('#errTechToggle')?.addEventListener('click',function(){this.classList.toggle('open')});
 panel.querySelectorAll('.op-tab').forEach(tab=>{tab.onclick=()=>{
  panel.querySelectorAll('.op-tab').forEach(t=>t.classList.remove('active'));
  panel.querySelectorAll('.op-pane').forEach(p=>p.classList.remove('active'));
  tab.classList.add('active');
  panel.querySelector('#pane-'+tab.dataset.tab)?.classList.add('active')}});
 const retryBtn=panel.querySelector('#opRetry');
 if(retryBtn)retryBtn.onclick=async()=>{
  try{await api(`/api/v2/pipelines/${pipelineId}/steps/${stepId}/retry`,{method:'POST'});close();pipelineDetail(pipelineId)}catch(e){notice(e.message,true)}}}

async function render(){if($('#overrideConfigDialog').open)$('#overrideConfigDialog').close();if($('#agentDialog').open)$('#agentDialog').close();clearInterval(refreshTimer);if(!token()||!session)return authScreen('login');renderIdentity();
 const p=(location.hash||'#dashboard').slice(1);navSetActive(p);
 // The wizard and pipeline detail views manage their own multi-step/live-poll
 // state and do not want the dashboard metrics bar refetched under them on
 // every internal step change, so they are handled before the generic path.
 if(p==='new-migration')return migrationWizard();
 if(p==='pipelines'){try{notice('');$('#title').textContent='Pipelines';await pipelineList()}catch(e){return renderError(e)}return}
 if(p.startsWith('pipelines/')){try{notice('');await pipelineDetail(p.slice('pipelines/'.length))}catch(e){return renderError(e)}return}
 try{notice('');$('#title').textContent=p.split('-').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' ');
 const [d,agentHealth]=await Promise.all([api('/api/v2/dashboard'),api('/api/v2/agent-registry/health').catch(()=>({items:[]}))]);
 const _ah=agentHealth.items||agentHealth.agents||[];
 const agentsRunning=_ah.filter(a=>a.status==='RUNNING').length;
 const totalAgents=_ah.length||14;
 const completedJobs=(d.jobs_by_state||[]).find(x=>x.state==='COMPLETED')?.count??0;
 const kpiData=[
  {label:'Active Migrations',value:d.jobs,color:'#2558C4',iconBg:'#DDE9F7',icon:'<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/>',trend:'',trendClass:'trend-neutral'},
  {label:'Pending Approvals',value:d.approvals,color:'#F59E0B',iconBg:'#FFFBEB',icon:'<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',trend:d.approvals>0?'Requires attention':'All clear',trendClass:d.approvals>0?'trend-warn':'trend-neutral'},
  {label:'Agents Running',value:agentsRunning,color:'#22C55E',iconBg:'#F0FDF4',icon:'<rect x="2" y="8" width="20" height="12" rx="2"/><path d="M8 8V5a4 4 0 018 0v3"/><circle cx="12" cy="14" r="2"/>',trend:`of ${totalAgents} total agents`,trendClass:'trend-neutral'},
  {label:'Evidence Items',value:d.evidence,color:'#8B5CF6',iconBg:'#F5F3FF',icon:'<path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/>',trend:'',trendClass:'trend-neutral'},
  {label:'Completed (QTR)',value:completedJobs,color:'#22C55E',iconBg:'#F0FDF4',icon:'<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',trend:'',trendClass:'trend-neutral'},
 ];
 const TREND_UP=`<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`;
 const TREND_DOWN=`<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>`;
 const TREND_WARN=`<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
 $('#metrics').innerHTML=kpiData.map(k=>`<div class="kpi-card" style="--kpi-color:${k.color};--kpi-icon-bg:${k.iconBg}"><div class="kpi-label">${esc(k.label)}<div class="kpi-icon"><svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24">${k.icon}</svg></div></div><div class="kpi-value">${k.value??0}</div>${k.trend?`<div class="kpi-trend ${k.trendClass}">${k.trendClass==='trend-up'?TREND_UP:k.trendClass==='trend-down'?TREND_DOWN:k.trendClass==='trend-warn'?TREND_WARN:''}${esc(k.trend)}</div>`:''}</div>`).join('');
 if(p==='jobs'){
  const jd=await api('/api/v2/jobs');const jobs=jd.items||[];
  const jobRow=j=>{const _pct=dispPct(j.state||j.status,j.progress);const _cls=progressFillClass(j.state||j.status,j.progress||0);console.log('[progress]',j.service||j.id,'state:',j.state||j.status,'pct:',_pct+'%','css-class:',_cls.trim()||'(base)');return`<tr>
   <td style="font-family:ui-monospace,monospace;font-size:12px;color:var(--text-2);white-space:nowrap">${esc(String(j.id||'').slice(0,14))}</td>
   <td class="td-name">${esc(j.service||j.workspace_dir||j.id||'')}</td>
   <td class="td-light">${esc(j.owner||j.created_by||'—')}</td>
   <td>${stateBadge(j.state||j.status||'CREATED')}</td>
   <td style="min-width:130px"><div style="display:flex;align-items:center;gap:8px">
    <div class="progress-bar" style="flex:1"><div class="progress-fill${_cls}" style="width:${_pct}%"></div></div>
    <span style="font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums;min-width:28px">${_pct}%</span>
   </div></td>
   <td>${riskBadge(j.risk)}</td>
   <td><button class="btn-ghost btn-view-job" data-job-id="${esc(j.id)}" style="font-size:12px">View</button></td>
  </tr>`};
  const rows=jobs.map(jobRow).join('');
  $('#view').innerHTML=`<div class="page-header page-header-row"><div><div class="page-title">Migration Jobs</div><div class="page-sub">All migration jobs and their full lifecycle status</div></div><button class="btn-primary btn-new-mig"><svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> New Migration</button></div>
   <div class="card"><div class="card-head"><div><div class="card-title">All Jobs (${jobs.length})</div></div><div style="display:flex;align-items:center;flex-shrink:0">
    <button class="btn-secondary" style="font-size:12px;white-space:nowrap">Filter</button>
</div></div>
   <div class="card-body"><div class="table-wrap"><table><thead><tr><th>Job ID</th><th>Service</th><th>Owner</th><th>State</th><th>Progress</th><th>Risk</th><th>Actions</th></tr></thead>
   <tbody>${rows||'<tr><td colspan="7" class="empty">No jobs yet</td></tr>'}</tbody></table></div></div></div>`;
  $('#view').querySelector('.btn-new-mig')?.addEventListener('click',()=>location.hash='new-migration');
  $('#view').addEventListener('click',e=>{const b=e.target.closest('.btn-view-job');if(b)location.hash='jobs/'+b.dataset.jobId});
  requestAnimationFrame(()=>{document.querySelectorAll('.progress-bar').forEach(bar=>{const fill=bar.querySelector('.progress-fill');if(!fill)return;const bw=bar.getBoundingClientRect().width;const fw=fill.getBoundingClientRect().width;console.log('[progress-computed] bar-width:'+bw.toFixed(1)+'px fill-width:'+fw.toFixed(1)+'px applied-style:'+fill.style.width+' class:'+fill.className)})});
  refreshTimer=setInterval(async()=>{const r=await api('/api/v2/jobs');const jobs2=r.items||[];const tb=document.querySelector('#view tbody');if(tb)tb.innerHTML=jobs2.map(jobRow).join('')||'<tr><td colspan="7" class="empty">No jobs yet</td></tr>'},10000);
  return}
 if(p==='approval-governance'){await approvalGovernance();refreshTimer=setInterval(approvalGovernance,5000);return}
 if(p==='approval-inbox'){await approvalInbox();refreshTimer=setInterval(approvalInbox,5000);return}
 if(p==='agent-registry')return agentRegistryPage();
 if(p==='agent-templates')return agentTemplatesPage();
 if(p==='model-registry')return modelRegistryPage();
 if(p==='skill-registry')return skillRegistryPage();
 if(p==='dashboard'){
  const [jobs,approvals,pipelines]=await Promise.all([api('/api/v2/jobs'),api('/api/v7/approval-inbox').catch(()=>({items:[]})),api('/api/v2/pipelines').catch(()=>({items:[]}))]);

  const runPl=(pipelines.items||[]).find(x=>x.status==='RUNNING');
  let plSteps=[];if(runPl){try{const pl=await api('/api/v2/pipelines/'+runPl.id);plSteps=pl.steps||[]}catch(_){}}
  const jobRows=(jobs.items||[]).slice(0,12).map(x=>`<tr><td class="td-name">${esc(x.service||x.workspace_dir||x.id)}${x.id?`<div class="td-svc-id">${esc(String(x.id).slice(0,14))}…</div>`:''}</td><td class="td-light">${esc(x.owner||x.created_by||'—')}</td><td>${stateBadge(x.state||x.status||'CREATED')}</td><td style="min-width:160px"><div style="display:flex;align-items:center;gap:8px"><div class="progress-bar" style="flex:1"><div class="progress-fill${progressFillClass(x.state||x.status,x.progress||0)}" style="width:${dispPct(x.state||x.status,x.progress)}%"></div></div><span style="font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums;min-width:28px">${dispPct(x.state||x.status,x.progress)}%</span></div></td><td>${riskBadge(x.risk)}</td></tr>`).join('');
  const pendingCount=(approvals.items||[]).length;
  const APPR_LIMIT=5;
  const appRows=(approvals.items||[]).slice(0,APPR_LIMIT).map(x=>`<div class="approval-item"><div class="appr-row-top">${x.gate?`<span class="gate gate-pending">${esc(x.gate)}</span>`:''}<div class="appr-meta"><div class="appr-service">${esc(x.service||x.pipeline_id||'—')}</div><div class="appr-waiting"><span class="appr-waiting-label">Awaiting review by</span><span class="appr-role-chip">${esc(x.role||'Reviewer')}</span></div>${x.description?`<div class="appr-desc">${esc(x.description)}</div>`:''}</div>${x.created_at?`<span class="appr-time">${fmtTime(x.created_at)}</span>`:''}</div><div class="appr-actions"><button class="btn-approve" data-approval-id="${esc(x.id||'')}">Approve</button><button class="btn-reject">Review</button></div></div>`).join('');
  const appOverflow=pendingCount>APPR_LIMIT?`<div style="padding:10px 20px 14px"><button id="dashViewAllApprovals2" class="btn-ghost" style="width:100%;justify-content:center;font-size:12px;color:var(--text-2)">+${pendingCount-APPR_LIMIT} more · View all →</button></div>`:'';
  const totalJobs=d.jobs||0;const pendingByGate={};(approvals.items||[]).forEach(x=>{if(x.gate)pendingByGate[x.gate]=(pendingByGate[x.gate]||0)+1});
  const stepCards=plSteps.slice(0,12).map(s=>{const st=s.status||'PENDING';const dot=`<div class="stepdot"></div>`;const time=s.finished_at?'Finished '+fmtTime(s.finished_at):s.started_at?'Running…':'Waiting';return `<div class="pipeline-step ${esc(st)}">${dot}<div class="stepmeta"><b>${esc(s.name||s.agent_id||'')}</b><small>${time}</small></div><span class="stepstatus">${esc(st)}</span></div>`}).join('');
  const pipelineTitle=runPl?(runPl.workspace_dir||runPl.id||'Pipeline'):'Recent Pipeline';
  const pipelineBadge=runPl?`<span class="badge badge-running"><span class="badge-dot"></span>RUNNING</span>`:`<span class="badge badge-neutral">NO ACTIVE PIPELINE</span>`;
  const stepViewerCard=plSteps.length?`<div class="card"><div class="card-head"><div><div class="card-title">${esc(pipelineTitle)} Pipeline</div><div class="card-sub">Live step execution · ${plSteps.length} steps</div></div>${pipelineBadge}</div><div class="pipeline-board" style="padding:14px 16px">${stepCards}</div></div>`:runPl?`<div class="card"><div class="card-head"><div class="card-title">${esc(pipelineTitle)} Pipeline</div></div><div class="empty">Loading pipeline steps…</div></div>`:'';
  const LC_STAGES=[{key:'CREATED',label:'Created',states:['CREATED']},{key:'INTAKE',label:'Intake',states:['INTAKE_VALIDATED']},{key:'DORMANCY',label:'Dormancy',states:['DORMANCY_ASSESSED','FRAMEWORK_BASELINED']},{key:'ANALYSIS',label:'Analysis',states:['ACE_ANALYSED']},{key:'PLANNING',label:'Planning',states:['PLAN_APPROVED']},{key:'TESTING',label:'Testing',states:['IMPLEMENTING','BUILD_VALIDATED','DIFFERENTIAL_TESTING','VARIANCE_REVIEW','REMEDIATING']},{key:'PROMOTE',label:'Promotion',states:['READY_FOR_PROMOTION']},{key:'COMPLETE',label:'Complete',states:['COMPLETED']}];
  const jobList=jobs.items||[];
  const stageCounts=LC_STAGES.map(st=>({...st,count:jobList.filter(j=>st.states.includes(String(j.state||j.status||'').toUpperCase())).length}));
  const stateToMilestoneIdx=s=>{const u=String(s||'').toUpperCase();return LC_STAGES.findIndex(st=>st.states.includes(u))};
  const maxStageIdx=jobList.length?Math.max(...jobList.map(j=>stateToMilestoneIdx(j.state||j.status))):-1;
  const checkSvg=`<svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`;
  const lcSteps=LC_STAGES.map((st,i)=>{const cnt=stageCounts[i].count;const isDone=i<maxStageIdx;const isActive=!isDone&&i===maxStageIdx&&cnt>0;const hasJobs=!isDone&&!isActive&&cnt>0;const cls=isDone?'lc-done':isActive?'lc-active':hasJobs?'lc-has-jobs':'';const inner=isDone?checkSvg:'';const countChip=cnt>0&&!isDone?`<div class="lc-count">${cnt>99?'99+':cnt}</div>`:'';return `<div class="lc-step ${cls}"><div class="lc-dot">${inner}</div><div class="lc-label">${esc(st.label)}</div>${countChip}</div>`}).join('');
  const lcTotalStages=LC_STAGES.length-1;const lcProgressPct=maxStageIdx<0?0:Math.round((maxStageIdx/lcTotalStages)*100);const lcGreenRatio=maxStageIdx<=0?0:Math.max(0,maxStageIdx-1)/lcTotalStages;const lcBlueRatio=maxStageIdx<=0?0:maxStageIdx/lcTotalStages;const lcGreenWidth=lcGreenRatio<=0?'0px':`calc(${(lcGreenRatio*100).toFixed(2)}% - ${(lcGreenRatio*28).toFixed(2)}px)`;const lcBlueWidth=lcBlueRatio<=0?'0px':`calc(${(lcBlueRatio*100).toFixed(2)}% - ${(lcBlueRatio*28).toFixed(2)}px)`;
  const completedJobs=stageCounts[LC_STAGES.length-1].count;
  const lcSummary=jobList.length?`<span class="lifecycle-summary"><strong>${jobList.length}</strong> jobs &middot; <strong>${completedJobs}</strong> complete</span>`:`<span class="lifecycle-summary">No jobs yet</span>`;
  const inboxCard=pendingCount>0
    ?`<div class="card" style="border-color:rgba(245,158,11,.35)"><div class="card-head" style="background:rgba(245,158,11,.06);border-bottom-color:rgba(245,158,11,.2)"><div style="display:flex;align-items:center;gap:8px"><div class="approval-alert-dot"></div><div><div class="card-title" style="color:var(--warning-text)">${pendingCount} Pending Approval${pendingCount>1?'s':''}</div><div class="card-sub">${pendingCount} action${pendingCount>1?'s':''} required to unblock pipelines</div></div></div><button id="dashViewAllApprovals" class="btn-ghost">All →</button></div>${appRows}${appOverflow}</div>`
    :`<div class="card"><div class="card-head"><div style="display:flex;align-items:center;gap:8px"><div style="width:28px;height:28px;border-radius:50%;background:var(--success-bg);display:flex;align-items:center;justify-content:center;color:var(--success-text);flex-shrink:0"><svg width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg></div><div><div class="card-title" style="color:var(--success-text)">All approvals cleared</div><div class="card-sub">No actions required</div></div></div><button id="dashViewAllApprovals" class="btn-ghost">All →</button></div></div>`;
  $('#view').innerHTML=`<div class="cmd-hero-card"><div class="cmd-hero-left"><svg width="36" height="36" fill="none" stroke="currentColor" stroke-width="1.6" viewBox="0 0 24 24" style="color:#93C5FD;flex-shrink:0"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg><div><div class="cmd-hero-title">Command Center</div><div class="cmd-hero-sub">ACE → Spring Boot migration governance &amp; oversight</div></div></div><div class="cmd-hero-right"><span class="cmd-live-badge"><span class="cmd-live-pulse"></span>LIVE</span><span class="cmd-hero-meta">Auto-refreshes every 30 s &middot; Last updated ${new Date().toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true})} &middot; ${totalJobs} services tracked</span></div></div><div style="display:flex;flex-direction:column;gap:24px"><div class="dash-grid"><div class="dash-stack"><div class="card"><div class="card-head"><div><div class="card-title">Active Migration Jobs</div><div class="card-sub">All in-flight migrations and their current lifecycle state</div></div><button id="dashViewAllJobs" class="btn-ghost">View all →</button></div><div class="card-body"><div class="table-wrap"><table><thead><tr><th>Service</th><th>Owner</th><th>State</th><th>Progress</th><th>Risk</th></tr></thead><tbody>${jobRows||'<tr><td colspan="5" class="empty">No jobs yet</td></tr>'}</tbody></table></div></div></div>${stepViewerCard}</div><div class="dash-stack">${inboxCard}</div></div></div>`;
  document.querySelectorAll('.btn-approve[data-approval-id]').forEach(btn=>{btn.onclick=async()=>{try{await api('/api/v7/approval-packages/'+btn.dataset.approvalId+'/approve',{method:'POST',body:JSON.stringify({})});btn.closest('.approval-item').style.opacity='0.4';notice('Approval submitted — pipeline will resume automatically')}catch(e){notice(e.message,true)}}});
  $('#dashViewAllJobs')?.addEventListener('click',()=>{location.hash='jobs';});
  $('#dashViewAllApprovals')?.addEventListener('click',()=>{location.hash='approval-inbox';});
  $('#dashViewAllApprovals2')?.addEventListener('click',()=>{location.hash='approval-inbox';});
  return}
 if(p==='clarifications'){await clarificationsPage();return}
 if(p==='agent-health'){
  const data=await api('/api/v2/agent-registry/health');
  const agents=data.items||data.agents||[];
  const agSvg=`<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><rect x="2" y="8" width="20" height="12" rx="2"/><path d="M8 8V5a4 4 0 018 0v3"/><circle cx="12" cy="14" r="2"/></svg>`;
  $('#view').innerHTML=`<div class="page-header"><div class="page-title">Agent Health</div><div class="page-sub">Live status of all registered agents</div></div>
    ${agents.length?`<div class="health-grid">${agents.map(a=>{
      const name=esc(a.display_name||a.name||a.agent_id||a.id||'Agent');
      const id=esc(a.agent_id||a.id||'');
      const st=a.status||'IDLE';
      const lr=a.last_run_at||a.last_run||'—';
      const tr=a.total_runs??a.run_count??'—';
      const sr=a.success_rate??a.success_pct??null;
      const bc={RUNNING:'badge-info',ERROR:'badge-error',FAILED:'badge-error',ACTIVE:'badge-success',IDLE:'badge-neutral'}[st]||'badge-neutral';
      const fc=sr>=90?'var(--success)':sr>=75?'var(--warning)':'var(--error)';
      return `<div class="health-card ${esc(st)}">
        <div class="health-head">
          <div style="display:flex;gap:10px;align-items:flex-start">
            <div class="health-icon">${agSvg}</div>
            <div><div class="health-name">${name}</div><div class="health-id">${id}</div></div>
          </div>
          <span class="badge ${bc}">${esc(st)}</span>
        </div>
        <div class="health-stats">
          <div><div class="health-stat-label">Last Run</div><div class="health-stat-val" style="font-size:12px">${esc(fmtDt(lr))}</div></div>
          <div><div class="health-stat-label">Total Runs</div><div class="health-stat-val">${esc(String(tr))}</div></div>
        </div>
        ${sr!==null?`<div style="margin-top:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span class="health-stat-label">Success Rate</span>
            <span class="health-stat-val" style="font-size:12px;color:${fc}">${sr}%</span>
          </div>
          <div class="success-rate-bar"><div class="success-rate-fill" style="width:${sr}%;background:${fc}"></div></div>
        </div>`:''}
      </div>`}).join('')}</div>`:'<div class="empty">No agent health data available</div>'}`;
  return}
 const endpoint='/api/v2/'+p;const data=await api(endpoint);$('#view').innerHTML=`<div class="card"><div class="sectionhead"><h2>${esc(p.split('-').map(w=>w.charAt(0).toUpperCase()+w.slice(1)).join(' '))}</h2><span class="live-dot">LIVE</span></div>${table(data.items||data.agents||[])}</div>`}catch(e){renderError(e)}}
function renderError(e){
 // 401 means the token is missing, malformed or expired -- ask for a new one.
 // 403 means it authenticated but lacks a role, so signing in again is useless;
 // show who the caller is instead.
 if(e.reauth){sessionStorage.removeItem('access_token');session=null;renderIdentity();return authScreen('login','Your session expired. Sign in again.',true)}
 if(e.forbidden){const u=session&&session.user;return notice('Not permitted: '+e.message+'. Signed in as '+((u&&u.email)||'unknown')+' with roles '+(((u&&u.roles)||[]).join(', ')||'none'),true)}
 notice(e.message,true)}
$('#new').onclick=()=>{location.hash='new-migration'};
$('#userChipButton').onclick=e=>{e.stopPropagation();const m=$('#userMenu');m.hidden=!m.hidden};
document.addEventListener('click',e=>{const m=$('#userMenu');if(!m.hidden&&!$('#userChip').contains(e.target))m.hidden=true});
$('#menuSignOut').onclick=()=>signOut();
$('#menuGithub').onclick=()=>{$('#userMenu').hidden=true;openGithub(githubState&&githubState.status==='FAILED'?githubState.last_error:'')};
$('#ghIndicator').onclick=()=>openGithub(githubState&&githubState.status==='FAILED'?githubState.last_error:'');
$('#githubCancel').onclick=()=>{$('#githubDialog').close();if(session)render()};
$('#githubForm').onsubmit=async e=>{
 e.preventDefault();const btn=$('#githubSubmit'),err=$('#githubError');const f=Object.fromEntries(new FormData(e.target));
 btn.disabled=true;err.textContent='Connecting to GitHub...';
 try{
  githubState=await api('/api/v2/integrations/github',{method:'POST',body:JSON.stringify({url:f.url,token:f.token})});
  // Remembered for this tab and this account only -- see cachedGithubToken().
  // The server itself never echoes a token back; this is only ever what was
  // just typed here, kept so reopening this dialog later shows it again.
  rememberGithubToken(f.token);
  $('#githubDialog').close();renderIdentity();
  notice('GitHub connected'+(githubState.account_login?' as '+githubState.account_login:''));render()
 }catch(x){
  // A token can pass signature verification yet name an account that no
  // longer exists -- typically a database reset while an old token was still
  // sitting in this tab's sessionStorage. Retyping the URL and token can never
  // fix that (it was previously reported as account_not_found and just kept
  // reopening this same dialog forever), so treat it like any other expired
  // session: close the dialog and return to sign-in.
  if(x.reauth){$('#githubDialog').close();return signOut('Your session is no longer valid. Sign in again.',true)}
  // The credential was still saved server-side even though verification
  // failed, so remember it here too -- reopening the dialog should show what
  // is actually configured, not a blank field.
  rememberGithubToken(f.token);
  // The dialog stays open with the upstream detail so the operator can
  // correct the URL or token and retry, per section 2 of the brief.
  err.textContent=githubErr(x);
  // The credential was stored before verification failed, so re-read the state:
  // otherwise the indicator keeps saying "not connected" when it is really
  // connected-but-failing, and the operator loses the reason.
  try{githubState=await api('/api/v2/integrations/github');renderIdentity()}catch(_){}
 }finally{btn.disabled=false}};
$('#githubTokenToggle').onclick=()=>{
 const f=$('#githubForm').token,show=f.type==='password';
 const eyeOpen='<svg width="15" height="15" fill="none" stroke="#6B7280" stroke-width="1.8" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
 const eyeOff='<svg width="15" height="15" fill="none" stroke="#6B7280" stroke-width="1.8" viewBox="0 0 24 24"><path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
 f.type=show?'text':'password';$('#githubTokenToggle').innerHTML=show?eyeOff:eyeOpen};
$('#githubDisconnect').onclick=async()=>{
 if(!confirm('Remove the stored GitHub connection for your account? You will need to reconnect to read migration sources.'))return;
 try{
  githubState=await api('/api/v2/integrations/github/disconnect',{method:'POST'});
  forgetGithubToken();$('#githubForm').reset();renderIdentity();
  $('#githubDialog').close();notice('GitHub connection removed')
 }catch(x){
  if(x.reauth){$('#githubDialog').close();return signOut('Your session is no longer valid. Sign in again.',true)}
  $('#githubError').textContent=friendly(x.message)}};
// ---- Notification bell: dropdown, badge, polling -----------------------------
let notifPollTimer=null;
function notifTimeAgo(iso){if(!iso)return '';const d=new Date(iso),s=Math.floor((Date.now()-d)/1000);if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})}
async function notifRefresh(){
 if(!token()||!session)return;
 try{
  const r=await api('/api/v7/notifications/unread-count');
  const n=r.count||0;const dot=$('#notifDot'),cnt=$('#notifCount');
  if(n>0){dot.hidden=false;cnt.hidden=false;cnt.textContent=n>99?'99+':n}
  else{dot.hidden=true;cnt.hidden=true}
 }catch(e){}}
function notifRenderItems(items){
 const list=$('#notifList');
 if(!items||!items.length){list.innerHTML='<div class="notif-dd-empty">No notifications yet</div>';return}
 list.innerHTML=items.slice(0,20).map(n=>{
  const unread=!n.is_read;
  const title=esc(n.title||n.event_type||'Notification');
  const time=notifTimeAgo(n.created_at);
  const type=esc((n.event_type||'').replace(/_/g,' '));
  return `<div class="notif-dd-item${unread?' unread':''}" data-nid="${esc(n.id)}" data-link="${esc(n.link||'')}"><div class="notif-dd-item-dot"></div><div class="notif-dd-item-body"><div class="notif-dd-item-title">${title}</div><div class="notif-dd-item-meta"><span class="notif-dd-item-type">${type}</span><span>${time}</span></div></div></div>`}).join('')}
async function notifOpen(){
 const dd=$('#notifDropdown');
 if(!dd.hidden){dd.hidden=true;return}
 dd.hidden=false;
 const list=$('#notifList');list.innerHTML='<div class="notif-dd-empty">Loading…</div>';
 try{
  const r=await api('/api/v7/notifications/mine');
  notifRenderItems(r.items||[]);
  const n=r.unread||0;const dot=$('#notifDot'),cnt=$('#notifCount');
  if(n>0){dot.hidden=false;cnt.hidden=false;cnt.textContent=n>99?'99+':n}
  else{dot.hidden=true;cnt.hidden=true}
  // Click-to-read and navigate
  list.querySelectorAll('.notif-dd-item').forEach(el=>{el.onclick=async()=>{
   const nid=el.dataset.nid,link=el.dataset.link;
   if(el.classList.contains('unread')){
    try{await api('/api/v7/notifications/mark-read',{method:'POST',body:JSON.stringify({id:nid})})}catch(e){}
    el.classList.remove('unread');notifRefresh()}
   if(link){dd.hidden=true;location.hash=link}}})
 }catch(e){list.innerHTML='<div class="notif-dd-empty">Could not load notifications</div>'}}
$('#notifBell').onclick=e=>{e.stopPropagation();notifOpen()};
$('#notifMarkAll').onclick=async e=>{e.stopPropagation();try{await api('/api/v7/notifications/mark-all-read',{method:'POST',body:'{}'});$('#notifDropdown').querySelectorAll('.unread').forEach(el=>el.classList.remove('unread'));notifRefresh();notice('All notifications marked as read')}catch(e){}};
$('#notifViewAll').onclick=()=>{$('#notifDropdown').hidden=true;location.hash='notifications'};
document.addEventListener('click',e=>{const dd=$('#notifDropdown');if(!dd.hidden&&!$('#notifWrap').contains(e.target))dd.hidden=true});
function notifStartPolling(){clearInterval(notifPollTimer);notifRefresh();notifPollTimer=setInterval(notifRefresh,15000)}
function notifStopPolling(){clearInterval(notifPollTimer)}

window.addEventListener('hashchange',()=>{if($('#overrideConfigDialog').open)$('#overrideConfigDialog').close();if($('#agentDialog').open)$('#agentDialog').close();if(session)render()});
document.getElementById('themeToggle')?.addEventListener('click',()=>{
 const curr=document.documentElement.dataset.theme;
 const next=curr==='dark'?'light':'dark';
 document.documentElement.dataset.theme=next;
 try{localStorage.setItem('theme',next)}catch(e){}});
document.getElementById('hamburgerBtn')?.addEventListener('click',()=>{
 document.getElementById('sidebar')?.classList.toggle('open')});
if(token())afterLogin();else authScreen('login');
