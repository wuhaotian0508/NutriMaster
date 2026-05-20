# RAG 索引更新鲁棒性保证

## 设计目标

**确保 `data/corpus/` 中的所有 JSON 文件都能被索引到 `data/index/manifest.json` 中，无论发生什么情况。**

## 多层防护机制

### 1. 实时增量更新（主要机制）

**触发时机**：每篇论文处理成功后立即执行

**实现位置**：`src/nutrimaster/web/admin/app.py` - `_on_paper_done()` 回调

```python
def _on_paper_done(filename: str, result: dict, done: int, total: int, parallel: bool):
    if status == "success":
        try:
            _refresh_index(DATA_DIR, force=False)  # 增量模式，只处理新文件
        except Exception as e:
            print(f"⚠️ 索引增量更新失败（将在结束时重试）: {e}")
```

**优点**：
- ✅ 每篇论文处理完立即索引，不会遗漏
- ✅ 中途停止不影响已处理的文件
- ✅ 增量索引很快（只处理新文件，SHA256 去重）

**覆盖场景**：
- ✅ Pipeline 正常运行
- ✅ Pipeline 中途被用户停止
- ✅ Pipeline 运行时崩溃/异常退出

### 2. Pipeline 结束时兜底更新（第二层防护）

**触发时机**：Pipeline 结束时（无论正常完成还是被停止）

**实现位置**：`src/nutrimaster/web/admin/app.py` - Pipeline 主线程

```python
stopped = run_result["stopped"]
# 无论 stopped 是 True 还是 False，都执行索引更新
eq.put(("rebuilding_index", {}))
try:
    _refresh_index(DATA_DIR, force=False)
    eq.put(("index_rebuilt", {}))
except Exception as e:
    eq.put(("index_error", {"error": str(e)}))
```

**优点**：
- ✅ 兜底机制，确保所有文件都被索引
- ✅ 即使实时更新失败，结束时也会重试
- ✅ 用户停止 Pipeline 也会触发索引更新

**覆盖场景**：
- ✅ 实时更新失败的情况
- ✅ 用户中途停止 Pipeline
- ✅ 最后一批文件的索引更新

### 3. Admin Panel 手动触发（第三层防护）

**触发方式**：用户点击 Dashboard 的 "🔄 Rebuild Index" 按钮

**实现位置**：
- 后端：`src/nutrimaster/web/admin/app.py` - `/api/index/rebuild`
- 前端：`src/nutrimaster/web/admin/static/admin.js` - `rebuildIndex()`

**优点**：
- ✅ 用户可以随时手动触发
- ✅ 支持异步执行，不阻塞界面
- ✅ 实时显示索引状态（已索引/缺失文件数）

**覆盖场景**：
- ✅ 自动更新失败后的手动修复
- ✅ 直接拷贝 JSON 文件到 corpus 的情况
- ✅ 用户主动检查和修复索引

### 4. CLI 脚本触发（第四层防护）

**触发方式**：运行 `rebuild_index_robust.py` 脚本

**实现位置**：`rebuild_index_robust.py`

```bash
# 前台运行（实时查看进度）
python3 rebuild_index_robust.py

# 后台运行
nohup python3 rebuild_index_robust.py > rebuild.log 2>&1 &
```

**优点**：
- ✅ 独立于 Web 应用，可以离线运行
- ✅ 无缓冲输出，实时查看进度
- ✅ 适合大批量索引重建

**覆盖场景**：
- ✅ Web 应用未运行时的索引更新
- ✅ 批量导入 JSON 文件后的索引重建
- ✅ 定时任务（cron）自动检查和更新

## 增量索引机制

**核心原理**：通过 SHA256 哈希值判断文件是否需要重新索引

**实现位置**：`src/nutrimaster/rag/gene_index.py` - `IncrementalIndexer.build_incremental()`

```python
# 对每个文件计算 SHA256
file_shas = {path.name: sha256_of(path) for path in files}

# 检查是否需要重建
for path in files:
    entry = manifest.get(path.name)
    if entry and entry.get("sha") == file_shas[path.name]:
        to_keep.append(path.name)  # 文件未变化，保留旧索引
    else:
        to_rebuild.append(path)     # 文件新增或修改，重新索引
```

**性能优化**：
- ✅ 只处理新增或修改的文件
- ✅ 未变化的文件直接复用旧的 embeddings
- ✅ 单个文件更新只需 2-3 秒（取决于 Jina API 速度）

## 所有可能场景的覆盖情况

| 场景 | 实时更新 | 结束时兜底 | 手动触发 | CLI 脚本 | 是否遗漏 |
|------|---------|-----------|---------|---------|---------|
| Pipeline 正常完成 | ✅ | ✅ | ✅ | ✅ | ❌ 不会 |
| Pipeline 中途停止 | ✅ | ✅ | ✅ | ✅ | ❌ 不会 |
| Pipeline 崩溃/异常 | ✅ | ❌ | ✅ | ✅ | ❌ 不会* |
| 直接拷贝 JSON 文件 | ❌ | ❌ | ✅ | ✅ | ❌ 不会** |
| 网络/API 失败 | ⚠️ | ✅ | ✅ | ✅ | ❌ 不会 |
| 多次运行 Pipeline | ✅ | ✅ | ✅ | ✅ | ❌ 不会 |

**注释**：
- \* 崩溃时实时更新已处理的文件，未处理的可通过手动触发或 CLI 补齐
- \*\* 直接拷贝文件需要用户手动触发索引更新（Dashboard 会显示缺失文件数）

## 监控和可见性

### Dashboard 索引状态卡片

实时显示索引健康状态：

```
📊 RAG Index Status
  Indexed Files: 18,181 / 18,181
  Total Chunks: 134,661
  Last Updated: May 8, 11:30 PM
  Status: ✅ Synced
  
  [🔄 Rebuild Index]
```

**状态指示**：
- ✅ Synced - 所有文件都已索引
- ⚠️ N files missing - 有文件未索引
- ❌ Error - 索引损坏或不可用

### Pipeline Log 高亮

索引相关事件在 Pipeline log 中高亮显示：

```
🔄 Rebuilding RAG index...
✅ RAG index rebuilt successfully
❌ Index rebuild failed: [error message]
```

### SSE 实时事件

前端通过 SSE 接收索引更新事件：

- `rebuilding_index` - 开始重建索引
- `index_rebuilt` - 索引重建成功
- `index_error` - 索引重建失败

## 故障恢复

### 场景 1：索引更新失败

**症状**：Dashboard 显示 "⚠️ N files missing"

**恢复步骤**：
1. 点击 Dashboard 的 "🔄 Rebuild Index" 按钮
2. 或运行 CLI 脚本：`python3 rebuild_index_robust.py`

### 场景 2：Pipeline 崩溃后索引不完整

**症状**：corpus 中有新文件，但 manifest 中没有

**恢复步骤**：
1. 重启 Flask 应用
2. 访问 Dashboard，查看索引状态
3. 点击 "🔄 Rebuild Index" 按钮

### 场景 3：直接拷贝了大量 JSON 文件

**症状**：Dashboard 显示大量缺失文件

**恢复步骤**：
1. 运行 CLI 脚本（推荐，适合大批量）：
   ```bash
   nohup python3 rebuild_index_robust.py > rebuild.log 2>&1 &
   tail -f rebuild.log  # 查看进度
   ```
2. 或通过 Admin Panel 手动触发（适合少量文件）

## 性能考虑

### 实时更新的开销

- **单个文件**：2-3 秒（Jina API 调用）
- **增量检查**：<0.1 秒（SHA256 计算 + manifest 查询）
- **总开销**：每篇论文增加 2-3 秒

**是否可接受？**
- ✅ 可接受 - 论文提取本身需要 30-60 秒
- ✅ 增量索引很快，不会重复计算已索引的文件
- ✅ 鲁棒性收益远大于性能开销

### 批量更新的性能

如果担心实时更新的开销，可以改为批量更新（每 N 篇更新一次）：

```python
# 每 10 篇或最后一篇时更新
if status == "success" and (done % 10 == 0 or done == total):
    _refresh_index(DATA_DIR, force=False)
```

**当前实现**：每篇实时更新（优先鲁棒性）

## 测试验证

### 测试 1：正常 Pipeline 流程

```bash
# 1. 上传 ZIP 文件
# 2. 运行 Pipeline
# 3. 检查 Dashboard 索引状态
# 预期：所有文件都已索引，状态显示 "✅ Synced"
```

### 测试 2：中途停止 Pipeline

```bash
# 1. 运行 Pipeline
# 2. 处理几篇论文后点击 "Stop"
# 3. 检查 Dashboard 索引状态
# 预期：已处理的文件都已索引
```

### 测试 3：手动触发索引重建

```bash
# 1. 直接拷贝 JSON 文件到 data/corpus/
# 2. 访问 Dashboard，查看缺失文件数
# 3. 点击 "🔄 Rebuild Index"
# 4. 等待完成，检查状态
# 预期：所有文件都已索引
```

### 测试 4：CLI 脚本重建

```bash
# 1. 运行脚本
python3 rebuild_index_robust.py

# 2. 观察实时输出
# 预期：显示进度，最终所有文件都已索引
```

## 总结

通过**四层防护机制**（实时更新 + 结束时兜底 + 手动触发 + CLI 脚本），确保：

✅ **完全鲁棒**：任何情况下都不会遗漏文件
✅ **实时同步**：索引始终与 corpus 保持一致
✅ **可监控**：Dashboard 实时显示索引健康状态
✅ **可恢复**：多种方式手动修复索引
✅ **高性能**：增量索引，只处理新文件

**核心原则**：宁可多次更新（幂等操作），也不能遗漏文件。
