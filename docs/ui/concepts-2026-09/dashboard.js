const variant = document.body.dataset.variant || 'aurora';
const variants = {aurora:['01','极光玻璃','AURORA / WORKSPACE'],prism:['02','晴空棱镜','PRISM / PERSONAL SPACE'],ion:['03','离子控制台','ION / MISSION CONTROL']};
const paths = {
 home:'M3 10 12 3l9 7v10H3Z M9 20v-7h6v7',
 database:'M4 6c0-4 16-4 16 0s-16 4-16 0Zm0 0v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0',
 grid:'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z',
 file:'M6 3h8l4 4v14H6Z M14 3v5h4 M9 12h6 M9 16h4',
 rocket:'M13 5c4-3 8-2 8-2s1 4-2 8l-6 6-6-6Z M7 11H3l4-5 5-1 M13 17v4l5-4 1-5 M8 16l-4 4 M5 15l-2 2 M9 19l-2 2',
 check:'m5 12 4 4L19 6',
 plus:'M12 5v14 M5 12h14',
 search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
 arrow:'M5 12h14 m-5-5 5 5-5 5',
 chevron:'m9 5 7 7-7 7',
 down:'m7 10 5 5 5-5',
 clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
 settings:'m9 3-1 3-3 1-2 3 2 2v3l2 3h3l2 3 3-2 3-1v-3l3-2-1-3-3-1-1-3Z M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 chat:'M3 4h18v13H9l-5 4v-4H3Z M7 8h10 M7 12h6',
 code:'m8 7-5 5 5 5 m8-10 5 5-5 5 M14 4l-4 16',
 terminal:'M3 4h18v16H3Z m4 4 4 4-4 4 M13 16h4',
 shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z m-3 9 2 2 4-4',
 spark:'m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z',
 book:'M3 4h7l2 2 2-2h7v16h-7l-2 1-2-1H3Z M12 6v15',
 close:'m6 6 12 12 M6 18 18 6',
 bell:'M6 8a6 6 0 0 1 12 0v7l3 3H3l3-3Z M10 21h4',
 activity:'M2 12h5l3-8 4 16 3-8h5',
 sun:'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10 M12 1v2 M12 21v2 M1 12h2 M21 12h2 M4 4l2 2 M18 18l2 2 M4 20l2-2 M18 6l2-2'
};
const icon=(name,cls='')=>`<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.grid}"/></svg>`;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const toolsList=[
 {name:'数据中心',sub:'6 种数据库',icon:'database',note:'统一进入 Oracle、MySQL、OceanBase、达梦、Redis 与 MongoDB 工作台。',nav:14},
 {name:'模型',sub:'聊天 · 工作',icon:'spark',note:'内网模型连续对话，以及绑定项目目录的 Agent 工作台。',nav:15,color:'#bcacf6'},
 {name:'接口排查',sub:'捕获 · 调试',icon:'activity',note:'多浏览器接口排查与本机请求测试，详细操作继续由原生工作台承载。',nav:12,color:'#8cc7ed'},
 {name:'日志排查',sub:'搜索 · 定位',icon:'terminal',note:'SSH 多机日志关键字截取与本地导出。',nav:13,color:'#edc78d'},
 {name:'加解密',sub:'网关 · 国密',icon:'shield',note:'网关国密解密与 JSON 结果查看。',nav:5,color:'#e5aac5'},
 {name:'格式工具',sub:'JSON · SQL',icon:'code',note:'离线格式化 JSON、XML、SQL 和文本。',nav:11,color:'#aaaaf6'},
 {name:'需求管理',sub:'归档 · 台账',icon:'file',note:'需求归档、上线台账与工具联动。',nav:10},
 {name:'发版联动',sub:'SQL · 发版',icon:'rocket',note:'整理需求、BUG、SQL 与发版 Excel。',nav:2},
 {name:'接口文档更新',sub:'文档 · 更新',icon:'book',note:'SQL 驱动接口文档更新。',nav:3},
 {name:'命令库',sub:'搜索 · 复制',icon:'terminal',note:'Linux 运维命令搜索与复制。',nav:6},
 {name:'证件类型',sub:'测试数据',icon:'file',note:'个人与单位证件模拟生成。',nav:1},
 {name:'车辆 VIN',sub:'测试数据',icon:'code',note:'中国车辆 VIN 测试数据。',nav:4},
 {name:'自我学习',sub:'个人知识',icon:'book',note:'学习资料整理与全文搜索；正式版按既有可见性规则展示。',nav:8}
];
const makeTests=(passed)=>['正常流程与结果核对','必填字段与边界值','异常提示与回退验证','关联功能回归','上线环境验证'].map((title,i)=>({title,passed:i<passed}));
let tasks=[
 {id:'REL-024',title:'客户查询体验优化',system:'客户中心',date:'09.08',done:false,tests:makeTests(5),note:'查询条件与结果反馈已完成验证，等待确认上线完成。'},
 {id:'REL-023',title:'网关接口字段校验',system:'接口服务',date:'09.10',done:false,tests:makeTests(3),note:'正常流程、字段校验与异常处理已通过，继续完成回归与上线环境验证。'},
 {id:'REL-022',title:'数据库升级脚本整理',system:'数据服务',date:'09.12',done:false,tests:makeTests(1),note:'按计划核对升级顺序、回退步骤与关联功能。'},
 {id:'REL-021',title:'异常日志检索优化',system:'运维工具',date:'09.04',done:true,tests:makeTests(5),note:'检索功能验证完成，已上线归档。'},
 ...['需求导出字段优化','日报提醒文案调整','接口文档模板更新','命令库索引优化','数据连接提示优化','JSON 格式化修复','VIN 结果复制优化','学习资料搜索优化'].map((title,i)=>({id:`REL-${String(20-i).padStart(3,'0')}`,title,system:'工具平台',date:`09.${String(1+i%4).padStart(2,'0')}`,done:true,tests:makeTests(5),note:'所有测试点已通过，示例任务已完成上线。'}))
];
let filter='all', showAll=false, dailySaved=false, nextId=25;
const passedCount=t=>t.tests.filter(p=>p.passed).length;
const taskStatus=t=>t.done?'ok':passedCount(t)===t.tests.length?'ready':'run';
const taskProgress=t=>t.done?100:Math.round(25+65*passedCount(t)/t.tests.length);
const statusText={run:'测试中',ready:'待确认',ok:'已完成'};
const navButton=(name,ic,action,count='')=>`<button class="nav-item ${name==='首页'?'active':''}" ${name==='首页'?'aria-current="page"':''} data-action="${action}" title="${name}" aria-label="${name}">${icon(ic)}<span>${name}</span>${count?`<span class="nav-count">${count}</span>`:''}</button>`;
const sidebar=variant==='ion'?`
 <div class="nav-group">${navButton('首页','home','home')}${navButton('数据中心','database','tool:数据中心')}${navButton('需求管理','file','tool:需求管理')}${navButton('发版联动','rocket','tool:发版联动')}${navButton('全部工具','grid','search')}</div>`:`
 <div class="space-switch"><span class="avatar">P</span><span>个人工作空间</span>${icon('down')}</div>
 <div class="nav-group"><div class="nav-label">WORKSPACE</div>${navButton('首页','home','home')}${navButton('数据中心','database','tool:数据中心')}${navButton('模型','spark','tool:模型')}</div>
 <div class="nav-group"><div class="nav-label">交付管理</div>${navButton('需求管理','file','tool:需求管理','08')}${navButton('发版联动','rocket','tool:发版联动')}${navButton('接口文档更新','book','tool:接口文档更新')}${navButton('日报','check','daily')}</div>
 <div class="nav-group"><div class="nav-label">工具与效率</div>${navButton('日志排查','terminal','tool:日志排查')}${navButton('全部工具','grid','search')}</div>
 <div class="sidebar-bottom">${navButton('外观设置','settings','settings')}<div class="local-status"><i class="point"></i>独立设计预览</div></div>`;
const featureCopy={aurora:['把精力，留给创造。','从上次的思路继续，让工作自然发生。'],prism:['让每个想法，轻盈落地。','整理好今天的重点，然后专注一件事。'],ion:['准备就绪。<br>进入工作状态。','需求、数据与工具，回到一个清晰的工作现场。']};
document.getElementById('app').innerHTML=`
<div class="ambient" aria-hidden="true"></div>
<div class="design-bar"><span>PENGTOOLS / DESIGN EXPLORATION</span><nav class="design-links" aria-label="设计方案"><a href="index.html">概览</a>${Object.entries(variants).map(([key,v])=>`<a href="${key}.html" class="${key===variant?'current':''}" ${key===variant?'aria-current="page"':''}>${v[0]} ${v[1]}</a>`).join('')}</nav><span class="prototype">交互原型 · 全部为示例数据</span></div>
<div class="shell">
 <aside class="sidebar" aria-label="应用导航"><div class="brand"><span class="brand-symbol">P</span><div class="brand-copy"><strong>PengTools<span style="font-weight:350">Hub</span></strong><small>YOUR EVERYDAY POWER</small></div></div>${sidebar}</aside>
 <div class="main"><header class="topbar"><div class="breadcrumb">${icon('grid')}<span>工作空间</span><span>/</span><strong>首页</strong></div><button class="search-trigger" data-action="search">${icon('search')}<span>搜索工具、上线任务…</span><kbd>Ctrl K</kbd></button><button class="icon-button" aria-label="外观与动效设置" title="外观与动效设置" data-action="settings">${icon('sun')}</button><span class="avatar" title="示例用户">P</span></header>
 <main class="content"><section class="welcome enter"><div><div class="overline">${variants[variant][2]}</div><h1>${variant==='ion'?'今天，也要创造点什么。':'你好，<span>探索者。</span>'}</h1><p>9 月 7 日，星期一 <span style="padding:0 6px">/</span> 新的一周，从这里开始。</p></div><div class="actions"><button class="secondary" data-action="daily" aria-label="写日报">${icon('file')}<span>写日报</span></button><button class="primary" data-action="create">${icon('plus')}新建任务</button></div></section>
 <div class="dashboard">
 <section class="panel focus enter"><div class="focus-copy"><div class="eyebrow">${icon('spark')} YOUR NEXT MOVE</div><h2>${featureCopy[variant][0]}</h2><p>${featureCopy[variant][1]}</p><button class="primary" data-action="task:REL-024">继续上线任务 ${icon('arrow')}</button></div><div class="orb" aria-hidden="true"><span class="orbit"></span><span class="orbit two"></span><div class="orb-center">${icon('spark')}</div><span class="floating-label">STAY IN FLOW</span></div></section>
 <section class="stats enter" aria-label="工作概览">
 <article class="panel stat"><div class="stat-top">${icon('file')}待办需求</div><strong class="stat-number" id="open-count">08</strong><span class="stat-foot"><em>2 项</em> 等待评审</span><svg class="spark" viewBox="0 0 60 28" aria-hidden="true"><path d="m1 24 10-4 8 2 10-13 9 4L59 2"/></svg></article>
 <article class="panel stat"><div class="stat-top">${icon('check')}本周日报</div><strong class="stat-number" id="daily-count">0 <small>/ 5</small></strong><span class="stat-foot" id="daily-note">记录每一份进展</span></article>
 <article class="panel stat"><div class="stat-top">${icon('rocket')}本月上线</div><strong class="stat-number" id="monthly-count">12 <small>项</small></strong><span class="stat-foot" id="monthly-note"><em>9 项</em> 已完成</span></article>
 <article class="panel stat"><div class="stat-top">${icon('grid')}累计完成</div><strong class="stat-number" id="completed-count">128</strong><span class="stat-foot">让好想法持续落地</span></article>
 </section>
 <section class="panel recent enter"><div class="panel-head"><div class="title-pair"><h2>本月上线任务</h2><small id="recent-count">12</small></div><button class="text-button" data-action="expand" id="expand-tasks">全部 12 项 ${icon('arrow')}</button></div><div class="filters" aria-label="上线任务状态筛选"><button class="filter active" data-filter="all" aria-pressed="true">全部</button><button class="filter" data-filter="run" aria-pressed="false">测试中</button><button class="filter" data-filter="ready" aria-pressed="false">待确认</button><button class="filter" data-filter="ok" aria-pressed="false">已完成</button></div><div id="task-list" aria-live="polite"></div><div class="task-hint">勾选完成 · 点击任务查看与确认测试点</div></section>
 <section class="panel release enter"><div class="panel-head"><h2>上线总览</h2><span class="badge mono">SEP / 2026</span></div><div class="release-overview"><div class="progress-ring" role="img" aria-label="上线完成进度 75%，12 项中已完成 9 项"><div><strong id="release-percent">75<small>%</small></strong><span>RELEASE PROGRESS</span></div></div><div class="release-copy"><strong>每一步，都算数。</strong><p id="release-summary">已完成 9 / 12 项上线任务</p><span class="badge" id="release-pending"><i class="point"></i>3 项正在推进</span></div></div><div class="milestones" id="release-milestones"></div><div class="release-bottom"><span>本月任务概览 · 示例</span><button class="text-button" data-action="release">查看清单 ${icon('arrow')}</button></div></section>
 <section class="panel toolbox enter"><div class="panel-head"><div class="title-pair"><h2>常用工具</h2><small>QUICK ACCESS</small></div><button class="text-button" data-action="search">全部工具 ${icon('arrow')}</button></div><div class="tool-grid">${toolsList.slice(0,6).map(t=>`<button class="tool" data-action="tool:${t.name}" ${t.color?`style="--tool-color:${t.color}"`:''}><span class="tool-icon">${icon(t.icon)}</span><span><strong>${t.name}</strong><small>${t.sub}</small></span></button>`).join('')}</div></section>
 </div><footer class="footer"><span><i class="point"></i>首页工作台 · 交互设计稿 · 所有内容均为示例</span><span class="key-help"><span><kbd>Ctrl K</kbd> 快速查找</span><span>${variants[variant][0]} / ${variants[variant][1]}</span></span></footer></main></div></div>
<dialog id="detail-dialog" aria-labelledby="dialog-title"><div class="dialog-head"><h2 id="dialog-title"></h2><button class="icon-button" data-close aria-label="关闭弹窗">${icon('close')}</button></div><div class="dialog-body" id="dialog-body"></div></dialog>
<dialog id="search-dialog" aria-label="搜索工具与上线任务"><input id="search-input" class="palette-input" aria-label="搜索工具与上线任务" placeholder="搜索工具或上线任务，例如：日志" autocomplete="off"><div class="palette-results" id="search-results"></div><div class="dialog-head" style="padding:12px 23px;border-top:1px solid var(--line);border-bottom:0"><span class="muted" style="font-size:10px">Tab 选择 · Enter 打开 · Esc 关闭</span><button class="text-button" data-close>关闭 ${icon('close')}</button></div></dialog>
<div class="toast" id="toast" role="status"></div>`;

const detail=document.getElementById('detail-dialog'), search=document.getElementById('search-dialog');
function renderTasks(){
 const matched=tasks.filter(t=>filter==='all'||taskStatus(t)===filter);
 const visible=showAll?matched:matched.slice(0,4);
 document.getElementById('task-list').innerHTML=visible.length?visible.map(t=>`<div class="task-row ${t.done?'completed':''}"><button class="task-check ${t.done?'checked':''}" role="checkbox" aria-checked="${t.done}" aria-label="${t.done?'取消完成':'标记完成'}：${esc(t.title)}" title="${t.done?'取消完成':'确认测试点通过后，标记上线完成'}" data-action="complete:${t.id}">${t.done?icon('check'):''}</button><button class="task-open" data-action="task:${t.id}"><span class="task-title"><strong>${esc(t.title)}</strong><span class="status ${taskStatus(t)}">${statusText[taskStatus(t)]}</span></span><span class="task-meta"><span>${esc(t.id)} · ${esc(t.system)} · ${t.date}</span><span class="test-count">${icon('check')}测试 ${passedCount(t)}/${t.tests.length}</span></span><span class="task-progress"><span class="task-track"><i style="width:${taskProgress(t)}%"></i></span><span>${taskProgress(t)}%</span></span></button></div>`).join(''):'<div class="empty">这个状态下暂时没有上线任务</div>';
 document.getElementById('recent-count').textContent=String(matched.length).padStart(2,'0');
 const expand=document.getElementById('expand-tasks');expand.innerHTML=showAll?'收起列表 '+icon('arrow'):`展开 ${matched.length} 项 `+icon('arrow');expand.disabled=!showAll&&matched.length<=4;
 document.querySelectorAll('[data-filter]').forEach(b=>{b.classList.toggle('active',b.dataset.filter===filter);b.setAttribute('aria-pressed',String(b.dataset.filter===filter))});
 updateSummary();
}
function updateSummary(){
 const done=tasks.filter(t=>t.done).length,total=tasks.length,percent=Math.round(done/total*100);
 const testTotal=tasks.reduce((n,t)=>n+t.tests.length,0),testDone=tasks.reduce((n,t)=>n+passedCount(t),0);
 document.getElementById('monthly-count').innerHTML=`${total} <small>项</small>`;
 document.getElementById('monthly-note').innerHTML=`<em>${done} 项</em> 已完成`;
 document.getElementById('completed-count').textContent=119+done;
 document.getElementById('release-percent').innerHTML=`${percent}<small>%</small>`;
 document.getElementById('release-summary').textContent=`已完成 ${done} / ${total} 项上线任务`;
 document.getElementById('release-pending').innerHTML=`<i class="point"></i>${total-done} 项正在推进`;
 const ring=document.querySelector('.progress-ring');ring.style.background=`conic-gradient(var(--accent) 0 ${percent}%,var(--line) ${percent}% 100%)`;ring.setAttribute('aria-label',`上线完成进度 ${percent}%，${total} 项中已完成 ${done} 项`);
 document.getElementById('release-milestones').innerHTML=`<div class="milestone done"><span class="step">${icon('check')}</span>测试点已通过<small>${testDone} / ${testTotal}</small></div><div class="milestone"><span class="step">${icon('clock')}</span>等待上线完成确认<small>${tasks.filter(t=>taskStatus(t)==='ready').length} 项</small></div><div class="milestone"><span class="step">${icon('activity')}</span>仍在测试验证<small>${tasks.filter(t=>taskStatus(t)==='run').length} 项</small></div>`;
}
function taskDetail(t){
 showDialog(t.title,`<span class="badge">本月上线任务 · ${t.id}</span><div class="detail-grid"><div><small>所属系统 / 计划上线</small>${esc(t.system)} · ${t.date}</div><div><small>任务状态 / 进度</small>${statusText[taskStatus(t)]} · ${taskProgress(t)}%</div></div><p>${esc(t.note)}</p><div class="spread"><h3>测试点</h3><span class="badge">${passedCount(t)} / ${t.tests.length} 已通过</span></div><div class="test-checklist">${t.tests.map((p,i)=>`<button class="test-item ${p.passed?'passed':''}" role="checkbox" aria-checked="${p.passed}" data-action="test:${t.id}:${i}"><span class="task-check ${p.passed?'checked':''}">${p.passed?icon('check'):''}</span><span>${esc(p.title)}</span><small>${p.passed?'已通过':'待测试'}</small></button>`).join('')}</div><p class="progress-help">示例进度：开发就绪占 25%，测试点占 65%，手动确认上线完成占 10%。全部测试点通过后，才能确认完成；不会自动将未测试项标记通过。</p><button class="${t.done?'secondary':'primary'}" data-action="complete:${t.id}">${icon('check')}${t.done?'取消完成标记':'确认上线完成'}</button>`);
}

function showDialog(title,body){if(search.open)search.close();document.getElementById('dialog-title').textContent=title;document.getElementById('dialog-body').innerHTML=body;if(!detail.open)detail.showModal()}
let toastTimer;
function toast(message){const el=document.getElementById('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),3200)}
function openSearch(){if(detail.open)detail.close();document.getElementById('search-input').value='';renderSearch('');if(!search.open)search.showModal();document.getElementById('search-input').focus()}
function renderSearch(query){const q=query.trim().toLowerCase();const rows=[...toolsList.map(t=>({title:t.name,sub:t.sub,icon:t.icon,action:`tool:${t.name}`})),{title:'写日报',sub:'个人效率',icon:'file',action:'daily'},...tasks.map(t=>({title:t.title,sub:t.id,icon:'file',action:`task:${t.id}`}))].filter(r=>(r.title+r.sub).toLowerCase().includes(q));document.getElementById('search-results').innerHTML=rows.length?rows.map(r=>`<button class="palette-result" data-action="${esc(r.action)}">${icon(r.icon)}<span>${esc(r.title)}</span><small>${esc(r.sub)}</small></button>`).join(''):'<div class="empty">没有找到结果，试试「数据」或「需求」</div>'}
function act(action){
 if(action==='home'){window.scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});return}
 if(action==='search'){openSearch();return}
 if(action==='settings'){showDialog('外观与动效',`<p>当前方案：${variants[variant][1]}。可关闭装饰动效，或切换方案继续比较。系统开启“减少动态效果”时，设计稿会自动跟随。</p><button class="secondary" data-action="motion" aria-pressed="${document.body.classList.contains('motion-off')}">${document.body.classList.contains('motion-off')?'开启动效':'关闭动效'}</button><div class="detail-grid">${Object.entries(variants).map(([k,v])=>`<a class="secondary" href="${k}.html">${v[0]} ${v[1]}</a>`).join('')}</div>`);return}
 if(action==='motion'){document.body.classList.toggle('motion-off');act('settings');return}
 if(action==='create'){showDialog('新建上线任务',`<p>体验轻量录入流程。内容仅保留在当前预览页，刷新后恢复，不会写入软件。</p><form id="create-form"><label for="req-title">任务名称</label><input id="req-title" name="title" required maxlength="60" placeholder="给这个新想法起个名字" autofocus><label for="req-note">备注</label><textarea id="req-note" name="note" maxlength="400" placeholder="描述要解决的问题…"></textarea><button class="primary" type="submit">${icon('plus')}添加到预览</button></form>`);return}
 if(action==='daily'){showDialog('记录今天的进展',`<p>2026 年 9 月 7 日 · 星期一 · 预览内容仅在本页暂存。</p><form id="daily-form"><label for="daily-text">今天完成了什么？</label><textarea id="daily-text" required maxlength="2000" placeholder="记录完成的事项、遇到的问题与明天的计划…">${esc(window.previewDaily||'')}</textarea><button class="primary" type="submit">${icon('check')}${dailySaved?'更新预览记录':'保存到预览'}</button></form>`);return}
 if(action==='release'){filter='all';showAll=true;renderTasks();document.querySelector('.recent').scrollIntoView({behavior:document.body.classList.contains('motion-off')||matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});return}
 if(action==='expand'){showAll=!showAll;renderTasks();return}
 if(action.startsWith('task:')){const t=tasks.find(t=>t.id===action.slice(5));if(t)taskDetail(t);return}
 if(action.startsWith('complete:')){const t=tasks.find(t=>t.id===action.slice(9));if(!t)return;if(!t.done&&passedCount(t)!==t.tests.length){taskDetail(t);toast(`还有 ${t.tests.length-passedCount(t)} 个测试点待确认。`);return}t.done=!t.done;renderTasks();if(detail.open)taskDetail(t);toast(t.done?'已标记上线完成，月度统计已更新。':'已取消完成标记，任务恢复为待确认。');return}
 if(action.startsWith('test:')){const [,id,index]=action.split(':');const t=tasks.find(t=>t.id===id);if(!t)return;const point=t.tests[Number(index)];point.passed=!point.passed;if(!point.passed)t.done=false;renderTasks();taskDetail(t);const buttons=document.querySelectorAll('.test-item');buttons[Number(index)]?.focus();return}
 if(action.startsWith('tool:')){const t=toolsList.find(x=>x.name===action.slice(5));if(!t)return;showDialog(t.name,`<div class="flex">${icon(t.icon)}<span class="badge">${t.sub}</span></div><p>${t.note}</p><p>这是首页入口的交互预览，尚未连接真实业务。正式版通过现有导航进入对应模块。</p><button class="secondary" data-close>返回工作台 ${icon('arrow')}</button>`)}
}
document.addEventListener('click',e=>{const close=e.target.closest('[data-close]');if(close){close.closest('dialog').close();return}const f=e.target.closest('[data-filter]');if(f){filter=f.dataset.filter;renderTasks();return}const a=e.target.closest('[data-action]');if(a)act(a.dataset.action)});
document.addEventListener('submit',e=>{
 if(e.target.id==='create-form'){e.preventDefault();const title=document.getElementById('req-title').value.trim();if(!title){document.getElementById('req-title').setCustomValidity('请填写任务名称');document.getElementById('req-title').reportValidity();return}tasks.unshift({id:`REL-${String(nextId++).padStart(3,'0')}`,title,system:'个人工作空间',date:'09.07',done:false,tests:makeTests(0),note:document.getElementById('req-note').value.trim()||'暂未填写备注。'});filter='all';renderTasks();detail.close();toast('上线任务已添加到预览；刷新页面后恢复。')}
 if(e.target.id==='daily-form'){e.preventDefault();const input=document.getElementById('daily-text');if(!input.value.trim()){input.setCustomValidity('请填写日报内容');input.reportValidity();return}window.previewDaily=input.value;dailySaved=true;document.getElementById('daily-count').innerHTML='1 <small>/ 5</small>';document.getElementById('daily-note').textContent='今天的进展已记录 · 示例';detail.close();toast('日报已暂存到当前预览页。')}
});
document.addEventListener('input',e=>{if(e.target.setCustomValidity)e.target.setCustomValidity('');if(e.target.id==='search-input')renderSearch(e.target.value)});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch()}});
renderTasks();
