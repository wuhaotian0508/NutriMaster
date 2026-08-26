// ===== 前端认证模块（新增） =====
// 负责：Supabase 初始化、登录/注册/登出、全局 token 管理、管理员检测

let supabaseClient = null;   // Supabase JS 客户端实例
let authInitPromise = null;  // 共享初始化任务，避免表单提交早于 /api/config 返回
let currentSession = null;   // 当前登录会话（含 access_token）
let userProfile = null;      // 用户 profile（含 is_admin、nickname）
let adminPort = 5501;        // 管理后台端口（从 /api/config 获取）
let siteUrl = '';            // 部署地址（用于邮箱验证跳转）
let signupAvatarDataUrl = null;  // 注册时选择的头像 base64

// ===== 头像文件选择 → 缩略图 base64 =====
function handleAvatarFileChange(e) {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(ev) {
        const img = new Image();
        img.onload = function() {
            // 缩放到 80x80 的 JPEG 缩略图
            const canvas = document.createElement('canvas');
            canvas.width = 80;
            canvas.height = 80;
            const ctx = canvas.getContext('2d');
            // 居中裁剪
            const size = Math.min(img.width, img.height);
            const sx = (img.width - size) / 2;
            const sy = (img.height - size) / 2;
            ctx.drawImage(img, sx, sy, size, size, 0, 0, 80, 80);
            signupAvatarDataUrl = canvas.toDataURL('image/jpeg', 0.7);

            // 更新预览
            const preview = document.getElementById('avatar-preview');
            if (preview) {
                preview.innerHTML = `<img src="${signupAvatarDataUrl}" alt="avatar">`;
            }
        };
        img.src = ev.target.result;
    };
    reader.readAsDataURL(file);
}

// ===== 初始化 Supabase 客户端 =====
async function initAuth() {
    if (supabaseClient?.auth) return supabaseClient;
    if (authInitPromise) return authInitPromise;

    authInitPromise = (async () => {
        if (typeof supabase === 'undefined' || !supabase?.createClient) {
            throw new Error('Supabase SDK 加载失败');
        }

        // 从后端获取 Supabase 公开配置
        const resp = await fetch('/api/config');
        if (!resp.ok) {
            throw new Error(`/api/config 请求失败: ${resp.status}`);
        }
        const cfg = await resp.json();

        if (!cfg.supabase_url || !cfg.supabase_anon_key) {
            throw new Error('缺少 Supabase 前端配置');
        }

        // 用 anon key 初始化客户端（公开的，安全）
        const client = supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key);
        if (!client?.auth) {
            throw new Error('Supabase 客户端创建失败');
        }
        if (cfg.admin_port) adminPort = cfg.admin_port;
        if (cfg.site_url) siteUrl = cfg.site_url;

        // 监听登录状态变化（刷新页面、token 过期等）
        // onAuthStateChange 会自动触发 INITIAL_SESSION 事件，无需再手动 getSession
        client.auth.onAuthStateChange((event, session) => {
            currentSession = session;
            handleAuthStateChange(session).catch(err => {
                console.error('处理登录状态变化失败:', err);
            });
        });

        supabaseClient = client;
        return client;
    })();

    try {
        return await authInitPromise;
    } catch (err) {
        supabaseClient = null;
        console.error('认证初始化失败:', err);
        showLoginOverlay();
        showAuthError(err.message || '认证初始化失败');
        throw err;
    } finally {
        // 失败后允许用户再次提交时重试配置请求；成功时客户端本身即为缓存。
        if (!supabaseClient) authInitPromise = null;
    }
}

// 登录/注册可能发生在页面启动的异步初始化完成之前，所有认证操作都必须等待它。
async function getReadyAuthClient() {
    try {
        const client = await initAuth();
        if (!client?.auth) throw new Error('认证客户端不可用');
        return client;
    } catch (err) {
        const detail = err?.message || '请刷新页面后重试';
        throw new Error(`认证服务初始化失败：${detail}`);
    }
}

// ===== 登录状态变化处理 =====
async function handleAuthStateChange(session) {
    if (session) {
        // 已登录 → 隐藏登录界面，显示主内容
        hideLoginOverlay();
        updateUserButton(session.user);
        // 获取用户 profile（含管理员状态）
        await fetchUserProfile();
    } else {
        // 未登录 → 显示登录界面，重置管理员状态
        userProfile = null;
        hideAdminButton();
        showLoginOverlay();
    }
}

// ===== 获取当前 access_token（给 app.js 调用） =====
function getAccessToken() {
    return currentSession?.access_token || '';
}

// ===== 邮箱+密码登录 =====
async function loginWithEmail(email, password) {
    const client = await getReadyAuthClient();
    const { data, error } = await client.auth.signInWithPassword({
        email: email,
        password: password,
    });

    if (error) throw error;
    return data;
}

// ===== 邮箱+密码注册（支持 nickname 和 avatar_url） =====
async function signUpWithEmail(email, password, metadata) {
    const client = await getReadyAuthClient();
    const options = {};
    if (metadata) options.data = metadata;
    // 指定邮箱验证后的跳转地址（使用实际部署地址，而非 localhost）
    if (siteUrl) options.emailRedirectTo = siteUrl;

    const { data, error } = await client.auth.signUp({
        email: email,
        password: password,
        options: options,
    });

    if (error) throw error;
    return data;
}

// ===== 登出 =====
async function logout() {
    const client = await getReadyAuthClient();
    const { error } = await client.auth.signOut();
    if (error) throw error;
    currentSession = null;
}

// ===== 显示登录覆盖层（全屏遮挡主内容） =====
function showLoginOverlay() {
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.style.display = 'flex';
}

// ===== 隐藏登录覆盖层 =====
function hideLoginOverlay() {
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.style.display = 'none';
}

// ===== 更新用户按钮显示（头像 + 昵称） =====
function updateUserButton(user) {
    const avatarEl = document.getElementById('user-avatar');
    const nicknameEl = document.getElementById('user-nickname');
    const meta = user?.user_metadata || {};

    if (avatarEl && user) {
        if (meta.avatar_url) {
            avatarEl.textContent = '';
            avatarEl.innerHTML = `<img src="${meta.avatar_url}" alt="avatar">`;
        } else {
            const initial = (user.email || '?')[0].toUpperCase();
            avatarEl.innerHTML = '';
            avatarEl.textContent = initial;
        }
        avatarEl.title = user.email;
    }
    if (nicknameEl && user) {
        const nick = meta.nickname || (user.email || '').split('@')[0] || 'User';
        nicknameEl.textContent = nick;
    }
}

// ===== 获取用户 profile（管理员状态等） =====
async function fetchUserProfile() {
    const MAX_RETRIES = 3;

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        try {
            // 直接用 currentSession（已由 onAuthStateChange 回调设好）
            // 不再调 getSession()，避免刚登录时 SDK 内部 session 还没持久化导致拿到空 token
            const token = getAccessToken();
            if (!token) {
                console.warn('fetchUserProfile: token 为空，跳过');
                return;
            }

            const resp = await fetch('/api/user/profile', {
                headers: { 'Authorization': 'Bearer ' + token },
            });
            if (!resp.ok) {
                throw new Error(`status ${resp.status}`);
            }
            userProfile = await resp.json();

            // 更新昵称显示
            const nicknameEl = document.getElementById('user-nickname');
            if (nicknameEl && userProfile.nickname) {
                nicknameEl.textContent = userProfile.nickname;
            }

            // 更新头像显示
            const avatarEl = document.getElementById('user-avatar');
            if (avatarEl && userProfile.avatar_url) {
                avatarEl.textContent = '';
                avatarEl.innerHTML = `<img src="${userProfile.avatar_url}" alt="avatar">`;
            }

            // 管理员 → 显示管理后台按钮
            if (userProfile.is_admin) {
                showAdminButton();
            } else {
                hideAdminButton();
            }
            return; // 成功，退出重试循环
        } catch (e) {
            console.warn(`获取用户 profile 失败 (第${attempt}次):`, e);
            if (attempt < MAX_RETRIES) {
                await new Promise(r => setTimeout(r, 1000 * attempt)); // 1s, 2s 退避
            }
        }
    }
    console.error('获取用户 profile 最终失败，管理员状态可能不正确');
}

// ===== 管理后台按钮显示/隐藏 =====
function showAdminButton() {
    const btn = document.getElementById('admin-btn');
    if (btn) btn.style.display = 'inline-block';
}

function hideAdminButton() {
    const btn = document.getElementById('admin-btn');
    if (btn) btn.style.display = 'none';
}

// ===== 打开管理后台（同域 /admin 路径） =====
function openAdminPanel() {
    window.open('/admin', '_blank');
}

// ===== 关闭管理后台（兼容旧调用） =====
function closeAdminPanel() {
    // 新标签页模式无需关闭，保留函数避免报错
}

// ===== 切换登录/注册表单 =====
function switchAuthForm(mode) {
    const loginForm = document.getElementById('login-form');
    const signupForm = document.getElementById('signup-form');
    const verifyForm = document.getElementById('verify-code-form');
    const loginTab = document.getElementById('tab-login');
    const signupTab = document.getElementById('tab-signup');

    if (mode === 'signup') {
        loginForm.style.display = 'none';
        signupForm.style.display = 'flex';
        verifyForm.style.display = 'none';
        loginTab.classList.remove('active');
        signupTab.classList.add('active');
    } else {
        loginForm.style.display = 'flex';
        signupForm.style.display = 'none';
        verifyForm.style.display = 'none';
        loginTab.classList.add('active');
        signupTab.classList.remove('active');
    }
    // 清除错误信息
    clearAuthError();
}

// ===== 显示认证错误信息 =====
function showAuthError(msg) {
    const el = document.getElementById('auth-error');
    if (el) {
        el.textContent = msg;
        el.style.display = 'block';
    }
}

// ===== 清除错误信息 =====
function clearAuthError() {
    const el = document.getElementById('auth-error');
    if (el) {
        el.textContent = '';
        el.style.display = 'none';
    }
}

// ===== 处理登录表单提交 =====
async function handleLogin(e) {
    e.preventDefault();
    clearAuthError();

    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;

    if (!email || !password) {
        showAuthError('请输入邮箱和密码');
        return;
    }

    // 禁用按钮，防止重复点击
    const btn = document.getElementById('login-submit-btn');
    btn.disabled = true;
    btn.textContent = '登录中...';

    try {
        await loginWithEmail(email, password);
    } catch (err) {
        showAuthError(err.message || '登录失败');
    } finally {
        btn.disabled = false;
        btn.textContent = '登 录';
    }
}

// ===== 处理注册表单提交 =====
let pendingSignupEmail = '';
let pendingSignupPassword = '';
let pendingSignupPasswordReusable = true;

async function handleSignUp(e) {
    e.preventDefault();
    clearAuthError();

    const email = document.getElementById('signup-email').value.trim();
    const password = document.getElementById('signup-password').value;
    const confirmPassword = document.getElementById('signup-confirm-password').value;
    const nickname = (document.getElementById('signup-nickname')?.value || '').trim();

    if (!email || !password) {
        showAuthError('请输入邮箱和密码');
        return;
    }

    if (password !== confirmPassword) {
        showAuthError('两次密码输入不一致');
        return;
    }

    if (password.length < 6) {
        showAuthError('密码至少 6 位');
        return;
    }

    const btn = document.getElementById('signup-submit-btn');
    btn.disabled = true;
    btn.textContent = '注册中...';

    try {
        // 调用后端注册接口
        const payload = { email, password };
        if (nickname) payload.nickname = nickname;
        if (signupAvatarDataUrl) payload.avatar_url = signupAvatarDataUrl;

        const resp = await fetch('/api/auth/signup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        const data = await resp.json();

        if (!resp.ok) {
            showAuthError(data.error || data.message || '注册失败');
            return;
        }

        // 注册成功，保存邮箱和密码用于验证后自动登录
        pendingSignupEmail = email;
        pendingSignupPassword = password;
        pendingSignupPasswordReusable = data.password_updated !== false;

        // 隐藏注册表单，显示验证码输入界面
        document.getElementById('signup-form').style.display = 'none';
        document.getElementById('verify-code-form').style.display = 'flex';
        document.getElementById('verify-email-display').textContent = email;

        showAuthError(''); // 清除错误
    } catch (err) {
        showAuthError(err.message || '注册失败，请稍后再试');
    } finally {
        btn.disabled = false;
        btn.textContent = '注 册';
    }
}

// ===== 处理验证码提交 =====
async function handleVerifyCode(e) {
    e.preventDefault();
    clearAuthError();

    const code = document.getElementById('verify-code-input').value.trim();

    if (!code || code.length !== 6) {
        showAuthError('请输入 6 位验证码');
        return;
    }

    const btn = document.getElementById('verify-code-btn');
    btn.disabled = true;
    btn.textContent = '验证中...';

    try {
        const resp = await fetch('/api/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: pendingSignupEmail,
                code: code,
            }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            showAuthError(data.error || data.message || '验证失败');
            return;
        }

        // 验证成功：仅在本次注册创建了新账号时自动登录。
        if (pendingSignupPasswordReusable && pendingSignupPassword) {
            try {
                await loginWithEmail(pendingSignupEmail, pendingSignupPassword);
                // 登录成功后会自动触发 handleAuthStateChange，隐藏登录界面
                // 重置验证码表单
                document.getElementById('verify-code-form').style.display = 'none';
                document.getElementById('verify-code-input').value = '';
                document.getElementById('signup-form').style.display = 'flex';
                pendingSignupEmail = '';
                pendingSignupPassword = '';
                pendingSignupPasswordReusable = true;
            } catch (loginErr) {
                showAuthError('验证成功，但自动登录失败，请手动登录');
                document.getElementById('verify-code-form').style.display = 'none';
                document.getElementById('signup-form').style.display = 'none';
                switchAuthForm('login');
            }
        } else {
            showAuthError('验证成功，请使用原密码登录');
            document.getElementById('verify-code-form').style.display = 'none';
            document.getElementById('signup-form').style.display = 'none';
            switchAuthForm('login');
        }
    } catch (err) {
        showAuthError(err.message || '验证失败，请稍后再试');
    } finally {
        btn.disabled = false;
        btn.textContent = '验 证';
    }
}

// ===== 重新发送验证码 =====
async function handleResendCode() {
    clearAuthError();

    const btn = document.getElementById('resend-code-btn');
    btn.disabled = true;
    btn.textContent = '发送中...';

    try {
        const resp = await fetch('/api/auth/resend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: pendingSignupEmail }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            showAuthError(data.error || data.message || '发送失败');
            return;
        }

        showAuthError('新验证码已发送，请查收邮箱');
    } catch (err) {
        showAuthError(err.message || '发送失败，请稍后再试');
    } finally {
        btn.disabled = false;
        btn.textContent = '重新发送';
    }
}

// ===== 返回注册表单 =====
function handleBackToSignup() {
    document.getElementById('verify-code-form').style.display = 'none';
    document.getElementById('signup-form').style.display = 'flex';
    document.getElementById('verify-code-input').value = '';
    pendingSignupPasswordReusable = true;
    clearAuthError();
}

// ===== 点击用户区域 → 显示用户信息弹出框 =====
function handleUserAreaClick() {
    if (!currentSession) {
        showLoginOverlay();
        return;
    }

    // 已登录：显示用户信息弹窗
    const existing = document.getElementById('user-popover');
    if (existing) {
        existing.remove();
        return;
    }

    const userArea = document.getElementById('user-area');
    const popover = document.createElement('div');
    popover.id = 'user-popover';
    popover.className = 'user-popover';

    const currentNickname = userProfile?.nickname || currentSession.user.user_metadata?.nickname || currentSession.user.email.split('@')[0];
    const currentAvatarUrl = userProfile?.avatar_url || currentSession.user.user_metadata?.avatar_url || '';

    popover.innerHTML = `
        <div class="popover-email">${escapeHtml(currentSession.user.email)}</div>
        <div class="popover-profile-section">
            <div class="popover-avatar-row">
                <div class="popover-avatar-preview" id="popover-avatar-preview">
                    ${currentAvatarUrl ? `<img src="${currentAvatarUrl}" alt="avatar">` : `<span>${(currentSession.user.email || '?')[0].toUpperCase()}</span>`}
                </div>
                <label class="popover-avatar-change" for="popover-avatar-input">${t('profile.changeAvatar')}</label>
                <input type="file" id="popover-avatar-input" accept="image/*" style="display:none;">
            </div>
            <div class="popover-nickname-row">
                <input type="text" id="popover-nickname-input" value="${escapeHtml(currentNickname)}" placeholder="${t('auth.nicknamePlaceholder')}" maxlength="30">
            </div>
            <button class="popover-save-btn" id="popover-save-btn">${t('profile.save')}</button>
        </div>
        <div class="popover-actions">
            <button class="popover-logout-btn" onclick="handleLogout()">${t('auth.logout')}</button>
            <button class="popover-delete-btn" onclick="handleDeleteAccount()">${t('profile.deleteAccount')}</button>
        </div>
    `;

    // 定位在用户区域下方
    userArea.parentElement.style.position = 'relative';
    userArea.parentElement.appendChild(popover);

    // 头像选择处理
    let popoverAvatarDataUrl = null;
    const popoverAvatarInput = document.getElementById('popover-avatar-input');
    popoverAvatarInput.addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function(ev) {
            const img = new Image();
            img.onload = function() {
                const canvas = document.createElement('canvas');
                canvas.width = 80;
                canvas.height = 80;
                const ctx = canvas.getContext('2d');
                const size = Math.min(img.width, img.height);
                const sx = (img.width - size) / 2;
                const sy = (img.height - size) / 2;
                ctx.drawImage(img, sx, sy, size, size, 0, 0, 80, 80);
                popoverAvatarDataUrl = canvas.toDataURL('image/jpeg', 0.7);
                const preview = document.getElementById('popover-avatar-preview');
                if (preview) preview.innerHTML = `<img src="${popoverAvatarDataUrl}" alt="avatar">`;
            };
            img.src = ev.target.result;
        };
        reader.readAsDataURL(file);
    });

    // 保存按钮
    document.getElementById('popover-save-btn').addEventListener('click', async function() {
        const btn = this;
        const nickname = document.getElementById('popover-nickname-input').value.trim();
        const payload = {};
        if (nickname) payload.nickname = nickname;
        if (popoverAvatarDataUrl) payload.avatar_url = popoverAvatarDataUrl;

        if (Object.keys(payload).length === 0) return;

        btn.disabled = true;
        btn.textContent = t('profile.saving');

        try {
            const resp = await fetch('/api/user/profile', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + getAccessToken(),
                },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                const d = await resp.json();
                alert(d.error || t('profile.saveFail'));
                return;
            }
            // 刷新显示
            await fetchUserProfile();
            // 关闭弹窗
            popover.remove();
        } catch (e) {
            alert(t('profile.saveFail'));
        } finally {
            btn.disabled = false;
            btn.textContent = t('profile.save');
        }
    });

    // 点击其他地方关闭
    setTimeout(() => {
        document.addEventListener('click', function closePopover(e) {
            if (!popover.contains(e.target) && !userArea.contains(e.target)) {
                popover.remove();
                document.removeEventListener('click', closePopover);
            }
        });
    }, 0);
}

// ===== 处理登出 =====
async function handleLogout() {
    try {
        await logout();
        const popover = document.getElementById('user-popover');
        if (popover) popover.remove();
        // 重置用户区域
        const avatarEl = document.getElementById('user-avatar');
        const nicknameEl = document.getElementById('user-nickname');
        if (avatarEl) { avatarEl.innerHTML = ''; avatarEl.textContent = '👤'; avatarEl.title = ''; }
        if (nicknameEl) nicknameEl.textContent = '';
        userProfile = null;
        hideAdminButton();
        closeAdminPanel();
    } catch (err) {
        console.error('登出失败:', err);
    }
}

// ===== 处理销户（删除账号） =====
async function handleDeleteAccount() {
    const confirmMsg = t('profile.confirmDelete');
    if (!confirm(confirmMsg)) return;

    try {
        const resp = await fetch('/api/user/account', {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });
        if (!resp.ok) {
            const d = await resp.json();
            alert(d.error || t('profile.deleteFail'));
            return;
        }
        // 删除成功 → 登出
        const popover = document.getElementById('user-popover');
        if (popover) popover.remove();
        const client = await getReadyAuthClient();
        await client.auth.signOut();
        currentSession = null;
        userProfile = null;
        const avatarEl = document.getElementById('user-avatar');
        const nicknameEl = document.getElementById('user-nickname');
        if (avatarEl) { avatarEl.innerHTML = ''; avatarEl.textContent = '👤'; avatarEl.title = ''; }
        if (nicknameEl) nicknameEl.textContent = '';
        hideAdminButton();
        showLoginOverlay();
    } catch (err) {
        console.error('删除账号失败:', err);
        alert(t('profile.deleteFail'));
    }
}

// ===== 绑定认证相关事件 =====
function setupAuthListeners() {
    // 登录表单
    const loginForm = document.getElementById('login-form');
    if (loginForm) loginForm.addEventListener('submit', handleLogin);

    // 注册表单
    const signupForm = document.getElementById('signup-form');
    if (signupForm) signupForm.addEventListener('submit', handleSignUp);

    // Tab 切换
    const loginTab = document.getElementById('tab-login');
    const signupTab = document.getElementById('tab-signup');
    if (loginTab) loginTab.addEventListener('click', () => switchAuthForm('login'));
    if (signupTab) signupTab.addEventListener('click', () => switchAuthForm('signup'));

    // 用户区域点击
    const userArea = document.getElementById('user-area');
    if (userArea) userArea.addEventListener('click', handleUserAreaClick);

    // 注册头像选择
    const avatarFileInput = document.getElementById('avatar-file-input');
    if (avatarFileInput) avatarFileInput.addEventListener('change', handleAvatarFileChange);

    // 验证码表单
    const verifyForm = document.getElementById('verify-code-form');
    if (verifyForm) verifyForm.addEventListener('submit', handleVerifyCode);

    const resendBtn = document.getElementById('resend-code-btn');
    if (resendBtn) resendBtn.addEventListener('click', handleResendCode);

    const backToSignupBtn = document.getElementById('back-to-signup-btn');
    if (backToSignupBtn) backToSignupBtn.addEventListener('click', handleBackToSignup);

    // 验证码输入框：只允许数字
    const verifyInput = document.getElementById('verify-code-input');
    if (verifyInput) {
        verifyInput.addEventListener('input', function() {
            this.value = this.value.replace(/\D/g, '').slice(0, 6);
        });
    }

    // 管理后台按钮
    const adminBtn = document.getElementById('admin-btn');
    if (adminBtn) adminBtn.addEventListener('click', openAdminPanel);

    // 管理后台关闭按钮
    const adminCloseBtn = document.getElementById('admin-close-btn');
    if (adminCloseBtn) adminCloseBtn.addEventListener('click', closeAdminPanel);
}
