# NutriMaster 生产上线检查表

这份检查表用于受控灰度/变更窗口。任何一个阻断项未满足，都不应全量切流。

## 1. 架构与配置

- [ ] 生产只配置一个 FastAPI worker，监听 `127.0.0.1:5000`。
- [ ] `/api/query`、`/api/pi/query`、Admin、认证、个人库和实验路由由该同一进程提供。
- [ ] Pi Node sidecar 只监听 `127.0.0.1:8787`，未加载 Python 索引。
- [ ] 已明确旧 `5000`/`5002` 的进程和单元所有者，并安排在新 unified 启动前
      停止、禁用和确认端口关闭；不会让两个 Python 索引进程重叠。
- [ ] dense、BM25、字段关键词和 Graph RAG 全部启用；未设置线上功能降级开关。
- [ ] `start-unified-production.sh` 中 BM25/field/Graph 为 required，online/build-on-miss/build-on-start 为 off。
- [ ] Admin Pipeline 同时只能运行一个，`NUTRIMASTER_PIPELINE_DEFAULT_WORKERS=1` 且
      `NUTRIMASTER_PIPELINE_MAX_WORKERS=1`。

## 2. 代码、配置和索引预检

- [ ] 已通过 `deploy/build_release.py verify` 验证内容寻址发布包；包内无 `.env`、
      密钥、索引、语料、报告、虚拟环境、`node_modules` 或 `.pi-agent`，且
      `expected_model=deepseek-v4-flash`。
- [ ] 已通过 `deploy/preflight_release.py` 的生产只读 admission；发布目录不存在，
      每个语料 SHA、旧 5000 manifest/chunk 范围和 embeddings shape 均一致。
- [ ] 旧 5000/8787 与候选使用同一条已通过真实请求的 LLM gateway；stage 的原子
      交接（如需）有独占 0600 备份，且返回模型精确为 `deepseek-v4-flash`。
- [ ] 旧、候选两份生产 `.env` 权限均为 `0600`，`MAIN_MODEL=deepseek-v4-flash`，
      Pi 模型未覆盖为其他模型。
- [ ] 已记录待发布 commit/发布目录、旧 Nginx 配置、旧 `CURRENT` 值和 active generation ID。
- [ ] `.env` 权限和 OPENAI/Jina/Supabase 必需配置已验证，日志不会打印密钥。
- [ ] 非集成 Python 测试、Node 测试/语法检查、脚本 `bash -n`、`git diff --check`
      均通过。
- [ ] 已对 active generation 执行：

  ```bash
  .venv/bin/python -m nutrimaster.rag.index_builder_cli verify-active
  ```

- [ ] 校验结果中 dense shape/norms、紧凑 BM25、field FTS、Graph SQLite、corpus fingerprint
      和 checksum 全部一致。
- [ ] `CURRENT` 指向已验证的不可变 generation，而不是 staging 目录或可变根目录产物。

## 3. 内存与磁盘阻断项

- [ ] 已检查 `free -h` 和索引所在文件系统的 `df -h`。
- [ ] systemd 生效硬限制为 unified 3 GiB、Pi 768 MiB、builder 2560 MiB、共享 slice
      5632 MiB，且已在 cgroup v1 控制器中核对实际值。
- [ ] 没有把 cgroup v1 上不生效的 `MemoryHigh`/`MemorySwapMax` 误当成强制隔离；
      已知生产主机仍有 swap。
- [ ] 已阅读 builder 的实时磁盘预检需求。当前 103,024 chunks 参考值为：
      完整 generation 约 3.1 GiB，额外构建空间约 5.833 GiB。
- [ ] 已预留 `CURRENT` + 上一代 rollback generation；没有计划删除这两代强行通过预检。
- [ ] 如果两代保留后空间过窄，已先扩容或获批将更旧归档移出本机。
- [ ] 首次上线已通过 `bootstrap-production.sh --preflight`；旧 5000 完整 dense
      索引是迁移源，临时 5002 的 18,580-file stale manifest 未被使用。
- [ ] bootstrap 在 2560 MiB systemd cgroup 中完成，`CURRENT` 指向通过完整 hash、
      sparse/field/Graph schema/fingerprint 校验的 generation；中断恢复无歧义。

## 4. systemd 与本机健康

- [ ] `nutrimaster.slice`、unified、Pi、builder service 和 builder path unit 已成组安装，
      工作目录/脚本路径均指向当前发布。
- [ ] builder path watcher 和 Pi 已先启用：

  ```bash
  systemctl enable --now nutrimaster-index-builder.path nutrimaster-pi.service
  ```

- [ ] 已停止并禁用已确认归属的旧 `5000`/`5002`，确认两个端口关闭后才启动
      `nutrimaster-unified.service`；启动前监听 guard 未被绕过。
- [ ] `systemctl status` 无启动循环、OOM 或 start-limit 失败。
- [ ] 未把 `Type=simple` 的 active 状态误当成监听就绪；Pi 8787 与 unified 5000
      均通过有界 health-readiness loop 后才进入下一步。
- [ ] `curl --fail --silent http://127.0.0.1:5000/api/health` 成功，且返回预期 generation ID。
- [ ] `curl --fail --silent http://127.0.0.1:8787/healthz` 成功。
- [ ] `ss -ltnp | rg ':(5000|5002|8787)\b'` 中的 PID/程序与预期一致。

## 5. 认证后 E2E

- [ ] 未认证请求访问 `/api/query` 和 `/api/pi/query` 均返回 401。
- [ ] 使用真实用户 token 验证旧 `/api/query` SSE，包括 citations 和 Graph 证据。
- [ ] 使用真实用户 token 验证 Pi `/api/pi/query` SSE，包括 tool call/result/callback。
- [ ] 验证个人库 PDF 上传、检索、删除和用户隔离。
- [ ] 验证 Admin ZIP 上传、串行 Pipeline、停止、排队索引和 durable 状态查询。
- [ ] 验证 CRISPR 和 gene-transfer 预览/确认流程。
- [ ] 公网 Nginx 边界下 `/api/pi/internal` 和 `/api/pi/internal/*` 均返回 404。

## 6. durable builder 验收

- [ ] Admin Rebuild 返回 HTTP 202、`status=queued` 和 job ID；团队未把此响应误认为构建成功。
- [ ] Pipeline 结束后只发生一次 durable enqueue，没有逐篇或 Web 内直接构建。
- [ ] `nutrimaster-index-builder.service` 持有排他锁，没有并行 builder。
- [ ] 状态顺序可观察：`queued` → `preflight` → `snapshotting` → `building` →
      `activating` → `succeeded`（或明确 `failed`/`awaiting_activation`）。
- [ ] 成功时 `/api/health` 报告新 generation ID；激活失败演练能回滚至旧 ID。
- [ ] 运维不会在 restart 的最长排空窗口内误 kill unified/builder。
- [ ] 没有 cron、nohup、tmux 或手工脚本直接调用 `rebuild_index_robust.py`、
      `build_sparse_indexes` 或 Graph CLI 写入生产 active index。

## 7. 单 Python 切换和 Nginx 切流

- [ ] 新 unified 启动前旧 `5000`/`5002` 已下线；没有通过并行常驻第二个
      Python 服务做“灰度”。
- [ ] `nginx -t` 通过，两个 SSE 路由均指向 `127.0.0.1:5000`、关闭 buffering，
      internal tool callback 未对外暴露。
- [ ] reload 后小流量观察认证、SSE、500/502、延迟和 cgroup/RSS 无异常。
- [ ] 切流后 `ss` 只显示 FastAPI `5000` 和 Pi Node `8787`，不存在 `5002` 监听器。
- [ ] 观察窗口内无 OOM kill、无服务反复重启，且 dense/BM25/field/Graph 检索都有实际命中。

## 8. Go / No-Go

只有在上述项目全部满足、回滚负责人和观察人明确时，才可进入受控灰度。
“不存在绝对风险”不是可验证的上线条件；可验证的条件是资源硬边界、功能 E2E、
可观测灰度和已演练回滚同时成立。
