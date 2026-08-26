# RAG 索引一致性与恢复设计

当前方案不再使用“每篇论文在 Web 内立即重建 + Pipeline 结束再重建 +
cron/CLI 兜底”。该旧方案会在请求服务中反复加载语料级对象，并且可与查询、
第二 Python 服务或另一次重建叠加，是生产 OOM 的重要诱因。

新方案的目标是：对一份稳定 corpus 快照构建一个经过完整校验的不可变
generation，只在新 generation 可用时原子激活，并在激活失败时保留可验证回滚点。

## 核心不变式

1. 生产只有一个 FastAPI `5000` 进程加载全局索引；Pi Node `8787` 不加载
   索引。新的 unified 启动前，旧 `5000`/`5002` 必须先下线，避免瞬时
   重复驻留索引。
2. 一个 generation 必须同时包含 chunks、dense embeddings/norms、紧凑 BM25、
   字段关键词 SQLite FTS 和 Graph SQLite。生产不允许缺任何分支启动。
3. Web 只读 active generation。它不在启动、在线请求、单篇回调或 Pipeline 结束时
   直接写入检索产物。
4. 任何 Admin 索引操作都先持久化排队；只有受 systemd 内存限制的 isolated
   builder 有权构建和发布。
5. builder 全局串行，同时只允许一次构建。Admin Pipeline 也只允许一个实例且
   `max_workers=1`。
6. `CURRENT` 只能指向完整验证的最终 generation，不能指向 staging、symlink 链
   或未发布工作目录。

## 触发与状态语义

下列操作只提交 durable job：

- Admin 单篇处理成功；
- Admin Pipeline 正常完成或在当前论文完成后停止；
- Admin Dashboard 手动 Rebuild。

任务先原子写入：

```text
RAG_INDEX_DIR/builder-state/jobs/pending/
```

HTTP `202` / `queued` 的语义仅是“请求已持久化”。它不代表已开始构建、已发布
或已在 Web 中生效。必须根据 durable status 和 `/api/health` 判定最终结果。

主要状态为：

```text
queued -> preflight -> snapshotting -> building -> activating
       -> succeeded | awaiting_activation | failed
```

## 完整 generation 构建

builder 持有排他锁后执行：

1. 清理只属于上次中断构建的未发布 staging/snapshot；
2. 根据当前 corpus 规模执行磁盘预检；
3. 创建稳定、私有的 JSON corpus 快照；
4. 构建 chunks、dense embeddings/norms、紧凑 CSR BM25、字段关键词 FTS 和 Graph；
5. 检查文件 hash、数组 shape/有限值、corpus fingerprint、BM25 契约和 SQLite 完整性；
6. 将完整目录发布为不可变 generation，并原子替换 `CURRENT`；
7. 受控重启 unified，等待 `/api/health` 报告精确的新 generation ID。

只有第 7 步验证成功才记录 `succeeded`。这比“manifest 已写入”更强：它确认
实际服务进程已加载指定 generation。

## 磁盘预检和两代保留

预检在任何 staging 写入前完成，要求足够空间容纳：

- 一份完整新 generation；
- 两份 dense workspace（含 atomic-save 临时件）；
- 一份稳定 corpus snapshot；
- 1 GiB 安全余量。

当前 103,024 chunks 的参考值是：generation 约 3.1 GiB，构建额外需求约
5.833 GiB。该数值不是固定配置，必须以每次 builder 按实际语料计算的结果为准。

激活成功后保留策略保护：

- `CURRENT` 指向的正在服务 generation；
- 紧邻的上一代 serving/rollback generation。

清理只能删除更旧、已验证、命名为 64 位十六进制的最终 generation。它不得删除
`CURRENT`、rollback generation、symlink 或无效目录。如果两代被保护时预检仍失败，
应扩容或将经运维批准的更旧归档移出本机；不得删除任何受保护世代强行开工。

## 中断、OOM 和激活失败

- 构建或校验失败：任务记为 `failed`，`CURRENT` 保持不变。
- 激活失败：builder 原子切回记录的旧 generation，重启 unified，并验证回滚 ID。
- OOM、SIGTERM 或被迫中断：systemd `ExecStopPost` 读取 durable activation state，执行同样的
  回滚恢复，并在下次预检前清理不可能被 `CURRENT` 引用的私有 staging/snapshot。
- 手工 maintenance mode：`NUTRIMASTER_INDEX_BUILDER_AUTO_ACTIVATE=false` 只能记录
  `awaiting_activation`，在 Web 实际报告新 ID 前不得标记成功。

unified 最长优雅停止窗口为 360 秒，builder 恢复窗口为 660 秒。受控激活期间
`deactivating` 持续数分钟可能是正常排空；在窗口内再次 kill 会破坏可恢复语义。

## 监控与验证

查看 durable 状态和 builder 日志：

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli status
journalctl -u nutrimaster-index-builder.service -n 200 --no-pager
```

校验 active generation：

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli verify-active
curl --fail --silent http://127.0.0.1:5000/api/health
```

Admin Dashboard 的 corpus/indexed 计数只是可观测信号，不能取代 generation 合同校验和
Web 健康确认。

## 明确禁止的旧路径

生产不得：

- 在 `_on_paper_done()` 中直接调用 `_refresh_index()` 构建全局索引；
- 在 Pipeline 线程中同步构建或等待索引完成；
- 设置 Web build-on-start/build-on-miss/online reindex 开关；
- 通过 cron、nohup、tmux 或另一个 Python 进程直接运行
  `rebuild_index_robust.py`、`build_sparse_indexes` 或 Graph CLI 写入 active index；
- 并行运行两个 builder；
- 为规避 OOM 而关闭 BM25、字段关键词或 Graph 功能。

运维细节见 [`docs/index_builder_operations.md`](docs/index_builder_operations.md)，生产切流顺序见
[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) 和
[`DEPLOYMENT_CHECKLIST.md`](DEPLOYMENT_CHECKLIST.md)。
