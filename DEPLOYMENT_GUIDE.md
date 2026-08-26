# NutriMaster 生产部署与切流指南

本文是当前生产架构的唯一部署口径。旧的双 Python 服务、Web 内建索引和
cron 直接重建方案已停用。

## 不可变的生产边界

- 只有一个单 worker FastAPI 进程，监听 `127.0.0.1:5000`。它共享一套
  `WebServices` / `ToolRegistry` / `JinaRetriever`，同时服务
  `/api/query`、`/api/pi/query`、Admin、个人库和实验路由。
- Pi 是监听 `127.0.0.1:8787` 的 Node sidecar，只负责编排和流式输出，
  不加载 Python 索引。
- `5002` 只能用于离线预检之外的短期迁移诊断，不能与正式 unified
  同时常驻。启动新的 `5000` 前，必须先停止并禁用已确认归属的旧
  `5000`/`5002` 进程或单元，并确认两个端口都没有监听器。
- dense、紧凑 BM25、字段关键词 SQLite FTS 和 SQLite Graph RAG 必须全部
  启用。不得以关闭某个检索分支作为常态 OOM 解决方案。
- Web 进程不得在启动、请求、单篇论文完成或 Pipeline 结束时直接构建
  索引。所有变更都先写入 durable queue，且只由隔离的
  `nutrimaster-index-builder.service` 构建。
- 生产 Admin Pipeline 同时只允许一个实例，并且 `max_workers=1`。

## 为什么不能再运行 5000 + 5002

旧 BM25 文件在磁盘上约 600 MB，但反序列化后的 Python 字典和 token list
实测占用约 4.39 GiB。迁移前只有一份 Python/RAG 对象，内存只是勉强不越线；
迁移后 `5000` 和 `5002` 重复加载 chunks、dense 和 Graph，再叠加索引构建临时对象，
就会超过生产机内存。因此修复是合并服务、紧凑/内存映射索引和隔离 builder，
而不是删减检索功能。

## 切流前预检

1. 在变更窗口内操作，记录旧发布目录、Nginx 配置、`CURRENT` 值和当前
   generation ID。
2. 确认 `.env` 可读，外部密钥和 Supabase 认证配置齐全。
3. 使用短命校验进程检查完整 generation：

   ```bash
   .venv/bin/python -m nutrimaster.rag.index_builder_cli verify-active
   ```

4. 检查 RAM、swap 和磁盘，但不要用“当前空闲看起来足够”代替 builder 预检：

   ```bash
   free -h
   df -h /root/code/nutrimaster/data/index
   ```

当前 103,024 chunks 的完整 generation 约 3.1 GiB。builder 在开始写入前会按实际
语料计算“完整新 generation + 两份 dense workspace + 稳定 corpus snapshot +
1 GiB 安全余量”；该语料当前需要额外约 5.833 GiB。数字会随语料变化，
以每次预检结果为准。

生产必须保护 `CURRENT` 和上一代可回滚 generation。保留两代后磁盘余量会
明显变窄；如果预检拒绝新构建，不得删除这两代中任何一代强行通过。
应扩容，或经运维明确批准后将更旧的已验证备份移出本机。

## systemd 安装与资源边界

将 `deploy/systemd/` 中的 `nutrimaster.slice`、unified、Pi、builder service 和
builder path unit 作为同一组发布。仔细核对 unit 中的工作目录和脚本路径后：

```bash
systemctl daemon-reload
systemctl enable --now nutrimaster-index-builder.path nutrimaster-pi.service
```

此时先不要直接启动 unified。记录并停止已确认归属的旧 `5000`/`5002`
服务，确认两个端口均关闭后，再执行：

```bash
systemctl enable --now nutrimaster-unified.service
```

启动脚本会在加载索引前检查这两个监听端口；任一仍被占用或无法执行检查时
都会 fail closed，避免新旧 Python 在端口绑定失败前短暂重复加载索引。

生产主机是 systemd 239 + cgroup v1。必须检查生效值，不能只看 unit 文本：

```bash
systemctl show nutrimaster-unified.service nutrimaster-pi.service \
  nutrimaster-index-builder.service -p Slice -p MemoryLimit
cat /sys/fs/cgroup/memory/nutrimaster.slice/memory.limit_in_bytes
```

期望硬限制为 unified 3 GiB、Pi 768 MiB、builder 2560 MiB，共享 slice
5632 MiB。cgroup v1 上 `MemoryHigh` 和 `MemorySwapMax` 不是有效的服务级控制；
该生产主机存在 swap，不得声称单元已将 swap 强制禁用。

## 索引构建与激活

Admin 上传后的 Pipeline、单篇预览或手动 Rebuild 只向
`RAG_INDEX_DIR/builder-state/jobs/pending/` 原子写入任务。手动 Rebuild
接口返回 HTTP `202` / `queued`；单篇和 Pipeline 通过响应/SSE 返回 job ID。
这些信号都只表示持久化排队成功，不表示索引已构建或已生效。

builder 持有全局排他锁，串行执行：

1. 磁盘预检和稳定语料快照；
2. 构建 dense、紧凑 BM25、字段关键词 FTS 和 Graph 全套产物；
3. 校验 checksum、shape、corpus fingerprint 和 SQLite 完整性；
4. 发布不可变 generation，原子切换 `CURRENT`；
5. 受控重启 unified，并等待 `/api/health` 报告精确 generation ID；
6. 激活失败时自动切回旧 `CURRENT`、重启并验证回滚 generation。

独立查看 durable 状态：

```bash
.venv/bin/python -m nutrimaster.rag.index_builder_cli status
journalctl -u nutrimaster-index-builder.service -n 200 --no-pager
```

严禁下列生产做法：

- 在 Web 环境中开启 build-on-start/build-on-miss/online reindex；
- Pipeline 每处理一篇就直接更新全局索引；
- 直接运行 `rebuild_index_robust.py`、`build_sparse_indexes` 或 Graph CLI 修改
  正在服务的索引目录；
- 用 cron/nohup/tmux 绕过 durable queue 和 systemd 内存限制；
- builder 运行时另启第二个 builder。

## 切流顺序

1. 离线确认 active generation 完整，启动并验证 Pi `8787`；记录旧 `5000`/`5002`
   的进程和单元归属，然后停止并禁用它们，确认两个监听端口均已关闭。
2. 启动新的 unified `5000`，确认 `/api/health` 报告预期 generation；若失败，
   在保持单 Python 约束的前提下按预留发布目录回滚。
3. 使用真实用户 token 验证 `/api/query` 和 `/api/pi/query` SSE，再验证个人库、
   Admin、CRISPR 和 gene-transfer 主要链路。
4. 将 Nginx 切换为 `deploy/nginx/nutrimaster-unified.conf`，先执行 `nginx -t`，
   再 reload。两个 SSE 路由都应指向同一 `127.0.0.1:5000` upstream；
   `/api/pi/internal/*` 必须在公网代理边界返回 404。
5. 小流量观察认证失败率、SSE 中断、500/502、延迟、unified/Pi RSS 和 cgroup 内存，
   并再次确认只有 `5000` 和 `8787` 监听。

```bash
ss -ltnp | rg ':(5000|5002|8787)\b'
curl --fail --silent http://127.0.0.1:5000/api/health
curl --fail --silent http://127.0.0.1:8787/healthz
```

## 回滚

- 索引激活失败由 builder 自动回滚。不要手工删除 generation，也不要
  在受控 restart 仍处于 `deactivating` 的排空窗口内再次 kill。
- 应用发布回滚使用切流前保留的已验证发布目录和 Nginx 配置，但仍必须
  遵守单 FastAPI `5000` + Node `8787` 边界，不得通过恢复常驻 `5002`
  来回滚。
- 回滚后重跑认证后 E2E，并检查 active generation ID 和端口所有者。

更详细的 builder 恢复、保留和状态语义见
[`docs/index_builder_operations.md`](docs/index_builder_operations.md)；Pi 边界和 SSE 合约见
[`docs/pi_runtime_migration.md`](docs/pi_runtime_migration.md)。
