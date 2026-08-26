/* ═══════════════════════════════════════════════════════════════════════
 *  NutriMaster Admin — 前端逻辑
 *
 *  模块：
 *    1. Auth      — Supabase 邮箱/密码认证（复用 server 的模式）
 *    2. Tabs      — 5 个 tab 切换（Dashboard/Upload/Pipeline/Papers/Editor）
 *    3. Dashboard — 统计卡片，显示 input 队列数、已处理数、归档数
 *    4. Upload    — 拖拽/选择 zip 上传，递归解压 .md，显示去重结果
 *    5. Pipeline  — 核心面板：Settings + Preview/Run/Stop + SSE 进度 + Output
 *    6. Papers    — 表格展示 data/ 中所有 verified JSON
 *    7. Editor    — 在线编辑 prompt 和 schema 文件
 * ═══════════════════════════════════════════════════════════════════════ */

// ═════════════════════════════════════════════════════════════════════
//  全局状态
// ═════════════════════════════════════════════════════════════════════

let supabaseClient = null;   // Supabase JS SDK 客户端实例
let currentSession = null;   // 当前登录会话（含 access_token）
let sseSource = null;        // SSE EventSource 实例（pipeline 运行时）
let outputPollTimer = null;  // 定时器：轮询 pipeline console output
let tokenPollTimer = null;   // 定时器：轮询 pipeline token 用量

// ═════════════════════════════════════════════════════════════════════
//  初始化
// ═════════════════════════════════════════════════════════════════════

// API 基础路径（集成到 NutriMaster web app 时为 /admin，独立运行时为空）
const ADMIN_BASE = '/admin';

document.addEventListener('DOMContentLoaded', initApp);

async function initApp() {
    setupAuthListeners();  // 绑定登录/注册表单事件
    setupTabs();           // 绑定顶部 tab 切换
    setupUpload();         // 绑定上传拖拽/选择事件
    await initAuth();      // 初始化 Supabase 认证
}

// ═════════════════════════════════════════════════════════════════════
//  Auth 模块 — Supabase 邮箱/密码认证
// ═════════════════════════════════════════════════════════════════════

/**
 * 初始化 Supabase 客户端，恢复已有会话。
 * 先从后端 /api/config（RAG 主站）获取 Supabase 公钥。
 * 因为 admin 现在集成在同一个 Flask app，共享同源 localStorage session。
 */
async function initAuth() {
    try {
        // 从 RAG 主站的 /api/config 获取 Supabase 配置（保证和主站一致）
        const resp = await fetch('/api/config');
        const cfg = await resp.json();
        // 用 anon key 创建前端客户端（只能做登录/注册，不能做管理操作）
        supabaseClient = supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key);

        // 监听认证状态变化（登录/登出/token 刷新）
        supabaseClient.auth.onAuthStateChange((_event, session) => {
            currentSession = session;
            handleAuthStateChange(session);
        });

        // 尝试恢复已有会话（浏览器 localStorage 中的 token）
        const { data } = await supabaseClient.auth.getSession();
        currentSession = data.session;
        handleAuthStateChange(data.session);
    } catch (err) {
        console.error('Auth init failed:', err);
        showLoginOverlay();
    }
}

/** 获取当前 access token（用于 API 请求的 Authorization header）。 */
function getAccessToken() {
    return currentSession?.access_token || '';
}

/** 构建带 Bearer token 的请求头。 */
function authHeaders() {
    return { 'Authorization': `Bearer ${getAccessToken()}` };
}

/** 根据会话状态切换：有会话→显示主界面，无会话→显示登录页。 */
function handleAuthStateChange(session) {
    if (session) {
        hideLoginOverlay();
        updateUserButton(session.user);
        refreshDashboard();
    } else {
        showLoginOverlay();
    }
}

function showLoginOverlay() {
    document.getElementById('auth-overlay').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
}

function hideLoginOverlay() {
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
}

/** 用户按钮显示邮箱首字母。 */
function updateUserButton(user) {
    const btn = document.querySelector('.user-btn');
    if (btn && user) {
        btn.textContent = (user.email || '?')[0].toUpperCase();
        btn.title = user.email;
    }
}

/** 切换登录/注册表单。 */
function switchAuthForm(mode) {
    document.getElementById('login-form').style.display = mode === 'login' ? 'block' : 'none';
    document.getElementById('signup-form').style.display = mode === 'signup' ? 'block' : 'none';
    document.getElementById('tab-login').classList.toggle('active', mode === 'login');
    document.getElementById('tab-signup').classList.toggle('active', mode === 'signup');
    clearAuthError();
}

function showAuthError(msg) {
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.style.display = 'block';
}

function clearAuthError() {
    document.getElementById('auth-error').style.display = 'none';
}

/** 绑定登录/注册表单提交 + 用户按钮点击事件。 */
function setupAuthListeners() {
    // ── 登录表单 ──
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearAuthError();
        const email = document.getElementById('login-email').value.trim();
        const password = document.getElementById('login-password').value;
        if (!email || !password) { showAuthError('Please enter email and password'); return; }
        const btn = document.getElementById('login-submit-btn');
        btn.disabled = true; btn.textContent = 'Logging in...';
        try {
            const { error } = await supabaseClient.auth.signInWithPassword({ email, password });
            if (error) throw error;
        } catch (err) {
            showAuthError(err.message || 'Login failed');
        } finally {
            btn.disabled = false; btn.textContent = 'Login';
        }
    });

    // ── 注册表单 ──
    document.getElementById('signup-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        clearAuthError();
        const email = document.getElementById('signup-email').value.trim();
        const pw = document.getElementById('signup-password').value;
        const pw2 = document.getElementById('signup-confirm-password').value;
        if (!email || !pw) { showAuthError('Please fill all fields'); return; }
        if (pw !== pw2) { showAuthError('Passwords do not match'); return; }
        if (pw.length < 6) { showAuthError('Password must be at least 6 characters'); return; }
        const btn = document.getElementById('signup-submit-btn');
        btn.disabled = true; btn.textContent = 'Signing up...';
        try {
            const { data, error } = await supabaseClient.auth.signUp({ email, password: pw });
            if (error) throw error;
            if (data.user && !data.session) {
                showAuthError('Registered! Please check email for confirmation link.');
                switchAuthForm('login');
            }
        } catch (err) {
            showAuthError(err.message || 'Sign up failed');
        } finally {
            btn.disabled = false; btn.textContent = 'Sign Up';
        }
    });

    // ── 右上角用户按钮 → 弹出 popover ──
    document.querySelector('.user-btn').addEventListener('click', handleUserBtnClick);
}

/** 点击用户头像按钮：显示/隐藏 popover（含邮箱和登出按钮）。 */
function handleUserBtnClick() {
    if (!currentSession) { showLoginOverlay(); return; }
    const existing = document.getElementById('user-popover');
    if (existing) { existing.remove(); return; }

    const btn = document.querySelector('.user-btn');
    const popover = document.createElement('div');
    popover.id = 'user-popover';
    popover.className = 'user-popover';
    popover.innerHTML = `
        <div class="popover-email">${currentSession.user.email}</div>
        <button class="popover-logout-btn" id="popover-logout">Logout</button>
    `;
    btn.parentElement.style.position = 'relative';
    btn.parentElement.appendChild(popover);
    document.getElementById('popover-logout').onclick = handleLogout;

    // 点击外部关闭 popover
    setTimeout(() => {
        document.addEventListener('click', function close(e) {
            if (!popover.contains(e.target) && e.target !== btn) {
                popover.remove();
                document.removeEventListener('click', close);
            }
        });
    }, 0);
}

async function handleLogout() {
    try {
        await supabaseClient.auth.signOut();
        currentSession = null;
        const popover = document.getElementById('user-popover');
        if (popover) popover.remove();
    } catch (err) {
        console.error('Logout failed:', err);
    }
}

// ═════════════════════════════════════════════════════════════════════
//  Tab 切换
// ═════════════════════════════════════════════════════════════════════

function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // 高亮当前 tab，隐藏其他 panel
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const panel = document.getElementById('panel-' + btn.dataset.tab);
            if (panel) panel.classList.add('active');
            onTabSwitch(btn.dataset.tab);
        });
    });
}

/** 切换 tab 时自动刷新对应面板的数据。 */
function onTabSwitch(tab) {
    if (tab === 'dashboard') refreshDashboard();
    else if (tab === 'pipeline') refreshPipelineTab();
    else if (tab === 'papers') loadPapers();
    else if (tab === 'editor') loadEditor();
}

// ═════════════════════════════════════════════════════════════════════
//  Dashboard — 统计卡片
// ═════════════════════════════════════════════════════════════════════

async function refreshDashboard() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/status', { headers: authHeaders() });
        if (!resp.ok) return;
        const d = await resp.json();
        document.getElementById('stat-input').textContent = d.input_queue;
        document.getElementById('stat-processed').textContent = d.processed_total;
        document.getElementById('stat-waitlist').textContent = d.waitlist;

        // pipeline 运行中时显示蓝色状态横幅
        const banner = document.getElementById('pipeline-status-banner');
        if (d.pipeline_running) {
            banner.style.display = 'flex';
            document.getElementById('pipeline-status-text').textContent =
                `Pipeline running: ${d.pipeline_done}/${d.pipeline_total} papers`;
        } else {
            banner.style.display = 'none';
        }

        // 同时刷新索引状态
        await refreshIndexStatus();
    } catch (e) { console.error('Dashboard error:', e); }
}

/**
 * 刷新 RAG 索引状态卡片
 */
async function refreshIndexStatus() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/index/status', { headers: authHeaders() });
        if (!resp.ok) return;
        const data = await resp.json();

        // 更新文件统计
        document.getElementById('index-files').textContent =
            `${data.indexed_files.toLocaleString()} / ${data.total_files.toLocaleString()}`;

        // 更新 chunks 数量
        document.getElementById('index-chunks').textContent =
            data.total_chunks ? data.total_chunks.toLocaleString() : '--';

        // 更新最后更新时间
        if (data.last_updated) {
            const date = new Date(data.last_updated);
            document.getElementById('index-updated').textContent =
                date.toLocaleString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit'
                });
        } else {
            document.getElementById('index-updated').textContent = 'Never';
        }

        // 更新状态徽章
        const statusBadge = document.getElementById('index-status');
        const build = data.build || {};
        const activeBuildStates = new Set([
            'queued', 'preflight', 'snapshotting', 'building', 'publishing', 'activating'
        ]);
        if (build.next_state === 'queued' || activeBuildStates.has(build.state)) {
            const state = build.next_state === 'queued' ? 'queued' : build.state;
            statusBadge.textContent = `⏳ Build ${state}`;
            statusBadge.className = 'badge badge-info';
        } else if (build.state === 'awaiting_activation') {
            statusBadge.textContent = '⚠️ Awaiting activation';
            statusBadge.className = 'badge badge-warning';
        } else if (build.state === 'failed') {
            statusBadge.textContent = '❌ Index build failed';
            statusBadge.className = 'badge badge-error';
        } else if (data.is_synced) {
            statusBadge.textContent = '✅ Synced';
            statusBadge.className = 'badge badge-success';
        } else {
            statusBadge.textContent = `⚠️ ${data.missing_files} files missing`;
            statusBadge.className = 'badge badge-warning';
        }
    } catch (e) {
        console.error('Index status error:', e);
    }
}

/**
 * 手动触发索引重建
 */
async function rebuildIndex() {
    const btn = document.getElementById('rebuild-index-btn');
    const originalText = btn.textContent;

    if (!confirm('Rebuild the RAG index? This may take several minutes to hours depending on the number of missing files.')) {
        return;
    }

    try {
        btn.disabled = true;
        btn.textContent = '📥 Queueing...';

        const resp = await fetch(ADMIN_BASE + '/api/index/rebuild', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: false, async: true })
        });

        if (!resp.ok) {
            throw new Error('Failed to start index rebuild');
        }

        const data = await resp.json();
        alert(data.message || `Index build queued (${data.job_id})`);

        // 5秒后刷新状态
        setTimeout(() => refreshIndexStatus(), 5000);
    } catch (e) {
        console.error('Rebuild error:', e);
        alert('Failed to start index rebuild: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}


// ═════════════════════════════════════════════════════════════════════
//  Upload — ZIP 上传
// ═════════════════════════════════════════════════════════════════════

function setupUpload() {
    const zone = document.getElementById('drop-zone');
    const input = document.getElementById('file-input');

    // 点击上传区域触发文件选择
    zone.addEventListener('click', () => input.click());
    // 拖拽效果
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
    });
    input.addEventListener('change', () => { if (input.files.length) uploadFile(input.files[0]); });
}

/** 上传 zip 文件到后端，后端递归解压并去重。 */
async function uploadFile(file) {
    if (!file.name.endsWith('.zip')) {
        alert('Please select a .zip file');
        return;
    }

    const progress = document.getElementById('upload-progress');
    const result = document.getElementById('upload-result');
    progress.style.display = 'flex';
    result.style.display = 'none';

    const form = new FormData();
    form.append('file', file);

    try {
        const resp = await fetch(ADMIN_BASE + '/api/upload', {
            method: 'POST',
            headers: authHeaders(),
            body: form,
        });
        const data = await resp.json();
        if (!resp.ok) { showUploadResult(`Error: ${data.error}`, true); return; }
        renderUploadResult(data);
    } catch (e) {
        showUploadResult(`Upload failed: ${e.message}`, true);
    } finally {
        progress.style.display = 'none';
        document.getElementById('file-input').value = '';
    }
}

/** 渲染上传结果：新增/跳过(已处理)/跳过(已在队列)。 */
function renderUploadResult(data) {
    const el = document.getElementById('upload-result');
    el.style.display = 'block';

    let html = '';
    if (data.new_files.length) {
        html += `<div><span class="count-good">${data.new_files.length}</span> new files added to input queue</div>`;
        html += `<div class="file-list">${data.new_files.join('<br>')}</div>`;
        // 添加提示：运行 Pipeline 会自动更新索引
        html += `<div style="margin-top:16px;padding:12px;background:var(--accent-blue-dim);border:1px solid var(--accent-blue);border-radius:var(--radius-sm);font-size:13px;">
            <strong>💡 Next step:</strong> Go to the <strong>Pipeline</strong> tab and run the extraction.
            The RAG index will be automatically updated after processing completes.
        </div>`;
    }
    if (data.skipped_processed.length) {
        html += `<div style="margin-top:10px"><span class="count-skip">${data.skipped_processed.length}</span> skipped (already in data)</div>`;
        html += `<div class="file-list">${data.skipped_processed.join('<br>')}</div>`;
    }
    if (data.skipped_existing.length) {
        html += `<div style="margin-top:10px"><span class="count-skip">${data.skipped_existing.length}</span> skipped (already in input queue)</div>`;
        html += `<div class="file-list">${data.skipped_existing.join('<br>')}</div>`;
    }
    if (!data.new_files.length && !data.skipped_processed.length && !data.skipped_existing.length) {
        html = 'No .md files found in the zip archive.';
    }
    el.innerHTML = html;
}

function showUploadResult(msg, isError) {
    const el = document.getElementById('upload-result');
    el.style.display = 'block';
    el.innerHTML = `<span style="color:${isError ? 'var(--accent-red)' : 'var(--text-primary)'}">${msg}</span>`;
}

// ═════════════════════════════════════════════════════════════════════
//  Pipeline — Settings 读取
// ═════════════════════════════════════════════════════════════════════

/** 从 Settings 输入框读取当前参数值。 */
function getSettings() {
    return {
        temperature: parseFloat(document.getElementById('set-temperature').value) || 0.7,
        verify_batch_genes: parseInt(document.getElementById('set-batch').value) || 12,
        max_workers: parseInt(document.getElementById('set-workers').value) || 32,
    };
}

/** 从后端加载 Settings 当前值并填入输入框。 */
async function loadSettings() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/settings', { headers: authHeaders() });
        if (!resp.ok) return;
        const d = await resp.json();
        document.getElementById('set-temperature').value = d.values.temperature;
        document.getElementById('set-batch').value = d.values.verify_batch_genes;
        document.getElementById('set-workers').value = d.values.max_workers;
        // 同步 input 的 min/max 属性
        document.getElementById('set-temperature').max = d.limits.temperature[1];
        document.getElementById('set-batch').max = d.limits.verify_batch_genes[1];
        document.getElementById('set-workers').max = d.limits.max_workers[1];
    } catch (e) { console.error('loadSettings error:', e); }
}

// ═════════════════════════════════════════════════════════════════════
//  Pipeline — 状态管理 & 操作
// ═════════════════════════════════════════════════════════════════════

/** 刷新 Pipeline tab：更新文件计数、检查是否有运行中任务、加载设置。 */
async function refreshPipelineTab() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/status', { headers: authHeaders() });
        if (!resp.ok) return;
        const d = await resp.json();
        document.getElementById('pipeline-file-count').textContent = d.input_queue + ' files';

        // 如果 pipeline 正在运行，切换到 running 面板并连接 SSE
        if (d.pipeline_running) {
            showPipeSection('running');
            if (!sseSource) connectSSE();
        }
    } catch (e) { console.error(e); }
    loadSettings();
}

/** 切换 Pipeline 子面板（idle/preview/running/complete）。 */
function showPipeSection(name) {
    ['idle', 'preview', 'running', 'complete'].forEach(s => {
        document.getElementById('pipe-' + s).style.display = s === name ? 'block' : 'none';
    });
}

/**
 * 预览：同步处理 input 中排序第一篇论文。
 * 发送当前 Settings 参数，处理完后显示 verified JSON + console output。
 */
async function startPreview() {
    const btn = document.getElementById('btn-preview');
    btn.disabled = true; btn.textContent = 'Processing...';

    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/preview', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: getSettings() }),
        });
        const data = await resp.json();
        if (!resp.ok) { alert(data.error); return; }

        showPipeSection('preview');
        document.getElementById('preview-status').innerHTML =
            `<strong>${data.filename}</strong> — status: <span style="color:${data.status === 'processed' ? 'var(--accent)' : 'var(--accent-red)'}">${data.status}</span>`;

        if (data.verified_json) {
            document.getElementById('preview-json').textContent =
                JSON.stringify(data.verified_json, null, 2);
        } else {
            document.getElementById('preview-json').textContent = 'No verified JSON generated.';
        }
        refreshPipelineTab();
        refreshOutput();  // 显示 console output
    } catch (e) {
        alert('Preview failed: ' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = 'Preview First Paper';
    }
}

/**
 * 批量运行：后台线程并行处理 input 中所有 .md 文件。
 * 发送当前 Settings 参数（temperature、batch size、workers）。
 * 自动跳过 data 中已有 verified JSON 的文件。
 */
async function startRun() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/run', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: getSettings() }),
        });
        const data = await resp.json();
        if (!resp.ok) { alert(data.error); return; }

        showPipeSection('running');
        document.getElementById('pipeline-log').innerHTML = '';
        document.getElementById('progress-bar').style.width = '0%';
        document.getElementById('progress-text').textContent = `Processing 0/${data.total}...`;

        // 如果有跳过的文件，显示提示
        if (data.skipped > 0) {
            addLogLine(`Skipped ${data.skipped} papers (already in data)`, 'info');
        }
        addLogLine(data.message, 'info');

        connectSSE();          // 连接 SSE 接收实时进度
        startOutputPolling();  // 开始轮询 console output
    } catch (e) {
        alert('Run failed: ' + e.message);
    }
}

// ═════════════════════════════════════════════════════════════════════
//  Pipeline — SSE 实时进度
// ═════════════════════════════════════════════════════════════════════

/**
 * 连接 SSE 端点，监听 pipeline 处理事件。
 * token 通过 URL query param 传递（EventSource 不支持自定义 header）。
 */
function connectSSE() {
    if (sseSource) sseSource.close();
    const token = getAccessToken();
    sseSource = new EventSource(`${ADMIN_BASE}/api/pipeline/stream?token=${encodeURIComponent(token)}`);

    // 开始轮询 token 用量
    tokenPollTimer = setInterval(pollTokenUsage, 3000);

    // 开始处理某篇论文
    sseSource.addEventListener('processing', (e) => {
        const d = JSON.parse(e.data);
        document.getElementById('progress-current').textContent = `Processing: ${d.filename}`;
        document.getElementById('progress-text').textContent = `Processing ${d.index + 1}/${d.total}...`;
    });

    // 某篇论文处理完成
    sseSource.addEventListener('paper_done', (e) => {
        const d = JSON.parse(e.data);
        const pct = Math.round((d.done / d.total) * 100);
        document.getElementById('progress-bar').style.width = pct + '%';
        document.getElementById('progress-text').textContent = `Processed ${d.done}/${d.total}`;
        addLogLine(
            `${d.filename} — ${d.status}`,
            d.status === 'processed' ? 'success' : d.status === 'failed' ? 'fail' : 'warn'
        );
    });

    // 正在提交独立索引构建任务
    sseSource.addEventListener('queueing_index', () => {
        addLogLine('📥 Queueing isolated RAG index build...', 'info');
    });

    // 任务已持久化排队；这不代表新代已经构建或激活
    sseSource.addEventListener('index_queued', (e) => {
        const d = JSON.parse(e.data);
        addLogLine(`📥 Index build queued: ${d.job_id}`, 'info');
        setTimeout(() => refreshIndexStatus(), 1000);
    });

    // 索引重建失败
    sseSource.addEventListener('index_error', (e) => {
        const d = JSON.parse(e.data);
        addLogLine(`❌ Index rebuild failed: ${d.error}`, 'fail');
    });

    // 全部完成
    sseSource.addEventListener('complete', (e) => {
        const d = JSON.parse(e.data);
        logTokenSummary(d.token_summary, d.token_report);
        sseSource.close();
        sseSource = null;
        stopTokenPolling();
        finishPipeline(`Complete: ${d.done}/${d.total} papers processed`);
    });

    // 被用户停止
    sseSource.addEventListener('stopped', (e) => {
        const d = JSON.parse(e.data);
        logTokenSummary(d.token_summary, d.token_report);
        sseSource.close();
        sseSource = null;
        stopTokenPolling();
        finishPipeline(`Stopped after ${d.done}/${d.total} papers`);
    });

    // pipeline 不在运行（兜底）
    sseSource.addEventListener('idle', () => {
        sseSource.close();
        sseSource = null;
        stopTokenPolling();
    });

    // SSE 连接断开
    sseSource.onerror = () => {
        sseSource.close();
        sseSource = null;
        stopTokenPolling();
    };
}

/** 向 Pipeline Log 面板追加一行日志。 */
function addLogLine(text, cls) {
    const log = document.getElementById('pipeline-log');
    const line = document.createElement('div');
    line.className = 'log-line ' + (cls || '');
    line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;  // 自动滚到底部
}

function logTokenSummary(summary, reportPath) {
    const total = summary?.total;
    if (!total) return;
    addLogLine(
        `Token usage: input ${total.prompt_ktokens}k, output ${total.completion_ktokens}k, total ${total.total_ktokens}k, calls ${total.calls}`,
        'info'
    );
    if (reportPath) {
        addLogLine(`Token report saved: ${reportPath}`, 'info');
    }
}

/** 轮询后端 /api/pipeline/tokens，实时更新 token 用量显示。 */
async function pollTokenUsage() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/tokens', { headers: authHeaders() });
        const data = await resp.json();
        const el = document.getElementById('token-live');
        if (!el) return;
        if (!data.summary || !data.summary.total) { el.textContent = ''; return; }
        const t = data.summary.total;
        el.textContent = `Tokens: ${t.total_ktokens}k (in ${t.prompt_ktokens}k / out ${t.completion_ktokens}k) · ${t.calls} calls`;
    } catch (e) { /* ignore */ }
}

function stopTokenPolling() {
    if (tokenPollTimer) { clearInterval(tokenPollTimer); tokenPollTimer = null; }
    // 最后拉一次确保显示最终数据
    pollTokenUsage();
}

/** Pipeline 完成（或被停止）：显示 complete 面板，停止 output 轮询。 */
function finishPipeline(message) {
    showPipeSection('complete');
    document.getElementById('complete-text').textContent = message;
    // 复制运行日志到 complete 面板
    document.getElementById('complete-log').innerHTML =
        document.getElementById('pipeline-log').innerHTML;
    stopOutputPolling();
    refreshOutput();       // 最终拉取一次完整输出
    refreshDashboard();    // 更新统计数字
}

/** 发送停止信号。当前论文处理完后 pipeline 会停止。 */
async function stopPipeline() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/stop', {
            method: 'POST',
            headers: authHeaders(),
        });
        const data = await resp.json();
        addLogLine(data.message || data.error, 'warn');
    } catch (e) {
        addLogLine('Stop request failed: ' + e.message, 'fail');
    }
}

// ═════════════════════════════════════════════════════════════════════
//  Pipeline — Console Output 查看器
//  每 5 秒从后端拉取 pipeline 的 print 输出（最后 5000 字符）
// ═════════════════════════════════════════════════════════════════════

async function refreshOutput() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/pipeline/output', { headers: authHeaders() });
        if (!resp.ok) return;
        const d = await resp.json();
        const viewer = document.getElementById('output-viewer');
        const wrap = document.getElementById('pipe-output');
        if (d.output) {
            viewer.textContent = d.output;
            wrap.style.display = 'block';
            viewer.scrollTop = viewer.scrollHeight;  // 滚到底部
        } else {
            wrap.style.display = 'none';
        }
    } catch (e) { console.error('Output fetch error:', e); }
}

function startOutputPolling() {
    stopOutputPolling();
    refreshOutput();
    outputPollTimer = setInterval(refreshOutput, 5000);  // 每 5 秒刷新
}

function stopOutputPolling() {
    if (outputPollTimer) {
        clearInterval(outputPollTimer);
        outputPollTimer = null;
    }
}

// ═════════════════════════════════════════════════════════════════════
//  Papers — 已处理论文表格
// ═════════════════════════════════════════════════════════════════════

async function loadPapers() {
    try {
        const resp = await fetch(ADMIN_BASE + '/api/papers', { headers: authHeaders() });
        if (!resp.ok) return;
        const papers = await resp.json();

        const tbody = document.getElementById('papers-tbody');
        if (!papers.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No papers found</td></tr>';
            return;
        }

        tbody.innerHTML = papers.map((p, i) => `
            <tr>
                <td>${i + 1}</td>
                <td>${escapeHtml(p.title || '—')}</td>
                <td>${escapeHtml(p.filename)}</td>
                <td>${p.modified}</td>
            </tr>
        `).join('');
    } catch (e) { console.error('Papers error:', e); }
}

// ═════════════════════════════════════════════════════════════════════
//  Editor — Prompt / Schema 在线编辑
// ═════════════════════════════════════════════════════════════════════

let editorLoaded = { prompt: false, schema: false };  // 防止重复加载

/** 切换 Prompt / Schema 子 tab。 */
function switchEditor(which) {
    document.querySelectorAll('.editor-tab').forEach(t =>
        t.classList.toggle('active', t.dataset.target === which));
    document.getElementById('editor-prompt').classList.toggle('active', which === 'prompt');
    document.getElementById('editor-schema').classList.toggle('active', which === 'schema');
    hideEditorStatus();
}

/** 从后端加载 prompt 和 schema 文件内容到 textarea。 */
async function loadEditor() {
    if (!editorLoaded.prompt) {
        try {
            const resp = await fetch(ADMIN_BASE + '/api/prompt', { headers: authHeaders() });
            if (resp.ok) {
                const d = await resp.json();
                document.getElementById('prompt-textarea').value = d.content;
                document.getElementById('prompt-path').textContent = d.path;
                editorLoaded.prompt = true;
            }
        } catch (e) { console.error(e); }
    }
    if (!editorLoaded.schema) {
        try {
            const resp = await fetch(ADMIN_BASE + '/api/schema', { headers: authHeaders() });
            if (resp.ok) {
                const d = await resp.json();
                document.getElementById('schema-textarea').value = d.content;
                document.getElementById('schema-path').textContent = d.path;
                editorLoaded.schema = true;
            }
        } catch (e) { console.error(e); }
    }
}

/** 保存 prompt 文件（后端会清除 lru_cache，下次提取用新版本）。 */
async function savePrompt() {
    const content = document.getElementById('prompt-textarea').value;
    try {
        const resp = await fetch(ADMIN_BASE + '/api/prompt', {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        const d = await resp.json();
        showEditorStatus(resp.ok ? d.message : d.error, resp.ok);
    } catch (e) { showEditorStatus('Save failed: ' + e.message, false); }
}

/** 保存 schema 文件（后端先验证 JSON 合法性，然后清除 lru_cache）。 */
async function saveSchema() {
    const content = document.getElementById('schema-textarea').value;
    try {
        const resp = await fetch(ADMIN_BASE + '/api/schema', {
            method: 'PUT',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        const d = await resp.json();
        showEditorStatus(resp.ok ? d.message : d.error, resp.ok);
    } catch (e) { showEditorStatus('Save failed: ' + e.message, false); }
}

function showEditorStatus(msg, ok) {
    const el = document.getElementById('editor-status');
    el.style.display = 'block';
    el.className = 'editor-status ' + (ok ? 'success' : 'error');
    el.textContent = msg;
    if (ok) setTimeout(hideEditorStatus, 3000);  // 成功提示 3 秒后消失
}

function hideEditorStatus() {
    document.getElementById('editor-status').style.display = 'none';
}

// ═════════════════════════════════════════════════════════════════════
//  工具函数
// ═════════════════════════════════════════════════════════════════════

/** 转义 HTML 特殊字符，防止 XSS。 */
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
