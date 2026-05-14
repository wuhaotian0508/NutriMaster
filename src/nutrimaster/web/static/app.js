// ===== 国际化 (i18n) =====
let currentLang = localStorage.getItem('lang') || 'zh';

const I18N = {
    zh: {
        // 语言按钮自身
        'lang.btn': '简体中文',
        // 侧边栏
        'sidebar.history': '历史会话',
        'sidebar.viewAll': '查看全部...',
        'sidebar.noHistory': '暂无历史记录',
        'sidebar.myLibrary': '我的知识库',
        // 头部
        'header.newChat': '+ 新对话',
        // 输入区
        'input.placeholder': '描述你的需求，例如：如何通过基因编辑提高大豆类黄酮含量',
        'input.personal': '个人库',
        'input.depth': '深度搜索',
        // 场景
        'scene.eyebrow': '科研场景',
        'scene.category': '场景介绍',
        'scene.subtitle': '围绕文献证据、调控网络与个人积累，构建更贴合植物营养代谢研究流程的智能检索入口。',
        'scene.retrieval.title': '检索增强',
        'scene.retrieval.desc': '每一个回答基于真实文献信息，夯实营养代谢科研知识根基。',
        'scene.deep.title': '深度搜索',
        'scene.deep.desc': '营养代谢产物合成途径与调控网络深度分析，探索营养合成的更多可能性。',
        'scene.library.title': '个人知识库',
        'scene.library.desc': '支持个人文献上传功能，打造符合个人需求的定制化知识库。',
        // 知识库弹窗
        'kb.title': '我的知识库',
        'kb.uploadBtn': '+ 上传 PDF',
        'kb.uploading': '上传中...',
        'kb.parsing': '解析中...',
        'kb.uploadSuccess': '上传成功!',
        'kb.uploadFail': '上传失败',
        'kb.networkError': '网络错误',
        'kb.loadFail': '加载失败',
        'kb.empty': '暂无文件，点击上方按钮上传 PDF',
        'kb.confirmDelete': '确定删除 "{0}" 吗？',
        'kb.renamePrompt': '输入新文件名：',
        'kb.renameFail': '重命名失败',
        'kb.deleteFail': '删除失败',
        'kb.pages': '页',
        'kb.chunks': '块',
        'kb.rename': '重命名',
        'kb.delete': '删除',
        // 认证
        'auth.login': '登录',
        'auth.signup': '注册',
        'auth.loginBtn': '登 录',
        'auth.signupBtn': '注 册',
        'auth.emailPlaceholder': '邮箱地址',
        'auth.passwordPlaceholder': '密码',
        'auth.signupPasswordPlaceholder': '密码（至少6位）',
        'auth.confirmPasswordPlaceholder': '确认密码',
        'auth.nicknamePlaceholder': '昵称（选填）',
        'auth.avatarPlaceholder': '头像',
        'auth.chooseAvatar': '选择头像',
        'auth.expired': '登录已过期，请重新登录',
        'auth.networkFail': '网络请求失败',
        'auth.tagline': '智能植物营养基因问答平台',
        'auth.feature1': '基因数据库语义检索',
        'auth.feature2': 'PubMed 文献联网搜索',
        'auth.feature3': '个人知识库管理',
        'auth.feature4': 'AI 深度搜索报告',
        // 管理后台
        'header.admin': '管理后台',
        'auth.logout': '退出登录',
        // Skills 管理
        'sidebar.skills': 'Skills 管理',
        'skill.title': 'Skills 管理',
        'skill.create': '+ 新建 Skill',
        'skill.save': '保存',
        'skill.delete': '删除',
        'skill.shared': '共享',
        'skill.private': '私有',
        'skill.mode_must': '必选',
        'skill.mode_auto': '自动',
        'skill.mode_disabled': '禁用',
        'skill.availableTools': '可用 Tools',
        'skill.saveFail': '保存失败',
        'skill.deleteFail': '删除失败',
        'skill.confirmDelete': '确定删除 Skill "{0}" 吗？',
        // 模型选择
        'model.select': '选择模型',
        // SOP NCBI 验证
        'sop.extracting': '正在从对话中提取基因...',
        'sop.ncbiVerifying': '正在 NCBI 确认基因信息...',
        'sop.confirm': '确认执行',
        'sop.cancel': '取消',
        'sop.ncbiFound': '已确认',
        'sop.ncbiNotFound': '未找到',
        // 个人资料
        'profile.changeAvatar': '更换头像',
        'profile.save': '保存',
        'profile.saving': '保存中...',
        'profile.saveFail': '保存失败',
        'profile.deleteAccount': '注销账号',
        'profile.confirmDelete': '确定要永久删除账号吗？此操作不可恢复！',
        'profile.deleteFail': '删除失败',
        // 工具调用
        'tool.calling': '正在调用 {0}...',
        'tool.used': '使用了',
        'tool.skill': '技能',
        'tool.details': '详情',
        // 思考过程
        'thinking.streaming': '思考中...',
        'thinking.done': '思考过程',
        // 搜索进度
        'search.searching': '正在搜索...',
        'search.deepSearching': '深度搜索中，正在检索更多文献并生成综合报告...',
        // 来源
        'source.title': '📚 参考来源',
        'source.gene_db': '基因库',
        'source.pubmed': 'PubMed',
        'source.jina_web': '网页',
        'source.personal': '个人库',
        // 生成控制
        'generation.stopped': '已停止生成',
        'scroll.toBottom': '回到底部',
        'feedback.prompt': '这个回答对你有帮助吗？',
        'feedback.up': '有帮助',
        'feedback.down': '需改进',
        'feedback.thanks': '感谢反馈',
        'feedback.failed': '反馈提交失败',
        // 其他
        'error.prefix': '错误',
        'history.oldRecord': '（旧记录，无回答内容）',
        'confirm.clearHistory': '确定要清空所有历史记录吗？',
        // 基因编辑器
        'gene.detected': '检测到以下可编辑基因：',
        'gene.addBtn': '+ 添加基因',
        'gene.inputPlaceholder': '输入基因名',
        'gene.removeTitle': '移除此基因',
        // 欢迎标语
        'slogans': [
            "今天你想知道点什么？",
            "探索植物代谢的奥秘",
            "有什么关于营养合成的问题吗？",
            "让我帮你找到答案",
            "开始你的科研探索之旅",
            "营养代谢的秘密，等你来发现",
            "想了解哪些营养代谢基因的功能？",
            "营养合成，从这里开始",
            "科学问答，随时为你服务",
            "深入了解植物营养代谢调控",
            "你的科研助手已就绪",
            "问我关于营养合成的任何问题",
            "发现植物代谢的精彩世界",
            "让数据为你揭示真相",
            "营养合成，一问便知",
            "今天想研究什么营养代谢通路？",
            "文献中的答案，触手可及",
            "从问题到洞察，只需一步",
            "你的问题，我的使命",
            "科研路上，有我相伴"
        ],
    },
    en: {
        'lang.btn': 'English',
        'sidebar.history': 'Chat History',
        'sidebar.viewAll': 'View all...',
        'sidebar.noHistory': 'No history yet',
        'sidebar.myLibrary': 'My Library',
        'header.newChat': '+ New Chat',
        'input.placeholder': 'Describe your needs, e.g.: How to increase soybean flavonoid content via gene editing',
        'input.personal': 'Personal',
        'input.depth': 'Deep Research',
        'scene.eyebrow': 'Research Modes',
        'scene.category': 'Research Scenarios',
        'scene.subtitle': 'Build a smarter entry point for plant nutrient metabolism research around literature evidence, regulatory networks, and your own curated knowledge.',
        'scene.retrieval.title': 'Retrieval Augmentation',
        'scene.retrieval.desc': 'Every answer is grounded in real literature, strengthening the research foundation for nutrient metabolism studies.',
        'scene.deep.title': 'Deep Search',
        'scene.deep.desc': 'Dive into biosynthetic pathways and regulatory networks of nutrient metabolites to uncover more possibilities for nutrient formation.',
        'scene.library.title': 'Personal Library',
        'scene.library.desc': 'Upload your own literature and build a customized knowledge base tailored to your research needs.',
        'kb.title': 'My Library',
        'kb.uploadBtn': '+ Upload PDF',
        'kb.uploading': 'Uploading...',
        'kb.parsing': 'Parsing...',
        'kb.uploadSuccess': 'Upload successful!',
        'kb.uploadFail': 'Upload failed',
        'kb.networkError': 'Network error',
        'kb.loadFail': 'Failed to load',
        'kb.empty': 'No files yet. Click the button above to upload a PDF.',
        'kb.confirmDelete': 'Delete "{0}"?',
        'kb.renamePrompt': 'Enter new filename:',
        'kb.renameFail': 'Rename failed',
        'kb.deleteFail': 'Delete failed',
        'kb.pages': 'pages',
        'kb.chunks': 'chunks',
        'kb.rename': 'Rename',
        'kb.delete': 'Delete',
        'auth.login': 'Login',
        'auth.signup': 'Sign Up',
        'auth.loginBtn': 'Login',
        'auth.signupBtn': 'Sign Up',
        'auth.emailPlaceholder': 'Email address',
        'auth.passwordPlaceholder': 'Password',
        'auth.signupPasswordPlaceholder': 'Password (min 6 chars)',
        'auth.confirmPasswordPlaceholder': 'Confirm password',
        'auth.nicknamePlaceholder': 'Nickname (optional)',
        'auth.avatarPlaceholder': 'Avatar',
        'auth.chooseAvatar': 'Choose Avatar',
        'auth.expired': 'Session expired, please log in again',
        'auth.networkFail': 'Network request failed',
        'auth.tagline': 'Intelligent Plant Nutrient Gene Q&A Platform',
        'auth.feature1': 'Semantic gene database retrieval',
        'auth.feature2': 'PubMed literature online search',
        'auth.feature3': 'Personal library management',
        'auth.feature4': 'AI deep search reports',
        'header.admin': 'Admin Panel',
        'auth.logout': 'Log out',
        // Skills
        'sidebar.skills': 'Skills',
        'skill.title': 'Skills Management',
        'skill.create': '+ New Skill',
        'skill.save': 'Save',
        'skill.delete': 'Delete',
        'skill.shared': 'Shared',
        'skill.private': 'Private',
        'skill.mode_must': 'Required',
        'skill.mode_auto': 'Auto',
        'skill.mode_disabled': 'Disabled',
        'skill.availableTools': 'Available Tools',
        'skill.saveFail': 'Save failed',
        'skill.deleteFail': 'Delete failed',
        'skill.confirmDelete': 'Delete skill "{0}"?',
        // Model
        'model.select': 'Select Model',
        // SOP
        'sop.extracting': 'Extracting genes from conversation...',
        'sop.ncbiVerifying': 'Verifying genes on NCBI...',
        'sop.confirm': 'Confirm & Run',
        'sop.cancel': 'Cancel',
        'sop.ncbiFound': 'Confirmed',
        'sop.ncbiNotFound': 'Not found',
        'profile.changeAvatar': 'Change Avatar',
        'profile.save': 'Save',
        'profile.saving': 'Saving...',
        'profile.saveFail': 'Save failed',
        'profile.deleteAccount': 'Delete Account',
        'profile.confirmDelete': 'Permanently delete your account? This cannot be undone!',
        'profile.deleteFail': 'Delete failed',
        // Tool calls
        'tool.calling': 'Calling {0}...',
        'tool.used': 'Used',
        'tool.skill': 'Skill',
        'tool.details': 'Details',
        // Thinking
        'thinking.streaming': 'Thinking...',
        'thinking.done': 'Thinking process',
        'search.searching': 'Searching...',
        'search.deepSearching': 'Deep search: retrieving more literature and generating a comprehensive report...',
        'source.title': '📚 References',
        'source.gene_db': 'Gene DB',
        'source.pubmed': 'PubMed',
        'source.jina_web': 'Web',
        'source.personal': 'Personal',
        'generation.stopped': 'Generation stopped',
        'scroll.toBottom': 'Scroll to bottom',
        'feedback.prompt': 'Was this answer helpful?',
        'feedback.up': 'Helpful',
        'feedback.down': 'Needs work',
        'feedback.thanks': 'Thanks for the feedback',
        'feedback.failed': 'Failed to submit feedback',
        'error.prefix': 'Error',
        'history.oldRecord': '(Old record, no answer)',
        'confirm.clearHistory': 'Clear all history?',
        // Gene editor
        'gene.detected': 'Detected editable genes:',
        'gene.addBtn': '+ Add gene',
        'gene.inputPlaceholder': 'Gene name',
        'gene.removeTitle': 'Remove this gene',
        'slogans': [
            "What would you like to discover today?",
            "Unlock the secrets of plant metabolism.",
            "Have a question about nutritional biosynthesis?",
            "I'll help you find the answer.",
            "Start your research journey now.",
            "Discover the hidden logic of nutrient metabolism.",
            "Which nutrient-metabolism genes would you like to explore?",
            "Start with nutritional biosynthesis.",
            "Scientific answers, on demand.",
            "Explore how plant nutrient metabolism is regulated.",
            "Your AI research assistant is ready.",
            "Ask me anything about nutritional biosynthesis.",
            "Discover the world of plant metabolism.",
            "Let data uncover the truth.",
            "Nutritional biosynthesis, made easy.",
            "Which nutrient-metabolism pathway would you like to study today?",
            "Literature-backed answers, at your fingertips.",
            "From question to insight, in one step.",
            "Your question, my purpose.",
            "Your research partner, every step of the way."
        ],
    },
};

/** Get translated string. Supports {0} placeholders. */
function t(key, ...args) {
    let str = I18N[currentLang]?.[key] ?? I18N['zh'][key] ?? key;
    args.forEach((arg, i) => { str = str.replace(`{${i}}`, arg); });
    return str;
}

/** Apply current language to all elements with data-i18n / data-i18n-placeholder */
function applyLanguage() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        el.textContent = t(key);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = t(key);
    });
    // Language button shows the *current* language name
    const langBtn = document.getElementById('lang-btn');
    if (langBtn) langBtn.textContent = t('lang.btn');
    // Update html lang
    document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
    // Refresh slogan
    setRandomSlogan();
}

function toggleLanguage() {
    currentLang = currentLang === 'zh' ? 'en' : 'zh';
    localStorage.setItem('lang', currentLang);
    applyLanguage();
    // Re-render history list (contains translated empty text)
    loadHistory();
}

// ===== 全局状态 =====
let isQuerying = false;
let conversationHistory = [];  // [{id, title, messages: [{query, answerText, sources, timestamp}], timestamp}]
let currentSessionId = null;   // 当前对话的 session ID
let hasStartedConversation = false;
let isDeepSearch = false;
let usePersonal = false;
const assistantTurnState = new Map();
let currentAbortController = null;  // 用于中断正在进行的 SSE 流
let currentStreamMsgId = null;      // 正在流式生成的 assistant 消息 ID
let userHasScrolled = false;        // 用户是否手动向上滚动（禁止自动跟随）

// ===== Agent / Model 偏好（Skills 由后台文件夹维护，不再前端编辑）=====
let skillPrefs = {};
let toolOverrides = {};
let selectedModelId = localStorage.getItem('selectedModelId') || '';

function createSessionId() {
    return `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// 中断正在进行的 SSE 流，保存已有的部分回答
function abortCurrentStream() {
    if (!currentAbortController) return;
    const msgId = currentStreamMsgId;
    // 先保存部分回答到历史
    if (msgId) {
        const state = assistantTurnState.get(msgId);
        if (state && (state.answerText || (state.genes && state.genes.length > 0))) {
            saveToHistory(state.query, state.answerText || '', state.sources, state.genes, state.sops, state.interactionId, state.turnId, state.feedbackSubmitted);
        }
        // 清理 loading spinner，添加"已停止生成"提示
        const regions = getMessageRegions(msgId);
        if (regions.answerEl) {
            // 移除所有正在转圈的 search-progress
            regions.answerEl.querySelectorAll('.search-progress').forEach(el => el.remove());
            // 如果有部分回答，在末尾追加停止提示
            if (state && state.answerText) {
                regions.answerEl.innerHTML = formatAnswer(state.answerText, state.sources || [], msgId);
            }
            // 追加停止提示
            const stopHint = document.createElement('div');
            stopHint.className = 'generation-stopped';
            stopHint.textContent = t('generation.stopped');
            regions.answerEl.appendChild(stopHint);
        }
        // 折叠工具调用 UI
        finalizeToolCallsUI(msgId);
        // 追加已有的参考文献
        if (state && state.sources && state.sources.length > 0 && regions.extrasEl) {
            regions.extrasEl.insertAdjacentHTML('beforeend', renderReferences(state.sources, msgId));
        }
    }
    currentAbortController.abort();
    currentAbortController = null;
    currentStreamMsgId = null;
    isQuerying = false;
    setSendBtnToSend();
}

// DOM 元素
const chatContainer = document.getElementById('chat-container');
const queryInput = document.getElementById('query-input');
const sendBtn = document.getElementById('send-btn');
const historyList = document.getElementById('history-list');
const sceneIntro = document.getElementById('scene-intro');
const welcomeSection = document.getElementById('welcome-section');
const welcomeSlogan = document.getElementById('welcome-slogan');

// ===== 智能滚动：仅在用户未手动上滚时自动跟随 =====
function smartScroll() {
    if (!userHasScrolled) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
}

// 监听滚动事件：判断用户是否手动向上滚动，并控制"回到底部"按钮
chatContainer.addEventListener('scroll', () => {
    const threshold = 80; // 距离底部 80px 以内视为"在底部"
    const atBottom = chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight < threshold;
    userHasScrolled = !atBottom;
    // 显示/隐藏"回到底部"按钮
    const scrollBtn = document.getElementById('scroll-to-bottom-btn');
    if (scrollBtn) {
        scrollBtn.classList.toggle('visible', userHasScrolled && isQuerying);
    }
});

// ===== 发送/停止按钮状态切换 =====
function setSendBtnToStop() {
    sendBtn.disabled = false;
    sendBtn.textContent = '■';
    sendBtn.classList.add('stop-mode');
    sendBtn.title = '停止生成';
}

function setSendBtnToSend() {
    sendBtn.disabled = false;
    sendBtn.textContent = '➤';
    sendBtn.classList.remove('stop-mode');
    sendBtn.title = '';
    // 隐藏"回到底部"按钮
    const scrollBtn = document.getElementById('scroll-to-bottom-btn');
    if (scrollBtn) scrollBtn.classList.remove('visible');
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    try {
        loadHistory();
        setupEventListeners();
        setRandomSlogan();
        setupKBModal();
        applyLanguage();
        initModelSelect();

        // ===== 登录功能初始化 =====
        setupAuthListeners();
        initAuth();
    } catch (err) {
        console.error('前端初始化失败:', err);
        if (typeof showLoginOverlay === 'function') {
            showLoginOverlay();
        }
        if (typeof showAuthError === 'function') {
            showAuthError(err.message || '页面初始化失败');
        }
    }
});

// 设置随机标语
function setRandomSlogan() {
    const slogans = I18N[currentLang]?.slogans || I18N['zh'].slogans;
    const randomIndex = Math.floor(Math.random() * slogans.length);
    if (welcomeSlogan) {
        welcomeSlogan.textContent = slogans[randomIndex];
    }
}

// 设置事件监听
function setupEventListeners() {
    if (!sendBtn || !queryInput) {
        throw new Error('关键输入组件未找到');
    }

    sendBtn.addEventListener('click', handleSend);

    // "回到底部"按钮
    const scrollToBottomBtn = document.getElementById('scroll-to-bottom-btn');
    if (scrollToBottomBtn) {
        scrollToBottomBtn.addEventListener('click', () => {
            userHasScrolled = false;
            chatContainer.scrollTop = chatContainer.scrollHeight;
            scrollToBottomBtn.classList.remove('visible');
        });
    }

    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', startNewChat);
    }

    queryInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    queryInput.addEventListener('input', () => {
        queryInput.style.height = 'auto';
        queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + 'px';
    });

    // 深度搜索按钮切换
    const depthBtn = document.getElementById('depth-btn');
    if (depthBtn) {
        depthBtn.addEventListener('click', () => {
            isDeepSearch = !isDeepSearch;
            depthBtn.classList.toggle('active', isDeepSearch);
        });
    }

    // 个人库按钮切换
    const personalBtn = document.getElementById('personal-btn');
    if (personalBtn) {
        personalBtn.addEventListener('click', () => {
            usePersonal = !usePersonal;
            personalBtn.classList.toggle('active', usePersonal);
        });
    }

    // 语言切换按钮
    const langBtn = document.getElementById('lang-btn');
    if (langBtn) {
        langBtn.addEventListener('click', toggleLanguage);
    }

    // 侧边栏折叠按钮
    const menuBtn = document.querySelector('.menu-btn');
    if (menuBtn) {
        menuBtn.addEventListener('click', () => {
            document.querySelector('.sidebar').classList.toggle('collapsed');
        });
    }
}

// ===== 知识库模态窗口 =====
function setupKBModal() {
    const toggle = document.getElementById('kb-toggle');
    const overlay = document.getElementById('kb-modal-overlay');
    const closeBtn = document.getElementById('kb-modal-close');
    const uploadBtn = document.getElementById('kb-upload-btn');
    const fileInput = document.getElementById('kb-file-input');

    if (!toggle || !overlay) return;

    toggle.addEventListener('click', () => {
        overlay.style.display = 'flex';
        loadPersonalFiles();
    });

    if (!closeBtn || !uploadBtn || !fileInput) {
        return;
    }

    closeBtn.addEventListener('click', () => {
        overlay.style.display = 'none';
    });

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.style.display = 'none';
        }
    });

    uploadBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', async () => {
        const file = fileInput.files[0];
        if (!file) return;
        await uploadPDF(file);
        fileInput.value = '';
    });
}

// 加载个人库文件列表
async function loadPersonalFiles() {
    const listEl = document.getElementById('kb-file-list');
    const badgeEl = document.getElementById('kb-file-count');
    if (!listEl) return;

    try {
        const resp = await fetch('/api/personal/files', {
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });
        if (!resp.ok) {
            listEl.innerHTML = `<div class="kb-empty">${t('kb.loadFail')}</div>`;
            return;
        }
        const data = await resp.json();
        const files = data.files || [];

        if (badgeEl) {
            if (files.length > 0) {
                badgeEl.textContent = files.length;
                badgeEl.style.display = 'inline-block';
            } else {
                badgeEl.style.display = 'none';
            }
        }

        if (files.length === 0) {
            listEl.innerHTML = `<div class="kb-empty">${t('kb.empty')}</div>`;
            return;
        }

        listEl.innerHTML = files.map(f => `
            <div class="kb-file-item" data-filename="${escapeHtml(f.filename)}">
                <div class="kb-file-info">
                    <div class="kb-file-name">${escapeHtml(f.filename)}</div>
                    <div class="kb-file-meta">${f.num_pages} ${t('kb.pages')} · ${f.num_chunks} ${t('kb.chunks')} · ${f.size_mb}MB · ${f.upload_time}</div>
                </div>
                <div class="kb-file-actions">
                    <button class="kb-action-btn kb-rename-btn" title="${t('kb.rename')}" onclick="renameFile('${escapeHtml(f.filename)}')">✏️</button>
                    <button class="kb-action-btn kb-delete-btn" title="${t('kb.delete')}" onclick="deleteFile('${escapeHtml(f.filename)}')">🗑️</button>
                </div>
            </div>
        `).join('');

    } catch (e) {
        listEl.innerHTML = `<div class="kb-empty">${t('kb.loadFail')}</div>`;
    }
}

// 上传 PDF
async function uploadPDF(file) {
    const progressEl = document.getElementById('kb-upload-progress');
    const barEl = document.getElementById('kb-upload-progress-bar');
    const statusEl = document.getElementById('kb-upload-status');

    progressEl.style.display = 'flex';
    barEl.style.width = '30%';
    statusEl.textContent = t('kb.uploading');

    const formData = new FormData();
    formData.append('file', file);

    try {
        barEl.style.width = '60%';
        statusEl.textContent = t('kb.parsing');

        const resp = await fetch('/api/personal/upload', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
            body: formData,
        });

        const data = await resp.json();
        if (!resp.ok) {
            barEl.style.width = '100%';
            barEl.style.background = '#E74C3C';
            statusEl.textContent = data.error || t('kb.uploadFail');
            setTimeout(() => { progressEl.style.display = 'none'; barEl.style.background = ''; }, 3000);
            return;
        }

        barEl.style.width = '100%';
        statusEl.textContent = t('kb.uploadSuccess');
        setTimeout(() => { progressEl.style.display = 'none'; }, 1500);

        loadPersonalFiles();

    } catch (e) {
        barEl.style.width = '100%';
        barEl.style.background = '#E74C3C';
        statusEl.textContent = t('kb.networkError');
        setTimeout(() => { progressEl.style.display = 'none'; barEl.style.background = ''; }, 3000);
    }
}

// 删除文件
async function deleteFile(filename) {
    if (!confirm(t('kb.confirmDelete', filename))) return;

    try {
        const resp = await fetch(`/api/personal/files/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });
        if (resp.ok) {
            loadPersonalFiles();
        } else {
            const data = await resp.json();
            alert(data.error || t('kb.deleteFail'));
        }
    } catch (e) {
        alert(t('kb.networkError'));
    }
}

// 重命名文件
async function renameFile(filename) {
    const newName = prompt(t('kb.renamePrompt'), filename);
    if (!newName || newName === filename) return;

    try {
        const resp = await fetch(`/api/personal/files/${encodeURIComponent(filename)}/rename`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + getAccessToken(),
            },
            body: JSON.stringify({ new_name: newName }),
        });
        if (resp.ok) {
            loadPersonalFiles();
        } else {
            const data = await resp.json();
            alert(data.error || t('kb.renameFail'));
        }
    } catch (e) {
        alert(t('kb.networkError'));
    }
}

// 开始新对话
function startNewChat() {
    abortCurrentStream();
    chatContainer.innerHTML = '';
    hasStartedConversation = false;
    currentSessionId = null;

    if (welcomeSection) welcomeSection.classList.remove('hidden');
    if (sceneIntro) sceneIntro.classList.remove('hidden');

    setRandomSlogan();

    queryInput.value = '';
    queryInput.style.height = 'auto';
    queryInput.focus();

    isDeepSearch = false;
    usePersonal = false;
    const depthBtn = document.getElementById('depth-btn');
    const personalBtn = document.getElementById('personal-btn');
    if (depthBtn) depthBtn.classList.remove('active');
    if (personalBtn) personalBtn.classList.remove('active');

    // 取消侧边栏高亮
    document.querySelectorAll('.history-item.active').forEach(el => el.classList.remove('active'));
}

// 处理发送
async function handleSend() {
    // 如果正在生成中，点击按钮 = 停止生成
    if (isQuerying) {
        abortCurrentStream();
        return;
    }

    const query = queryInput.value.trim();
    if (!query) return;

    if (!hasStartedConversation) {
        hideWelcomeAndScene();
        hasStartedConversation = true;
    }
    if (!currentSessionId) {
        currentSessionId = createSessionId();
    }

    addMessage('user', query);

    queryInput.value = '';
    queryInput.style.height = 'auto';

    isQuerying = true;
    setSendBtnToStop();
    userHasScrolled = false; // 新查询开始时重置滚动状态

    const loadingText = isDeepSearch ? t('search.deepSearching') : t('search.searching');
    const loadingHtml = `<div class="search-progress"><span class="search-spinner"></span>${escapeHtml(loadingText)}</div>`;
    const assistantMsgId = addMessage('assistant', loadingHtml);
    assistantTurnState.set(assistantMsgId, {
        query,
        answerText: '',
        sources: [],
        toolCalls: [],          // 工具调用记录
        selectedSkill: null,    // 命中的 skill
        experimentRunning: false,
        experimentDone: false,
        genesAvailable: false,
        genes: [],  // 检测到的基因名列表（由后端 genes_available 事件填充）
        streamDone: false, // 标记流式传输是否完成，防止后续 updateMessage 覆盖按钮
        interactionId: '',
        turnId: '',
        feedbackSubmitted: '',
    });

    try {
        const { answerText, sources } = await streamQuery(query, assistantMsgId);
        const state = getAssistantTurnState(assistantMsgId);
        saveToHistory(query, answerText, sources, state.genes, state.sops, state.interactionId, state.turnId, state.feedbackSubmitted);
    } catch (error) {
        if (error.name === 'AbortError') {
            // 用户切换了对话，部分回答已在 abortCurrentStream() 中保存
        } else {
            updateMessage(assistantMsgId, `<p style="color: red;">${t('error.prefix')}: ${error.message}</p>`);
        }
    } finally {
        isQuerying = false;
        setSendBtnToSend();
    }
}

// 隐藏欢迎标语和场景介绍
function hideWelcomeAndScene() {
    if (welcomeSection) welcomeSection.classList.add('hidden');
    if (sceneIntro) sceneIntro.classList.add('hidden');
}

// 构建当前 session 的多轮历史（不含当前提问）
// 策略：保留第 1 轮（初始上下文）+ 最近 2 轮（最新讨论），避免 token 过长
function buildHistory() {
    if (!currentSessionId) return [];
    const session = conversationHistory.find(s => s.id === currentSessionId);
    if (!session || !session.messages.length) return [];

    const all = session.messages;
    let selected;
    if (all.length <= 3) {
        // 3 轮以内全部保留
        selected = all;
    } else {
        // 第 1 轮 + 最近 2 轮
        selected = [all[0], ...all.slice(-2)];
    }

    const history = [];
    for (const turn of selected) {
        history.push({ role: 'user', content: turn.query });
        if (turn.answerText) {
            history.push({ role: 'assistant', content: turn.answerText });
        }
    }
    return history;
}

// 流式查询
async function streamQuery(query, messageId) {
    const history = buildHistory();
    const abortController = new AbortController();
    currentAbortController = abortController;
    currentStreamMsgId = messageId;
    const response = await fetch('/api/query', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + getAccessToken(),
        },
        body: JSON.stringify({
            query,
            use_personal: usePersonal,
            use_depth: isDeepSearch,
            history,
            skill_prefs: skillPrefs,
            tool_overrides: toolOverrides,
            model_id: selectedModelId,
            session_id: currentSessionId || '',
            client_turn_id: messageId,
            capture_consent: true,
        }),
        signal: abortController.signal,
    });

    if (response.status === 401) {
        showLoginOverlay();
        throw new Error(t('auth.expired'));
    }

    if (!response.ok) {
        throw new Error(t('auth.networkFail'));
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    let answerText = '';
    let sources = [];
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

        const lines = buffer.split('\n');
        buffer = done ? '' : (lines.pop() || '');

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    const data = JSON.parse(line.slice(6));

                    if (data.type === 'capture') {
                        const state = getAssistantTurnState(messageId);
                        state.captureEnabled = !!data.enabled;
                        state.interactionId = data.interaction_id || '';
                        state.turnId = data.turn_id || '';
                    } else if (data.type === 'tools_enabled') {
                        const state = getAssistantTurnState(messageId);
                        state.enabledTools = data.tools;
                    } else if (data.type === 'skill_selected') {
                        const state = getAssistantTurnState(messageId);
                        state.selectedSkill = data.data;
                        updateToolCallsUI(messageId);
                    } else if (data.type === 'thinking') {
                        const state = getAssistantTurnState(messageId);
                        state.thinkingText += data.data || '';
                        updateThinkingUI(messageId, state.thinkingText, true);
                    } else if (data.type === 'tool_call') {
                        const state = getAssistantTurnState(messageId);
                        state.toolCalls.push({ tool: data.tool, args: data.args, done: false });
                        updateToolCallsUI(messageId);
                    } else if (data.type === 'tool_result') {
                        const state = getAssistantTurnState(messageId);
                        const tc = state.toolCalls.find(c => c.tool === data.tool && !c.done);
                        if (tc) {
                            tc.done = true;
                            tc.summary = data.summary || '';
                            tc.result = data.content || data.summary || '';
                        }
                        updateToolCallsUI(messageId);
                    } else if (data.type === 'searching') {
                        updateMessage(messageId,
                            `<div class="search-progress"><span class="search-spinner"></span>${escapeHtml(data.data)}</div>`);
                    } else if (data.type === 'citations') {
                        sources = data.data;
                        const state = getAssistantTurnState(messageId);
                        state.sources = sources;
                    } else if (data.type === 'sources') {
                        sources = data.data;
                        const state = getAssistantTurnState(messageId);
                        state.sources = sources;
                    } else if (data.type === 'text') {
                        // 收到正式回答后，结束思考状态
                        finalizeThinkingUI(messageId);
                        answerText += data.data;
                        const state = getAssistantTurnState(messageId);
                        state.answerText = answerText;
                        updateMessage(messageId, formatAnswer(answerText));
                    } else if (data.type === 'experiment_start') {
                        const state = getAssistantTurnState(messageId);
                        state.experimentRunning = true;
                        updateMessage(messageId, formatAnswer(answerText));
                        removeExperimentButton(messageId);
                        appendExperimentProgress(messageId, data.genes);
                    } else if (data.type === 'progress') {
                        updateExperimentProgress(messageId, data);
                    } else if (data.type === 'result' && data.sops) {
                        const state = getAssistantTurnState(messageId);
                        state.experimentDone = true;
                        state.experimentRunning = false;
                        state.sops = data.sops;
                        const msgEl = document.getElementById(messageId);
                        if (msgEl) {
                            const regions = getMessageRegions(messageId);
                            if (regions.extrasEl) renderSOPs(regions.extrasEl, data.sops);
                        }
                    } else if (data.type === 'genes_available') {
                        // 后端检测到回答中包含具体基因名，携带基因名列表
                        const state = getAssistantTurnState(messageId);
                        state.genesAvailable = true;
                        // 保存基因名列表（如 ["GmFAD2", "AtMYB4"]），供基因编辑器使用
                        if (data.genes && Array.isArray(data.genes)) {
                            state.genes = data.genes;
                        }
                    } else if (data.type === 'done') {
                        const state = getAssistantTurnState(messageId);
                        state.streamDone = true;
                        finalizeThinkingUI(messageId);
                        finalizeToolCallsUI(messageId);

                        updateMessage(messageId, formatAnswer(answerText, sources, messageId));
                        const { extrasEl } = getMessageRegions(messageId);
                        if (extrasEl && sources && sources.length > 0) {
                            extrasEl.insertAdjacentHTML('beforeend', renderReferences(sources, messageId));
                        }
                        renderExperimentButton(messageId);
                        renderFeedbackControls(messageId);
                    } else if (data.type === 'error') {
                        const state = getAssistantTurnState(messageId);
                        state.experimentRunning = false;
                        if (data.msg) {
                            // pipeline error — show in progress area
                            handleExperimentError(messageId, data.msg);
                            renderExperimentButton(messageId);
                        } else {
                            throw new Error(data.data);
                        }
                    }
                } catch (parseErr) {
                    if (parseErr.message && !parseErr.message.startsWith('Unexpected')) {
                        throw parseErr;
                    }
                }
            }
        }

        if (done) {
            if (buffer.trim().startsWith('data: ')) {
                try {
                    const data = JSON.parse(buffer.trim().slice(6));
                    if (data.type === 'done') {
                        const state = getAssistantTurnState(messageId);
                        state.streamDone = true;
                        finalizeToolCallsUI(messageId);
                        updateMessage(messageId, formatAnswer(answerText, sources, messageId));
                        // 在回答末尾追加参考文献
                        const { extrasEl } = getMessageRegions(messageId);
                        if (extrasEl && sources && sources.length > 0) {
                            extrasEl.insertAdjacentHTML('beforeend', renderReferences(sources, messageId));
                        }
                        renderExperimentButton(messageId);
                        renderFeedbackControls(messageId);
                    }
                } catch (parseErr) {
                    if (parseErr.message && !parseErr.message.startsWith('Unexpected')) {
                        throw parseErr;
                    }
                }
            }
            break;
        }
    }

    currentAbortController = null;
    currentStreamMsgId = null;
    return { answerText, sources };
}

async function streamExperiment(messageId) {
    const state = getAssistantTurnState(messageId);

    // ---- Phase A+B: 提取基因 + NCBI 验证 ----
    const extractResp = await fetch('/api/experiment/preview', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + getAccessToken(),
        },
        body: JSON.stringify({
            goal: `${state.query || ''}\n${state.answerText || ''}`.trim(),
            selected_gene_names: state.genes || [],
        }),
    });

    if (extractResp.status === 401) {
        showLoginOverlay();
        throw new Error(t('auth.expired'));
    }
    if (!extractResp.ok) {
        const err = await extractResp.json().catch(() => ({}));
        throw new Error(err.detail || t('auth.networkFail'));
    }

    // 显示提取中进度
    const { extrasEl } = getMessageRegions(messageId);
    removeExperimentButton(messageId);

    const sopProgressEl = document.createElement('div');
    sopProgressEl.className = 'sop-extract-progress';
    sopProgressEl.innerHTML = `<div class="search-progress"><span class="search-spinner"></span>${t('sop.extracting')}</div>`;
    if (extrasEl) extrasEl.appendChild(sopProgressEl);

    const preview = await extractResp.json();
    const verifiedGenes = preview.genes || [];
    sopProgressEl.remove();

    if (!verifiedGenes || verifiedGenes.length === 0) {
        renderExperimentButton(messageId);
        return;
    }

    // ---- 展示 NCBI 验证结果，等待用户确认 ----
    const confirmed = await showNCBIVerification(messageId, verifiedGenes, extrasEl);
    if (!confirmed) {
        renderExperimentButton(messageId);
        return;
    }

    // ---- Phase C: 用户确认后执行 SOP pipeline ----
    state.experimentRunning = true;
    state.experimentDone = false;
    appendExperimentProgress(messageId, confirmed.map(g => g.gene));

    const runResp = await fetch('/api/experiment/run', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + getAccessToken(),
        },
        body: JSON.stringify({ genes: confirmed }),
    });

    if (!runResp.ok) {
        const err = await runResp.json().catch(() => ({}));
        throw new Error(err.detail || t('auth.networkFail'));
    }

    const runReader = runResp.body.getReader();
    const runDecoder = new TextDecoder();
    let runBuffer = '';

    while (true) {
        const { done, value } = await runReader.read();
        runBuffer += runDecoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = runBuffer.split('\n');
        runBuffer = done ? '' : (lines.pop() || '');

        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
                const data = JSON.parse(line.slice(6));
                if (data.type === 'experiment_start') {
                    // Already showing progress
                } else if (data.type === 'progress') {
                    updateExperimentProgress(messageId, data);
                } else if (data.type === 'result' && data.sops) {
                    state.experimentDone = true;
                    state.experimentRunning = false;
                    state.sops = data.sops;
                    const regions = getMessageRegions(messageId);
                    if (regions.extrasEl) renderSOPs(regions.extrasEl, data.sops);
                    updateLastTurnSops(state.sops, state.genes);
                } else if (data.type === 'error') {
                    state.experimentRunning = false;
                    handleExperimentError(messageId, data.msg || data.data || t('auth.networkFail'));
                    renderExperimentButton(messageId);
                }
            } catch (e) {
                if (e.message && !e.message.startsWith('Unexpected')) throw e;
            }
        }
        if (done) break;
    }
}

/**
 * 展示 NCBI 验证结果表格，返回 Promise<gene[]|null>
 * 用户点"确认执行"resolve 为基因列表，点"取消"resolve 为 null
 */
function showNCBIVerification(messageId, genes, container) {
    return new Promise((resolve) => {
        const verifyEl = document.createElement('div');
        verifyEl.className = 'ncbi-verify-section';

        const rows = genes.map(g => {
            const statusCls = g.ncbi_found ? 'ncbi-found' : 'ncbi-not-found';
            const statusText = g.ncbi_found ? t('sop.ncbiFound') : t('sop.ncbiNotFound');
            const icon = g.ncbi_found ? '✓' : '✗';
            return `<tr class="${statusCls}">
                <td>${escapeHtml(g.gene)}</td>
                <td>${escapeHtml(g.species)}</td>
                <td><span class="ncbi-status-icon">${icon}</span> ${statusText}</td>
            </tr>`;
        }).join('');

        verifyEl.innerHTML = `
            <div class="ncbi-verify-title">🧬 NCBI 基因验证结果</div>
            <table class="ncbi-verify-table">
                <thead><tr><th>基因名</th><th>物种</th><th>NCBI</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div class="ncbi-verify-actions">
                <button class="ncbi-confirm-btn">${t('sop.confirm')}</button>
                <button class="ncbi-cancel-btn">${t('sop.cancel')}</button>
            </div>
        `;

        if (container) container.appendChild(verifyEl);

        verifyEl.querySelector('.ncbi-confirm-btn').addEventListener('click', () => {
            verifyEl.remove();
            resolve(genes.filter(g => g.ncbi_found));
        });

        verifyEl.querySelector('.ncbi-cancel-btn').addEventListener('click', () => {
            verifyEl.remove();
            resolve(null);
        });

        smartScroll();
    });
}

// 格式化答案（使用 marked.js 渲染 Markdown）
function formatAnswer(text, citations = [], citationScope = '') {
    const citationLinks = new Map();
    for (const citation of citations || []) {
        const num = Number(citation.tool_index || citation.source_id);
        if (!num) continue;
        const href = citationHref(citation);
        if (href) citationLinks.set(num, href);
    }
    const linkInlineCitations = (html) => {
        if (!citationLinks.size) return html;
        return html.replace(/\[(\d+)\]/g, (match, rawNum) => {
            const num = Number(rawNum);
            const href = citationLinks.get(num);
            if (!href) return match;
            return `<a class="citation-link" href="${escapeHtml(href)}" target="_blank" rel="noopener" title="打开参考文献 [${num}]">[${num}]</a>`;
        });
    };

    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,
            gfm: true,
        });
        const sanitized = DOMPurify.sanitize(marked.parse(text));
        return '<div class="markdown-body">' + linkInlineCitations(sanitized) + '</div>';
    }
    // fallback: 无 marked 时用简单段落
    const formatted = text.replace(
        /\[([^\]]+)\s*\|\s*([^\]]+)\]/g,
        '<span class="source-tag">$1 | $2</span>'
    );
    const paragraphs = formatted.split('\n\n').filter(p => p.trim());
    const html = paragraphs.map(p => `<p>${p}</p>`).join('');
    const safeHtml = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
    return linkInlineCitations(safeHtml);
}

function citationHref(citation) {
    const raw = citation.url || (
        citation.doi ? `https://doi.org/${String(citation.doi).trim()}` :
        (citation.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${String(citation.pmid).trim()}/` : '')
    );
    const href = String(raw || '').trim();
    return /^https?:\/\//i.test(href) ? href : '';
}

// 来源类型标签（动态取翻译）
function getSourceBadge(sourceType) {
    const map = {
        gene_db:  { key: 'source.gene_db',  cls: 'badge-gene' },
        pubmed:   { key: 'source.pubmed',    cls: 'badge-pubmed' },
        jina_web: { key: 'source.jina_web',  cls: 'badge-web' },
        personal: { key: 'source.personal',  cls: 'badge-personal' },
    };
    const entry = map[sourceType];
    if (entry) return { label: t(entry.key), cls: entry.cls };
    return { label: sourceType, cls: '' };
}

// 渲染来源列表（旧版带分数 — 保留但不再使用）
function renderSources(sources) {
    if (!sources || sources.length === 0) return '';

    const items = sources.slice(0, 15).map((s, i) => {
        const badge = getSourceBadge(s.source_type);
        let title = '';
        let detail = '';

        if (s.source_type === 'gene_db') {
            title = s.gene_name || '';
            detail = s.paper_title || '';
        } else if (s.source_type === 'pubmed') {
            title = s.paper_title || s.title || '';
            detail = s.pmid ? `PMID:${s.pmid}` : '';
        } else if (s.source_type === 'jina_web') {
            title = s.title || '';
            detail = s.url ? `<a href="${s.url}" target="_blank" rel="noopener">${s.url}</a>` : '';
        } else if (s.source_type === 'personal') {
            title = s.filename || '';
            detail = s.page ? `p.${s.page}` : '';
        } else {
            title = s.title || s.paper_title || '';
        }

        const scoreStr = (typeof s.score === 'number') ? s.score.toFixed(3) : '';

        return `
            <div class="source-item">
                <span class="source-badge ${badge.cls}">${badge.label}</span>
                <strong>${i + 1}. ${escapeHtml(title)}</strong>
                ${detail ? ` - ${detail}` : ''}
                ${scoreStr ? `<span style="color:#999;margin-left:6px;">(${scoreStr})</span>` : ''}
            </div>
        `;
    }).join('');

    return `
        <div class="sources-section">
            <div class="sources-title">${t('source.title')} (${sources.length}):</div>
            <div class="sources-list">${items}</div>
        </div>
    `;
}

// 渲染参考文献列表（仅文献名+链接，无基因名、无分数）
// sources 已经由后端按正文引用顺序过滤和排序
function renderReferences(sources, citationScope = '') {
    if (!sources || sources.length === 0) return '';

    // 用 Set 按文献名去重（保持顺序）
    const seen = new Set();
    const refs = [];

    for (const s of sources) {
        let literatureName = '';

        if (s.source_type === 'gene_db') {
            literatureName = s.paper_title || s.title || '';
        } else if (s.source_type === 'pubmed') {
            literatureName = s.paper_title || s.title || '';
        } else if (s.source_type === 'jina_web') {
            literatureName = s.title || '';
        } else if (s.source_type === 'personal') {
            literatureName = s.filename || s.title || '';
        } else {
            literatureName = s.title || s.paper_title || '';
        }

        if (!literatureName) continue;

        if (seen.has(literatureName)) continue;
        seen.add(literatureName);

        refs.push({ literatureName, url: s.url || '', doi: s.doi || '', pmid: s.pmid || '', toolIndex: s.tool_index || 0 });
    }

    if (refs.length === 0) return '';

    const scopePrefix = citationScope ? `${citationScope}-` : '';
    const items = refs.map((r, i) => {
        // 使用后端传来的 tool_index（与正文角标一致），fallback 到顺序编号
        const num = r.toolIndex || (i + 1);
        let litHtml = escapeHtml(r.literatureName);
        const href = r.url || (r.doi ? `https://doi.org/${r.doi}` : (r.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${r.pmid}/` : ''));
        if (href) {
            litHtml = `<a href="${href}" target="_blank" rel="noopener">${litHtml}</a>`;
        }
        return `<div class="ref-item" id="${scopePrefix}ref-${num}" data-citation="${num}">[${num}] ${litHtml}</div>`;
    }).join('');

    return `
        <div class="references-section">
            <div class="references-title">📖 参考文献</div>
            <div class="references-list">${items}</div>
        </div>
    `;
}

// 添加消息
function addMessage(role, content) {
    const messageId = `msg-${Date.now()}-${Math.random()}`;

    const messageDiv = document.createElement('div');
    messageDiv.className = `message message-${role}`;
    messageDiv.id = messageId;

    if (role === 'user') {
        messageDiv.innerHTML = `
            <div class="message-content">${escapeHtml(content)}</div>
        `;
    } else {
        messageDiv.innerHTML = `
            <div class="message-avatar">🧬</div>
            <div class="message-body">
                <div class="message-content">
                    <div class="tool-calls-summary" style="display:none"></div>
                    <div class="assistant-answer">${content}</div>
                    <div class="assistant-extras"></div>
                </div>
            </div>
        `;
    }

    chatContainer.appendChild(messageDiv);
    // 用户发送的消息始终滚动到底部；助手消息使用智能滚动
    if (role === 'user') {
        userHasScrolled = false;
        chatContainer.scrollTop = chatContainer.scrollHeight;
    } else {
        smartScroll();
    }

    return messageId;
}

// 更新消息
function updateMessage(messageId, content) {
    const messageEl = document.getElementById(messageId);
    if (messageEl) {
        const regions = getMessageRegions(messageId);
        if (regions.answerEl) {
            regions.answerEl.innerHTML = content;
            smartScroll();
        }
    }
}

function getAssistantTurnState(messageId) {
    if (!assistantTurnState.has(messageId)) {
        assistantTurnState.set(messageId, {
            query: '',
            answerText: '',
            sources: [],
            toolCalls: [],        // [{tool, args, done, summary, result}]
            selectedSkill: null,   // skill name string
            experimentRunning: false,
            experimentDone: false,
            genesAvailable: false,
            genes: [],
            streamDone: false,
            interactionId: '',
            turnId: '',
            feedbackSubmitted: '',
            thinkingText: '',     // 思考内容
        });
    }
    return assistantTurnState.get(messageId);
}

// ===== 工具调用 UI 辅助 =====

/** 实时更新工具调用状态（streaming 阶段） */
function updateToolCallsUI(messageId) {
    const msgEl = document.getElementById(messageId);
    if (!msgEl) return;
    const summaryEl = msgEl.querySelector('.tool-calls-summary');
    if (!summaryEl) return;

    const state = getAssistantTurnState(messageId);
    const { toolCalls, selectedSkill } = state;
    if (toolCalls.length === 0 && !selectedSkill) return;

    summaryEl.style.display = '';

    // Build real-time status lines
    let html = '<div class="tool-calls-live">';
    if (selectedSkill) {
        html += `<div class="tool-call-line"><span class="tool-badge tool-badge-skill">${t('tool.skill')}</span> ${escapeHtml(selectedSkill)}</div>`;
    }
    for (const tc of toolCalls) {
        if (tc.done) {
            html += `<div class="tool-call-line"><span class="tool-badge">✓ ${escapeHtml(tc.tool)}</span></div>`;
        } else {
            html += `<div class="tool-call-line"><span class="tool-badge tool-badge-active"><span class="search-spinner"></span> ${escapeHtml(tc.tool)}</span></div>`;
        }
    }
    html += '</div>';
    summaryEl.innerHTML = html;
}

/** 流结束后，把工具调用折叠成一行摘要 */
function finalizeToolCallsUI(messageId) {
    const msgEl = document.getElementById(messageId);
    if (!msgEl) return;
    const summaryEl = msgEl.querySelector('.tool-calls-summary');
    if (!summaryEl) return;

    const state = getAssistantTurnState(messageId);
    const { toolCalls, selectedSkill } = state;

    // No tool calls → hide the summary area
    if (toolCalls.length === 0 && !selectedSkill) {
        summaryEl.style.display = 'none';
        return;
    }

    summaryEl.style.display = '';

    // Deduplicate tool names for header
    const toolNames = [...new Set(toolCalls.map(tc => tc.tool))];
    let headerParts = [];
    if (toolNames.length > 0) {
        headerParts.push(`${t('tool.used')} ${toolNames.join(', ')}`);
    }
    if (selectedSkill) {
        headerParts.push(`${t('tool.skill')}: ${selectedSkill}`);
    }
    const headerText = headerParts.join(' | ');

    // Build detail section
    let detailHtml = '';
    for (const tc of toolCalls) {
        const argsStr = tc.args ? JSON.stringify(tc.args, null, 2) : '';
        const truncArgs = argsStr.length > 120 ? argsStr.slice(0, 120) + '...' : argsStr;
        detailHtml += `<div class="tool-call-item"><span class="tool-call-name">${escapeHtml(tc.tool)}</span>`;
        if (truncArgs) {
            detailHtml += ` <span class="tool-call-args">${escapeHtml(truncArgs)}</span>`;
        }
        if (tc.result) {
            detailHtml += `
                <details class="tool-call-result">
                    <summary>查看完整工具返回</summary>
                    <pre>${escapeHtml(tc.result)}</pre>
                </details>
            `;
        }
        detailHtml += `</div>`;
    }

    summaryEl.innerHTML = `
        <div class="tool-calls-header" onclick="this.parentElement.classList.toggle('expanded')">
            <span class="tool-calls-header-text">${escapeHtml(headerText)}</span>
            <span class="tool-calls-toggle">▸</span>
        </div>
        ${detailHtml ? `<div class="tool-calls-detail">${detailHtml}</div>` : ''}
    `;
}

// ===== 思考过程 UI =====

/** 实时更新思考内容（streaming 阶段） */
function updateThinkingUI(messageId, thinkingText, isStreaming = false) {
    const msgEl = document.getElementById(messageId);
    if (!msgEl) return;

    let thinkingContainer = msgEl.querySelector('.thinking-container');
    if (!thinkingContainer) {
        // 首次创建思考容器，插入到 tool-calls-summary 之后
        const summaryEl = msgEl.querySelector('.tool-calls-summary');
        if (!summaryEl) return;

        thinkingContainer = document.createElement('div');
        thinkingContainer.className = 'thinking-container';
        summaryEl.insertAdjacentElement('afterend', thinkingContainer);
    }

    const spinnerHtml = isStreaming ? '<span class="search-spinner"></span>' : '';
    const headerText = isStreaming ? t('thinking.streaming') : t('thinking.done');
    thinkingContainer.innerHTML = `
        <div class="thinking-header" onclick="this.parentElement.classList.toggle('expanded')">
            <span class="thinking-header-text">${spinnerHtml}💭 ${escapeHtml(headerText)}</span>
            <span class="thinking-toggle">▸</span>
        </div>
        <div class="thinking-content">
            <pre>${escapeHtml(thinkingText)}</pre>
        </div>
    `;
}

/** 结束思考状态（收到正式回答或 done 事件时） */
function finalizeThinkingUI(messageId) {
    const msgEl = document.getElementById(messageId);
    if (!msgEl) return;

    const thinkingContainer = msgEl.querySelector('.thinking-container');
    if (!thinkingContainer) return;

    const state = getAssistantTurnState(messageId);
    if (!state.thinkingText) {
        // 没有思考内容，移除容器
        thinkingContainer.remove();
        return;
    }

    // 更新为最终状态（移除 spinner）
    const headerEl = thinkingContainer.querySelector('.thinking-header-text');
    if (headerEl) {
        headerEl.innerHTML = `💭 ${escapeHtml(t('thinking.done'))}`;
    }
}

function getMessageRegions(messageId) {
    const messageEl = document.getElementById(messageId);
    if (!messageEl) return {};

    let answerEl = messageEl.querySelector('.assistant-answer');
    let extrasEl = messageEl.querySelector('.assistant-extras');

    if (!answerEl) {
        const contentEl = messageEl.querySelector('.message-content');
        if (!contentEl) return {};

        const existing = contentEl.innerHTML;
        contentEl.innerHTML = '<div class="assistant-answer"></div><div class="assistant-extras"></div>';
        answerEl = contentEl.querySelector('.assistant-answer');
        extrasEl = contentEl.querySelector('.assistant-extras');
        answerEl.innerHTML = existing;
    }

    return { messageEl, answerEl, extrasEl };
}

function renderFeedbackControls(messageId) {
    const state = getAssistantTurnState(messageId);
    if (!state.interactionId || state.feedbackSubmitted) return;
    const { extrasEl } = getMessageRegions(messageId);
    if (!extrasEl || extrasEl.querySelector('.feedback-controls')) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'feedback-controls';
    wrapper.innerHTML = `
        <span class="feedback-prompt">${escapeHtml(t('feedback.prompt'))}</span>
        <button class="feedback-btn" data-rating="up">${escapeHtml(t('feedback.up'))}</button>
        <button class="feedback-btn" data-rating="down">${escapeHtml(t('feedback.down'))}</button>
    `;
    wrapper.querySelectorAll('.feedback-btn').forEach(btn => {
        btn.addEventListener('click', () => submitFeedback(messageId, btn.dataset.rating));
    });
    extrasEl.appendChild(wrapper);
}

async function submitFeedback(messageId, rating) {
    const state = getAssistantTurnState(messageId);
    if (!state.interactionId || !rating || state.feedbackSubmitted) return;
    const { extrasEl } = getMessageRegions(messageId);
    try {
        const resp = await fetch('/api/feedback', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + getAccessToken(),
            },
            body: JSON.stringify({
                interaction_id: state.interactionId,
                session_id: currentSessionId || '',
                turn_id: state.turnId || messageId,
                rating,
            }),
        });
        if (!resp.ok) throw new Error(t('feedback.failed'));
        state.feedbackSubmitted = rating;
        updateLastTurnFeedback(state.interactionId, rating);
        const controls = extrasEl?.querySelector('.feedback-controls');
        if (controls) {
            controls.innerHTML = `<span class="feedback-thanks">${escapeHtml(t('feedback.thanks'))}</span>`;
        }
    } catch (err) {
        const controls = extrasEl?.querySelector('.feedback-controls');
        if (controls) {
            controls.classList.add('feedback-error');
            controls.title = err.message || t('feedback.failed');
        }
    }
}

function renderExperimentButton(messageId) {
    const state = getAssistantTurnState(messageId);
    if (!state.genesAvailable || state.experimentRunning || state.experimentDone) return;

    const { answerEl, extrasEl } = getMessageRegions(messageId);
    if (!extrasEl) return;

    // 避免重复渲染
    const existing = extrasEl.querySelector('.experiment-trigger-area');
    if (existing) return;
    const existingBtn = extrasEl.querySelector('.experiment-btn');
    if (existingBtn) return;
    const progressEl = extrasEl.querySelector('.experiment-progress');
    if (progressEl && !progressEl.querySelector('.experiment-step.error')) return;
    if (extrasEl.querySelector('.experiment-sop')) return;

    // ---- 创建基因编辑器 + SOP 按钮的容器 ----
    const container = document.createElement('div');
    container.className = 'experiment-trigger-area';

    // ---- 渲染基因编辑器（可编辑的基因芯片列表） ----
    if (state.genes && state.genes.length > 0) {
        const geneEditor = renderGeneEditor(messageId, state);
        container.appendChild(geneEditor);
    }

    // ---- 创建 SOP 生成按钮 ----
    const btn = document.createElement('button');
    btn.className = 'experiment-btn';
    btn.type = 'button';
    btn.textContent = '🧬 生成 CRISPR 实验方案 SOP';
    btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
            await streamExperiment(messageId);
        } catch (error) {
            btn.disabled = false;
            handleExperimentError(messageId, error.message);
        }
    });
    container.appendChild(btn);

    // 始终放到 extrasEl（不插入 answerEl，避免被 updateMessage 覆盖）
    extrasEl.appendChild(container);
}

/**
 * 渲染基因编辑器 —— 可编辑的基因芯片（chip）列表
 *
 * 功能：
 * - 展示后端检测到的基因名，每个基因显示为一个可删除的芯片
 * - 用户可以点击 "×" 移除不需要的基因
 * - 用户可以通过 "+" 按钮添加新的基因名
 * - 编辑结果实时同步到 state.genes，点击 SOP 按钮时传给后端
 *
 * @param {string} messageId - 消息 DOM 元素 ID
 * @param {object} state - assistantTurnState 中的状态对象
 * @returns {HTMLElement} 基因编辑器 DOM 元素
 */
function renderGeneEditor(messageId, state) {
    const editor = document.createElement('div');
    editor.className = 'gene-editor';

    // ---- 标题标签 ----
    const label = document.createElement('div');
    label.className = 'gene-editor-label';
    label.textContent = t('gene.detected');
    editor.appendChild(label);

    // ---- 芯片容器（放置所有基因芯片 + 添加按钮） ----
    const chipsContainer = document.createElement('div');
    chipsContainer.className = 'gene-chips-container';

    /**
     * 重新渲染所有基因芯片（在添加/删除操作后调用）
     */
    function rebuildChips() {
        chipsContainer.innerHTML = '';

        // 为每个基因创建一个可删除的芯片
        state.genes.forEach((geneName, index) => {
            const chip = document.createElement('span');
            chip.className = 'gene-chip';

            // 基因名文本
            const nameSpan = document.createElement('span');
            nameSpan.className = 'gene-chip-name';
            nameSpan.textContent = geneName;
            chip.appendChild(nameSpan);

            // "×" 删除按钮
            const removeBtn = document.createElement('button');
            removeBtn.className = 'gene-chip-remove';
            removeBtn.type = 'button';
            removeBtn.textContent = '\u00d7';  // × 符号
            removeBtn.title = t('gene.removeTitle');
            removeBtn.addEventListener('click', () => {
                // 从 state.genes 中移除该基因并重新渲染
                state.genes.splice(index, 1);
                rebuildChips();
            });
            chip.appendChild(removeBtn);

            chipsContainer.appendChild(chip);
        });

        // ---- "+" 添加基因按钮 ----
        const addBtn = document.createElement('button');
        addBtn.className = 'gene-add-btn';
        addBtn.type = 'button';
        addBtn.textContent = t('gene.addBtn');
        addBtn.addEventListener('click', () => {
            // 替换"+"按钮为输入框
            addBtn.style.display = 'none';

            const inputWrapper = document.createElement('span');
            inputWrapper.className = 'gene-add-wrapper';

            const input = document.createElement('input');
            input.className = 'gene-add-input';
            input.type = 'text';
            input.placeholder = t('gene.inputPlaceholder');
            input.maxLength = 30;

            /**
             * 确认添加基因：去重后加入列表
             */
            function confirmAdd() {
                const name = input.value.trim();
                if (name && !state.genes.includes(name)) {
                    state.genes.push(name);
                }
                // 无论是否添加成功，都重新渲染（移除输入框）
                rebuildChips();
            }

            // 按 Enter 确认添加
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    confirmAdd();
                } else if (e.key === 'Escape') {
                    // 按 Escape 取消输入
                    rebuildChips();
                }
            });

            // 失焦时确认添加
            input.addEventListener('blur', confirmAdd);

            inputWrapper.appendChild(input);
            chipsContainer.appendChild(inputWrapper);

            // 自动聚焦输入框
            input.focus();
        });
        chipsContainer.appendChild(addBtn);
    }

    // 首次渲染芯片
    rebuildChips();

    editor.appendChild(chipsContainer);
    return editor;
}

function removeExperimentButton(messageId) {
    // 移除整个实验触发区域（含基因编辑器 + SOP 按钮）— 仅在 extrasEl 中
    const { extrasEl } = getMessageRegions(messageId);
    extrasEl?.querySelector('.experiment-trigger-area')?.remove();
    extrasEl?.querySelector('.experiment-btn')?.remove();
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function trimSopsForStorage(sops, maxCharsPerSop = 12000, maxTotalChars = 30000) {
    if (!sops || Object.keys(sops).length === 0) return null;

    const trimmed = {};
    let totalChars = 0;

    for (const [accession, text] of Object.entries(sops)) {
        if (totalChars >= maxTotalChars) break;

        const rawText = typeof text === 'string' ? text : String(text || '');
        const remaining = Math.max(maxTotalChars - totalChars, 0);
        const limit = Math.min(maxCharsPerSop, remaining);
        if (limit <= 0) break;

        trimmed[accession] = rawText.length > limit
            ? rawText.slice(0, limit) + '\n\n...(历史记录中已截断)'
            : rawText;
        totalChars += trimmed[accession].length;
    }

    return Object.keys(trimmed).length > 0 ? trimmed : null;
}

function buildPersistableHistory(includeSops = true) {
    return conversationHistory.map(session => ({
        ...session,
        messages: (session.messages || []).map(turn => {
            const nextTurn = { ...turn };
            if (includeSops) {
                const trimmedSops = trimSopsForStorage(nextTurn.sops);
                if (trimmedSops) {
                    nextTurn.sops = trimmedSops;
                } else {
                    delete nextTurn.sops;
                }
            } else {
                delete nextTurn.sops;
            }
            return nextTurn;
        }),
    }));
}

function persistConversationHistory() {
    try {
        const sanitized = buildPersistableHistory(true);
        localStorage.setItem('conversation_history', JSON.stringify(sanitized));
        conversationHistory = sanitized;
    } catch (err) {
        console.warn('保存历史失败，改为不持久化 SOP 全文:', err);
        const fallback = buildPersistableHistory(false);
        localStorage.setItem('conversation_history', JSON.stringify(fallback));
        conversationHistory = fallback;
    }
}

// 保存到历史
function saveToHistory(query, answerText, sources, genes, sops, interactionId, turnId, feedbackSubmitted) {
    const turn = { query, answerText, sources, timestamp: Date.now() };
    // 保存基因列表和 SOP 结果（用于切换对话后重新渲染，不会发给 LLM）
    if (genes && genes.length > 0) turn.genes = genes;
    if (sops && Object.keys(sops).length > 0) turn.sops = sops;
    if (interactionId) turn.interactionId = interactionId;
    if (turnId) turn.turnId = turnId;
    if (feedbackSubmitted) turn.feedback = feedbackSubmitted;

    if (currentSessionId) {
        // 追问：追加到当前 session
        let session = conversationHistory.find(s => s.id === currentSessionId);
        if (!session) {
            session = {
                id: currentSessionId,
                title: query.slice(0, 50),
                messages: [],
                timestamp: Date.now(),
            };
            conversationHistory.unshift(session);
        }
        if (session) {
            session.messages.push(turn);
            session.timestamp = Date.now();
            // 将该 session 移到列表最前面
            const idx = conversationHistory.indexOf(session);
            if (idx > 0) {
                conversationHistory.splice(idx, 1);
                conversationHistory.unshift(session);
            }
        }
    } else {
        // 新对话：创建新 session
        currentSessionId = createSessionId();
        conversationHistory.unshift({
            id: currentSessionId,
            title: query.slice(0, 50),
            messages: [turn],
            timestamp: Date.now(),
        });
    }

    if (conversationHistory.length > 20) {
        conversationHistory = conversationHistory.slice(0, 20);
    }

    persistConversationHistory();
    loadHistory();
}

// 将 SOP 结果更新到当前 session 最后一轮（按钮触发 SOP 生成后调用）
function updateLastTurnSops(sops, genes) {
    if (!currentSessionId) return;
    const session = conversationHistory.find(s => s.id === currentSessionId);
    if (!session || !session.messages.length) return;
    const lastTurn = session.messages[session.messages.length - 1];
    if (sops && Object.keys(sops).length > 0) lastTurn.sops = sops;
    if (genes && genes.length > 0) lastTurn.genes = genes;
    persistConversationHistory();
}

function updateLastTurnFeedback(interactionId, rating) {
    if (!currentSessionId || !interactionId) return;
    const session = conversationHistory.find(s => s.id === currentSessionId);
    if (!session || !session.messages.length) return;
    const turn = [...session.messages].reverse().find(item => item.interactionId === interactionId);
    if (!turn) return;
    turn.feedback = rating;
    persistConversationHistory();
}

// 加载历史
function loadHistory() {
    const saved = localStorage.getItem('conversation_history');
    if (saved) {
        try {
            conversationHistory = JSON.parse(saved);
        } catch (err) {
            console.warn('历史记录解析失败，已清空本地缓存:', err);
            conversationHistory = [];
            localStorage.removeItem('conversation_history');
        }

        // 兼容旧格式：将单条 {query, answerText} 迁移为 session 格式
        let migrated = false;
        conversationHistory = conversationHistory.map(item => {
            if (item.messages) return item; // 已经是新格式
            if (!item.answerText) return null; // 过滤无效旧数据
            migrated = true;
            return {
                id: `migrated-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                title: (item.query || '').slice(0, 50),
                messages: [{
                    query: item.query,
                    answerText: item.answerText,
                    sources: item.sources || [],
                    timestamp: item.timestamp || Date.now(),
                }],
                timestamp: item.timestamp || Date.now(),
            };
        }).filter(Boolean);

        if (migrated) {
            persistConversationHistory();
        }

        const hasStoredSops = conversationHistory.some(
            session => (session.messages || []).some(turn => turn.sops && Object.keys(turn.sops).length > 0)
        );
        if (hasStoredSops) {
            persistConversationHistory();
        }
    }

    historyList.innerHTML = '';

    if (conversationHistory.length === 0) {
        historyList.innerHTML = `<div class="history-loading">${t('sidebar.noHistory')}</div>`;
        return;
    }

    conversationHistory.forEach((session) => {
        const div = document.createElement('div');
        div.className = 'history-item';
        if (session.id === currentSessionId) {
            div.classList.add('active');
        }
        const displayTitle = session.title || (session.messages[0]?.query || '');
        div.textContent = displayTitle.slice(0, 30) + (displayTitle.length > 30 ? '...' : '');
        const msgCount = session.messages.length;
        if (msgCount > 1) {
            const badge = document.createElement('span');
            badge.className = 'history-msg-count';
            badge.textContent = msgCount;
            div.appendChild(badge);
        }
        div.onclick = () => { restoreConversation(session); };
        historyList.appendChild(div);
    });
}

// 还原历史会话
function restoreConversation(session) {
    abortCurrentStream();
    hideWelcomeAndScene();
    hasStartedConversation = true;
    currentSessionId = session.id;

    chatContainer.innerHTML = '';

    // 恢复该 session 内的所有多轮对话
    for (const turn of session.messages) {
        addMessage('user', turn.query);
        const answerHtml = turn.answerText ? formatAnswer(turn.answerText, turn.sources || []) : '';
        const msgId = addMessage('assistant', answerHtml);
        const state = getAssistantTurnState(msgId);
        state.query = turn.query;
        state.answerText = turn.answerText || '';
        state.sources = turn.sources || [];
        state.genes = turn.genes ? [...turn.genes] : [];
        state.interactionId = turn.interactionId || '';
        state.turnId = turn.turnId || '';
        state.feedbackSubmitted = turn.feedback || '';
        state.streamDone = true;
        if (turn.answerText && turn.sources && turn.sources.length > 0) {
            const { answerEl } = getMessageRegions(msgId);
            if (answerEl) answerEl.innerHTML = formatAnswer(turn.answerText, turn.sources, msgId);
        }

        // 恢复来源、基因、SOP 到 extrasEl
        const { extrasEl } = getMessageRegions(msgId);
        if (extrasEl) {
            // 在回答末尾追加参考文献
            if (turn.sources && turn.sources.length > 0) {
                extrasEl.insertAdjacentHTML('beforeend', renderReferences(turn.sources, msgId));
            }
            // SOP 结果
            if (turn.sops && Object.keys(turn.sops).length > 0) {
                renderSOPs(extrasEl, turn.sops);
            }
            if (turn.interactionId && !turn.feedback) {
                renderFeedbackControls(msgId);
            }
        }

        // 恢复基因编辑器 + SOP 按钮（有基因但还没有 SOP）
        if (turn.genes && turn.genes.length > 0 && (!turn.sops || Object.keys(turn.sops).length === 0)) {
            state.genesAvailable = true;
            renderExperimentButton(msgId);
        }
    }

    // 更新侧边栏高亮
    loadHistory();
}

// 清空历史
function clearHistory() {
    if (confirm(t('confirm.clearHistory'))) {
        conversationHistory = [];
        localStorage.removeItem('conversation_history');
        loadHistory();
    }
}

// ===== CRISPR 实验方案自动生成（内联 pipeline 进度） =====

const STEP_LABELS = [
    '查询 NCBI Accession',
    '下载基因序列',
    '设计 CRISPR 靶点',
    '生成实验方案 SOP',
];

function appendExperimentProgress(messageId, genes) {
    const { extrasEl } = getMessageRegions(messageId);
    if (!extrasEl) return;

    extrasEl.querySelectorAll('.experiment-progress, .experiment-sop').forEach(el => el.remove());

    const genesStr = genes ? genes.join(', ') : '';

    const progressEl = document.createElement('div');
    progressEl.className = 'experiment-progress';
    progressEl.id = `exp-progress-${messageId}`;
    progressEl.innerHTML = `<div class="experiment-progress-title">\uD83D\uDD2C CRISPR 实验方案生成${genesStr ? '（' + escapeHtml(genesStr) + '）' : ''}</div>` +
        STEP_LABELS.map((label, i) =>
            `<div class="experiment-step" id="exp-step-${messageId}-${i+1}">` +
            `<span class="step-icon">${i+1}</span>` +
            `<span class="step-label">${label}</span>` +
            `</div>`
        ).join('');
    extrasEl.appendChild(progressEl);
    smartScroll();
}

function updateExperimentProgress(messageId, data) {
    const step = data.step;
    // 标记之前的步骤为 done
    for (let i = 1; i < step; i++) {
        const el = document.getElementById(`exp-step-${messageId}-${i}`);
        if (el && !el.classList.contains('done')) {
            el.classList.remove('active');
            el.classList.add('done');
            el.querySelector('.step-icon').textContent = '\u2713';
        }
    }
    // 标记当前步骤为 active
    const curEl = document.getElementById(`exp-step-${messageId}-${step}`);
    if (curEl) {
        curEl.classList.add('active');
        curEl.querySelector('.step-label').textContent = data.msg;
    }
    smartScroll();
}

function handleExperimentError(messageId, msg) {
    if (!document.getElementById(`exp-step-${messageId}-1`)) {
        appendExperimentProgress(messageId, []);
    }

    let targetEl = null;
    // 找到最后一个 active 步骤并标记为 error
    for (let i = STEP_LABELS.length; i >= 1; i--) {
        const el = document.getElementById(`exp-step-${messageId}-${i}`);
        if (el && el.classList.contains('active')) {
            targetEl = el;
            break;
        }
    }
    if (!targetEl) {
        targetEl = document.getElementById(`exp-step-${messageId}-1`);
    }
    if (targetEl) {
        targetEl.classList.remove('active', 'done');
        targetEl.classList.add('error');
        targetEl.querySelector('.step-icon').textContent = '\u2717';
        targetEl.querySelector('.step-label').textContent = msg;
    }
    smartScroll();
}

function renderSOPs(container, sops) {
    container.querySelectorAll('.experiment-sop').forEach(el => el.remove());
    // 标记所有进度步骤为 done
    const progressEl = container.closest('.message-content')?.querySelector('.experiment-progress') || container.querySelector('.experiment-progress');
    if (progressEl) {
        progressEl.querySelectorAll('.experiment-step').forEach(el => {
            el.classList.remove('active');
            el.classList.add('done');
            el.querySelector('.step-icon').textContent = '\u2713';
        });
    }

    for (const [accession, text] of Object.entries(sops)) {
        const sopEl = document.createElement('div');
        sopEl.className = 'experiment-sop';

        const headerEl = document.createElement('div');
        headerEl.className = 'experiment-sop-header';

        // 生成下载文件名：基因名（accession）+ 登录账号
        let userEmail = 'user';
        try {
            if (typeof currentSession !== 'undefined' && currentSession && currentSession.user && currentSession.user.email) {
                userEmail = currentSession.user.email.split('@')[0];
            }
        } catch (e) {
            // 忽略错误，使用默认值
        }
        const safeAccession = accession.replace(/[^a-zA-Z0-9\-_]/g, '_');
        const downloadFilename = `${safeAccession}_${userEmail}.doc`;

        headerEl.innerHTML =
            `<span class="experiment-sop-title">\uD83D\uDCCB 实验方案: ${escapeHtml(accession)}</span>` +
            `<span style="display:flex;align-items:center;gap:8px;">` +
            `<button class="sop-download-btn" title="下载实验方案">⬇ 下载</button>` +
            `<span class="experiment-sop-toggle">\u25BC</span>` +
            `</span>`;

        // 用 addEventListener 绑定下载，避免将大量 SOP 文本嵌入 onclick 属性导致 HTML 解析错误
        headerEl.querySelector('.sop-download-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            downloadSOP(downloadFilename, text);
        });

        const bodyEl = document.createElement('div');
        bodyEl.className = 'experiment-sop-body';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'experiment-sop-content markdown-body';
        if (typeof marked !== 'undefined') {
            contentDiv.innerHTML = DOMPurify.sanitize(marked.parse(text));
        } else {
            contentDiv.textContent = text;
        }

        bodyEl.appendChild(contentDiv);
        sopEl.appendChild(headerEl);
        sopEl.appendChild(bodyEl);
        container.appendChild(sopEl);

        // 默认展开第一个
        const isFirst = container.querySelectorAll('.experiment-sop').length === 1;
        if (isFirst) {
            headerEl.classList.add('expanded');
            bodyEl.classList.add('expanded');
        }

        // 折叠/展开事件
        headerEl.addEventListener('click', () => {
            headerEl.classList.toggle('expanded');
            bodyEl.classList.toggle('expanded');
        });
    }
}

// 下载 SOP 文件（转换为 Word .doc 格式）
function downloadSOP(filename, text) {
    // 确保文件后缀为 .doc
    const docFilename = filename.replace(/\.(md|doc)$/i, '') + '.doc';

    // 用 marked 将 markdown 转换为 HTML
    let htmlContent = '';
    if (typeof marked !== 'undefined') {
        htmlContent = DOMPurify.sanitize(marked.parse(text));
    } else {
        // fallback：简单段落转换
        const raw = text.split('\n').map(line => `<p>${line}</p>`).join('');
        htmlContent = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(raw) : raw;
    }

    // 构造 Word 兼容的 HTML 文档
    const wordDoc = `
<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<style>
    body { font-family: "SimSun", "宋体", "Times New Roman", serif; font-size: 12pt; line-height: 1.8; color: #333; }
    h1 { font-size: 18pt; font-weight: bold; margin: 16pt 0 8pt 0; }
    h2 { font-size: 16pt; font-weight: bold; margin: 14pt 0 6pt 0; border-bottom: 1px solid #ccc; padding-bottom: 4pt; }
    h3 { font-size: 14pt; font-weight: bold; margin: 12pt 0 4pt 0; }
    h4 { font-size: 12pt; font-weight: bold; margin: 10pt 0 4pt 0; }
    p { margin: 4pt 0; }
    ul, ol { margin: 4pt 0 4pt 18pt; }
    li { margin-bottom: 2pt; }
    table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
    th, td { border: 1px solid #999; padding: 4pt 8pt; text-align: left; font-size: 11pt; }
    th { background: #f0f0f0; font-weight: bold; }
    blockquote { margin: 4pt 0; padding: 4pt 12pt; border-left: 3pt solid #ccc; color: #666; }
    code { font-family: "Courier New", monospace; font-size: 10pt; background: #f5f5f5; padding: 1pt 3pt; }
    pre { font-family: "Courier New", monospace; font-size: 10pt; background: #f5f5f5; padding: 8pt; margin: 6pt 0; white-space: pre-wrap; }
    hr { border: none; border-top: 1pt solid #ccc; margin: 10pt 0; }
    a { color: #2563eb; }
</style>
</head>
<body>
${htmlContent}
</body>
</html>`;

    const blob = new Blob([wordDoc], { type: 'application/msword;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = docFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}


// ===== Skills 管理 =====
let _cachedSkills = [];
let _cachedTools = [];
let _currentEditingSkill = null;

function setupSkillModal() {
    const toggle = document.getElementById('skill-toggle');
    const overlay = document.getElementById('skill-modal-overlay');
    const closeBtn = document.getElementById('skill-modal-close');
    const createBtn = document.getElementById('skill-create-btn');
    const saveBtn = document.getElementById('skill-save-btn');
    const deleteBtn = document.getElementById('skill-delete-btn');

    if (!toggle || !overlay) return;

    toggle.addEventListener('click', () => {
        overlay.style.display = 'flex';
        loadSkills();
        loadTools();
    });

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            overlay.style.display = 'none';
        });
    }

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.style.display = 'none';
    });

    if (createBtn) createBtn.addEventListener('click', createNewSkill);
    if (saveBtn) saveBtn.addEventListener('click', saveCurrentSkill);
    if (deleteBtn) deleteBtn.addEventListener('click', deleteCurrentSkill);
}

async function loadSkills() {
    try {
        const resp = await fetch('/api/skills', {
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });
        if (!resp.ok) return;
        const data = await resp.json();
        _cachedSkills = data.skills || [];
        renderSkillList(_cachedSkills);
    } catch (e) {
        console.error('加载 Skills 失败:', e);
    }
}

async function loadTools() {
    try {
        const resp = await fetch('/api/tools', {
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });
        if (!resp.ok) return;
        const data = await resp.json();
        _cachedTools = data.tools || [];
        renderToolsList(_cachedTools);
    } catch (e) {
        console.error('加载 Tools 失败:', e);
    }
}

function renderSkillList(skills) {
    const listEl = document.getElementById('skill-list');
    if (!listEl) return;

    listEl.innerHTML = skills.map(s => {
        const badgeText = s.is_shared ? t('skill.shared') : t('skill.private');
        const badgeCls = s.is_shared ? 'skill-badge-shared' : 'skill-badge-private';
        const pref = skillPrefs[s.name] || 'auto';
        return `
            <div class="skill-item ${_currentEditingSkill === s.name ? 'active' : ''}" data-skill="${escapeHtml(s.name)}">
                <div class="skill-item-header">
                    <span class="skill-item-name">${escapeHtml(s.name)}</span>
                    <span class="skill-badge ${badgeCls}">${badgeText}</span>
                </div>
                <div class="skill-item-desc">${escapeHtml(s.description || '')}</div>
                <div class="skill-item-controls">
                    <select class="skill-mode-select" data-skill="${escapeHtml(s.name)}">
                        <option value="auto" ${pref === 'auto' ? 'selected' : ''}>${t('skill.mode_auto')}</option>
                        <option value="must_use" ${pref === 'must_use' ? 'selected' : ''}>${t('skill.mode_must')}</option>
                        <option value="disabled" ${pref === 'disabled' ? 'selected' : ''}>${t('skill.mode_disabled')}</option>
                    </select>
                </div>
            </div>
        `;
    }).join('');

    // Click to open editor
    listEl.querySelectorAll('.skill-item').forEach(el => {
        el.addEventListener('click', (e) => {
            if (e.target.closest('.skill-mode-select')) return;
            openSkillEditor(el.dataset.skill);
        });
    });

    // Preference change
    listEl.querySelectorAll('.skill-mode-select').forEach(sel => {
        sel.addEventListener('change', () => {
            const name = sel.dataset.skill;
            if (sel.value === 'auto') {
                delete skillPrefs[name];
            } else {
                skillPrefs[name] = sel.value;
            }
            saveSkillPrefs();
        });
    });
}

function renderToolsList(tools) {
    const listEl = document.getElementById('skill-tools-list');
    if (!listEl) return;

    listEl.innerHTML = tools.map(tool => `
        <div class="skill-tool-item">
            <span class="skill-tool-name">${escapeHtml(tool.name)}</span>
            <span class="skill-tool-desc">${escapeHtml(tool.description || '')}</span>
        </div>
    `).join('');
}

function renderEditorTools(selectedTools, disabled) {
    const container = document.getElementById('skill-editor-tools');
    if (!container) return;
    const selected = new Set(selectedTools || []);
    container.innerHTML = '<div class="skill-editor-tools-label">Tools:</div>' +
        _cachedTools.map(t => `<label class="skill-editor-tool-item">
            <input type="checkbox" value="${escapeHtml(t.name)}"
                ${selected.has(t.name) ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
            <span>${escapeHtml(t.name)}</span>
        </label>`).join('');
}

function getEditorTools() {
    return [...document.querySelectorAll('#skill-editor-tools input:checked')].map(cb => cb.value);
}

function openSkillEditor(name) {
    const skill = _cachedSkills.find(s => s.name === name);
    if (!skill) return;

    _currentEditingSkill = name;
    const isShared = skill.is_shared;

    const editorPanel = document.getElementById('skill-editor-panel');
    if (editorPanel) editorPanel.style.display = 'block';

    const nameInput = document.getElementById('skill-editor-name');
    if (nameInput) { nameInput.value = skill.name; nameInput.disabled = isShared; }

    const descInput = document.getElementById('skill-editor-desc');
    if (descInput) { descInput.value = skill.description || ''; descInput.disabled = isShared; }

    renderEditorTools(skill.tools || [], isShared);

    const contentArea = document.getElementById('skill-editor-content');
    if (contentArea) { contentArea.value = skill.content || ''; contentArea.disabled = isShared; }

    const badge = document.getElementById('skill-editor-badge');
    if (badge) {
        badge.textContent = isShared ? t('skill.shared') : t('skill.private');
        badge.className = 'skill-editor-badge ' + (isShared ? 'skill-badge-shared' : 'skill-badge-private');
    }

    // 共享 skill 隐藏保存/删除按钮
    const saveBtn = document.getElementById('skill-save-btn');
    const deleteBtn = document.getElementById('skill-delete-btn');
    if (saveBtn) saveBtn.style.display = isShared ? 'none' : 'inline-block';
    if (deleteBtn) deleteBtn.style.display = isShared ? 'none' : 'inline-block';

    // 列表高亮
    document.querySelectorAll('.skill-item').forEach(el => {
        el.classList.toggle('active', el.dataset.skill === name);
    });
}

async function createNewSkill() {
    // 弹出输入框让用户描述需求
    const prompt = window.prompt(currentLang === 'zh' ? '描述你想要的 Skill：' : 'Describe the Skill you want:', '');
    if (!prompt || !prompt.trim()) return;

    const editorPanel = document.getElementById('skill-editor-panel');
    if (editorPanel) editorPanel.style.display = 'block';

    // 显示 loading 状态
    _currentEditingSkill = null;
    const nameInput = document.getElementById('skill-editor-name');
    const descInput = document.getElementById('skill-editor-desc');
    const contentArea = document.getElementById('skill-editor-content');
    if (nameInput) { nameInput.value = ''; nameInput.disabled = false; }
    if (descInput) { descInput.value = currentLang === 'zh' ? '生成中...' : 'Generating...'; descInput.disabled = true; }
    if (contentArea) { contentArea.value = ''; contentArea.disabled = true; }
    renderEditorTools([], true);

    const badge = document.getElementById('skill-editor-badge');
    if (badge) { badge.textContent = t('skill.private'); badge.className = 'skill-editor-badge skill-badge-private'; }
    const saveBtn = document.getElementById('skill-save-btn');
    const deleteBtn = document.getElementById('skill-delete-btn');
    if (saveBtn) saveBtn.style.display = 'inline-block';
    if (deleteBtn) deleteBtn.style.display = 'none';

    try {
        const resp = await fetch('/api/skills/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getAccessToken() },
            body: JSON.stringify({ prompt: prompt.trim() }),
        });
        if (!resp.ok) throw new Error('生成失败');
        const draft = await resp.json();

        // 填入编辑器
        if (nameInput) { nameInput.value = draft.name || ''; nameInput.disabled = false; }
        if (descInput) { descInput.value = draft.description || ''; descInput.disabled = false; }
        renderEditorTools(draft.tools || [], false);
        if (contentArea) { contentArea.value = draft.content || ''; contentArea.disabled = false; }
    } catch (e) {
        if (descInput) { descInput.value = ''; descInput.disabled = false; }
        if (contentArea) contentArea.disabled = false;
        renderEditorTools([], false);
        alert('Skill 生成失败: ' + e.message);
    }

    document.querySelectorAll('.skill-item.active').forEach(el => el.classList.remove('active'));
}

async function saveCurrentSkill() {
    const nameInput = document.getElementById('skill-editor-name');
    const descInput = document.getElementById('skill-editor-desc');
    const contentArea = document.getElementById('skill-editor-content');
    const name = (nameInput?.value || '').trim();
    const description = (descInput?.value || '').trim();
    const tools = getEditorTools();
    const content = (contentArea?.value || '').trim();

    if (!name) return;

    try {
        const isUpdate = _currentEditingSkill && _cachedSkills.some(s => s.name === _currentEditingSkill && !s.is_shared);
        const url = isUpdate ? `/api/skills/${encodeURIComponent(_currentEditingSkill)}` : '/api/skills';
        const method = isUpdate ? 'PUT' : 'POST';

        const resp = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + getAccessToken(),
            },
            body: JSON.stringify({ name, description, tools, content }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.detail || t('skill.saveFail'));
            return;
        }

        _currentEditingSkill = name;
        await loadSkills();
        openSkillEditor(name);
    } catch (e) {
        alert(t('skill.saveFail'));
    }
}

async function deleteCurrentSkill() {
    if (!_currentEditingSkill) return;
    if (!confirm(t('skill.confirmDelete', _currentEditingSkill))) return;

    const skillName = _currentEditingSkill;
    try {
        const resp = await fetch(`/api/skills/${encodeURIComponent(skillName)}`, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + getAccessToken() },
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(err.detail || t('skill.deleteFail'));
            return;
        }

        _currentEditingSkill = null;
        const editorPanel = document.getElementById('skill-editor-panel');
        if (editorPanel) editorPanel.style.display = 'none';
        delete skillPrefs[skillName];
        saveSkillPrefs();
        await loadSkills();
    } catch (e) {
        alert(t('skill.deleteFail'));
    }
}

function saveSkillPrefs() {
    localStorage.setItem('skillPrefs', JSON.stringify(skillPrefs));
}

// ===== 模型选择 =====

async function initModelSelect() {
    const selectEl = document.getElementById('model-select');
    if (!selectEl) return;

    try {
        const resp = await fetch('/api/config');
        if (!resp.ok) return;
        const data = await resp.json();
        const models = data.models || [];

        if (models.length <= 1) {
            selectEl.style.display = 'none';
            if (models.length === 1) selectedModelId = models[0].id;
            return;
        }

        selectEl.innerHTML = models.map(m =>
            `<option value="${escapeHtml(m.id)}" ${m.id === selectedModelId ? 'selected' : ''}>${escapeHtml(m.name)}</option>`
        ).join('');
        selectEl.style.display = 'inline-block';

        // If stored selection not in options, default to first
        if (!models.some(m => m.id === selectedModelId)) {
            selectedModelId = models[0].id;
            localStorage.setItem('selectedModelId', selectedModelId);
        }

        selectEl.addEventListener('change', () => {
            selectedModelId = selectEl.value;
            localStorage.setItem('selectedModelId', selectedModelId);
        });
    } catch (e) {
        console.error('加载模型列表失败:', e);
    }
}
