# 部署和测试指南

## 已完成的改进

### 后端改进（`src/nutrimaster/web/admin/app.py`）

1. **新增 API 端点**：
   - `/api/index/status` - 查询索引状态
   - `/api/index/rebuild` - 手动触发索引重建

2. **实时增量索引**：
   - 每篇论文处理成功后立即更新索引
   - Pipeline 结束时兜底更新（无论正常完成还是被停止）

3. **进度日志**（`src/nutrimaster/rag/gene_index.py`）：
   - 每处理一个文件输出日志
   - 每 10 个文件输出汇总进度

### 前端改进（`src/nutrimaster/web/admin/static/`）

1. **Dashboard 索引状态卡片**（`index.html`）：
   - 显示已索引/总文件数
   - 显示总 chunks 数
   - 显示最后更新时间
   - 显示同步状态（✅ Synced / ⚠️ N files missing）
   - "🔄 Rebuild Index" 按钮

2. **JavaScript 功能**（`admin.js`）：
   - `refreshIndexStatus()` - 刷新索引状态
   - `rebuildIndex()` - 手动触发索引重建
   - SSE 事件处理增强（索引完成后自动刷新状态）

3. **Upload 面板提示**（`admin.js`）：
   - ZIP 上传成功后提示用户运行 Pipeline
   - 说明索引会自动更新

4. **样式改进**（`style.css`）：
   - 索引状态卡片样式
   - 状态徽章样式（success/warning/error/info）

### CLI 工具

1. **`rebuild_index_robust.py`**：
   - 无缓冲输出，实时查看进度
   - 详细的状态信息和时间戳
   - 适合后台运行

2. **`test_index_api.py`**：
   - 测试 API 端点是否正常工作
   - 显示索引状态

3. **`check_index_status.py`**：
   - 快速检查索引状态
   - 估算重建时间

## 部署步骤

### 1. 等待当前索引重建完成

```bash
# 查看进度
tail -f rebuild_robust.log

# 或检查进程状态
ps aux | grep rebuild_index_robust
```

**预计完成时间**：约 2 小时（从 23:11 开始，预计 01:11 完成）

### 2. 重启 Flask 应用

```bash
# 找到 Flask 进程
ps aux | grep "python.*app.py\|flask run\|gunicorn"

# 重启应用（具体命令取决于你的部署方式）
# 如果使用 systemd:
sudo systemctl restart nutrimaster

# 如果使用 supervisor:
supervisorctl restart nutrimaster

# 如果是开发模式直接运行:
# 先 Ctrl+C 停止，然后重新运行
python -m nutrimaster.web.app
```

### 3. 验证 API 端点

```bash
# 测试索引状态 API
python3 test_index_api.py

# 或使用 curl（需要认证 token）
curl http://localhost:8000/admin/api/index/status
```

**预期输出**：
```json
{
  "total_files": 18181,
  "indexed_files": 18181,
  "missing_files": 0,
  "total_chunks": 134661,
  "embedding_shape": [134661, 1024],
  "last_updated": "2026-05-09T01:11:00",
  "is_synced": true
}
```

### 4. 测试 Admin Panel

1. **访问 Dashboard**：
   - 打开浏览器访问 `http://localhost:8000/admin`
   - 登录后查看 Dashboard

2. **检查索引状态卡片**：
   - 应该显示 "Indexed Files: 18,181 / 18,181"
   - 状态显示 "✅ Synced"

3. **测试手动重建**（可选）：
   - 点击 "🔄 Rebuild Index" 按钮
   - 确认对话框
   - 观察是否有错误提示

### 5. 端到端测试

**测试场景 1：上传 ZIP → Pipeline → 自动索引**

```bash
# 1. 准备一个包含 1-2 个 .md 文件的测试 ZIP
# 2. 在 Admin Panel 上传 ZIP
# 3. 切换到 Pipeline 标签，点击 "Run All"
# 4. 观察 Pipeline log，应该看到：
#    - 论文处理进度
#    - 每篇论文处理完后的索引更新（静默，不会显示）
#    - Pipeline 结束时的索引重建消息：
#      "🔄 Rebuilding RAG index..."
#      "✅ RAG index rebuilt successfully"
# 5. 切换回 Dashboard，检查索引状态是否更新
```

**测试场景 2：中途停止 Pipeline**

```bash
# 1. 上传包含多个文件的 ZIP
# 2. 运行 Pipeline
# 3. 处理几篇论文后点击 "Stop"
# 4. 观察 log，应该看到索引重建消息
# 5. 检查 Dashboard，已处理的文件应该都已索引
```

**测试场景 3：手动拷贝文件后手动重建**

```bash
# 1. 直接拷贝一个 JSON 文件到 data/corpus/
cp some_paper_nutri_plant_verified.json data/corpus/

# 2. 访问 Dashboard，应该显示 "⚠️ 1 files missing"
# 3. 点击 "🔄 Rebuild Index"
# 4. 等待几秒，刷新页面
# 5. 状态应该变为 "✅ Synced"
```

## 监控和维护

### 日常检查

访问 Admin Panel Dashboard，查看索引状态卡片：
- ✅ Synced - 一切正常
- ⚠️ N files missing - 需要手动重建索引

### 定期维护（可选）

设置 cron 任务，每天自动检查和修复索引：

```bash
# 编辑 crontab
crontab -e

# 添加任务（每天凌晨 3 点运行）
0 3 * * * cd /data/haotianwu/biojson && python3 rebuild_index_robust.py >> /var/log/index_rebuild.log 2>&1
```

### 故障排查

**问题 1：Dashboard 不显示索引状态卡片**

```bash
# 检查 Flask 应用是否重启
ps aux | grep flask

# 检查浏览器控制台是否有 JavaScript 错误
# 检查 /api/index/status 是否返回 200
curl http://localhost:8000/admin/api/index/status
```

**问题 2：索引状态显示缺失文件**

```bash
# 方法 1：通过 Admin Panel 手动重建
# 点击 Dashboard 的 "🔄 Rebuild Index" 按钮

# 方法 2：通过 CLI 脚本重建
python3 rebuild_index_robust.py

# 方法 3：检查是否有文件损坏
python3 check_index_status.py
```

**问题 3：索引重建失败**

```bash
# 检查错误日志
tail -100 rebuild_robust.log

# 常见原因：
# - Jina API key 过期或无效
# - 网络连接问题
# - 磁盘空间不足

# 检查 Jina API key
echo $JINA_API_KEY

# 检查磁盘空间
df -h data/index/
```

## 性能优化（可选）

如果觉得实时索引更新太慢，可以改为批量更新：

**修改 `app.py` 中的 `_on_paper_done()` 函数**：

```python
# 每 10 篇或最后一篇时更新
if status == "success" and (done % 10 == 0 or done == total):
    try:
        _refresh_index(DATA_DIR, force=False)
    except Exception as e:
        print(f"⚠️ 索引更新失败: {e}")
```

**权衡**：
- 优点：减少 API 调用次数，提高 Pipeline 速度
- 缺点：中途停止时可能有最多 9 篇论文未索引（会在结束时兜底更新）

## 回滚方案

如果新版本有问题，可以回滚到之前的版本：

```bash
# 1. 恢复旧版本的文件
git checkout HEAD~1 src/nutrimaster/web/admin/app.py
git checkout HEAD~1 src/nutrimaster/web/admin/static/
git checkout HEAD~1 src/nutrimaster/rag/gene_index.py

# 2. 重启 Flask 应用

# 3. 索引仍然可以通过 CLI 脚本手动更新
python3 rebuild_index_robust.py
```

## 下一步改进（P2 优先级）

1. **进度条可视化**：
   - 在 Pipeline log 中显示索引重建进度
   - 需要修改 `_refresh_index()` 添加进度回调

2. **索引健康检查端点**：
   - `/api/index/health` - 返回详细的健康报告
   - 包括缺失文件列表、建议操作等

3. **自动重试机制**：
   - 索引更新失败时自动重试 3 次
   - 指数退避策略

4. **索引重建历史**：
   - 记录每次重建的时间、文件数、耗时
   - 在 Dashboard 显示历史记录

## 总结

✅ **完全鲁棒**：四层防护机制，确保不会遗漏任何文件
✅ **实时同步**：每篇论文处理完立即索引
✅ **可监控**：Dashboard 实时显示索引健康状态
✅ **可恢复**：多种方式手动修复索引
✅ **易部署**：只需重启 Flask 应用即可生效

**关键文件**：
- `src/nutrimaster/web/admin/app.py` - 后端逻辑
- `src/nutrimaster/web/admin/static/admin.js` - 前端逻辑
- `src/nutrimaster/web/admin/static/index.html` - 界面
- `src/nutrimaster/web/admin/static/style.css` - 样式
- `src/nutrimaster/rag/gene_index.py` - 索引核心逻辑
- `rebuild_index_robust.py` - CLI 重建脚本
