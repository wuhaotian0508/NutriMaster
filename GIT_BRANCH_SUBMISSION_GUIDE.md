# Git 分支提交参考指南

本文档总结一次完整、安全的 Git 分支提交流程，适用于本仓库中 `feature/<short-topic>` 风格的开发分支提交。

**当前分支**: `feature/tooluse-login-fix`  
**主分支**: `main`  
**Git 用户**: wuhaotian0508

## 1. 推荐分支命名

参考 `DEVELOPMENT.md`，推荐分支命名格式：

```text
feature/<short-topic>
fix/<short-topic>
refactor/<short-topic>
data/<short-topic>
docs/<short-topic>
```

如果是新增功能，使用：

```bash
git switch -c feature/<short-topic>
```

示例：

```bash
git switch -c feature/tooluse-login-fix
```

注意：命令里的 `<short-topic>` 是占位符，实际执行时不能保留尖括号。

错误示例：

```bash
git push -u origin feature/<short-topic>
```

正确示例：

```bash
git push -u origin feature/tooluse-login-fix
```

也可以直接推送当前分支：

```bash
git push -u origin HEAD
```

## 2. 从最新主分支创建开发分支

通常流程：

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/<short-topic>
```

如果主分支不是 `main`，请替换为实际基线分支。

## 3. 理解 `git status --short`

常见状态含义：

```text
 M file.py     工作区已修改，但还没有 git add
M  file.py     已经 git add，进入暂存区
MM file.py     已暂存过，但之后又继续修改了
?? file.py     未跟踪文件，git 还没有管理它
!! file.py     被 .gitignore 忽略的文件
D  file.py     删除已经进入暂存区
 D file.py     工作区删除了，但还没有暂存
```

重点：

- `git commit` 只提交**暂存区**中的内容。
- ` M` 开头的文件不会被提交，除非先执行 `git add`。
- `??` 文件不会被提交，除非先执行 `git add`。
- `!!` 文件已经被忽略，正常不会进入提交。

## 4. 为什么有些改动没有进入 commit

如果执行：

```bash
git commit -m "feat: <summary>"
```

但发现只有少数文件被提交，原因通常是：

> 只有那些已经 `git add` 进入暂存区的文件会被 commit。

例如：

```text
D  data/corpus/.gitkeep
D  data/index/.gitkeep
 M pyproject.toml
 M src/nutrimaster/agent/agent.py
```

这里真正会被提交的是前两个 `D  ` 文件；后两个 ` M` 文件只是工作区修改，尚未暂存。

## 5. `.gitignore` 的关键规则

`.gitignore` 只会忽略**尚未被 Git 跟踪**的文件。

如果某个文件已经被 Git 跟踪，即使后来写入 `.gitignore`，它仍然会出现在 `git status` 里。

例如：

```text
 M data/interactions/interactions.jsonl
```

即使 `.gitignore` 中有：

```gitignore
/data
```

这个文件仍然显示修改，说明它以前已经被 Git 跟踪。

如果确认某个已跟踪文件以后不应再入库，可以从 Git 索引移除，但保留本地文件：

```bash
git rm --cached path/to/file
```

目录示例：

```bash
git rm -r --cached data/interactions
```

谨慎使用：这会在下一次提交中表现为“从仓库删除这些文件”，但本地文件会保留。

## 6. 忽略大目录和本地文件

本仓库中常见不应提交的内容包括：

- `.env`、密钥、token、服务账号
- `*.ipynb`
- `.ipynb_checkpoints/`
- `no-use/`
- `artifacts/`
- 大型索引生成物，如 `*.pkl`、`*.npy`
- 本地运行时数据、临时文件、缓存文件

示例 `.gitignore` 规则：

```gitignore
*.ipynb
.ipynb_checkpoints/
/no-use
/artifacts
*.log
.pytest_cache/
**/__pycache__/
```

对于 `data/`，需要特别谨慎。本仓库的 `DEVELOPMENT.md` 中说明：

- `data/corpus/` 是主语料目录
- `data/index/` 是索引目录
- `data/personal_lib/`、`data/user_skills/` 是运行时用户数据

因此不要随意提交大规模 `data/` 生成物；是否整目录忽略，需要结合项目约定判断。

## 7. 提交前检查目录体积

查看仓库下各个顶层目录大小：

```bash
du -sh -- */ 2>/dev/null | sort -hr
```

如果发现大目录，例如 `no-use/`、`data/`、`artifacts/`，提交前应确认它们不会被误加入 Git。

## 8. 安全提交流程

不要直接使用：

```bash
git add .
```

更安全的做法是只添加确定要提交的文件。

示例：

```bash
git add .gitignore
git add pyproject.toml
git add src/nutrimaster/agent/agent.py
git add src/nutrimaster/agent/tools/__init__.py
git add src/nutrimaster/agent/tools/experiment.py
git add src/nutrimaster/agent/tools/rag.py
git add src/nutrimaster/rag/evidence.py
git add src/nutrimaster/web/static/app.js
git add src/nutrimaster/web/static/style.css
git add uv.lock
```

如果新文件也属于本次改动，再单独添加：

```bash
git add EVOMASTER_EVAL_GUIDE.md eval_evomaster.py
```

提交前必须检查暂存区：

```bash
git status --short
git diff --cached --stat
```

如果要看具体内容：

```bash
git diff --cached
```

确认无误后提交：

```bash
git commit -m "feat: improve agent tool login behavior"
```

推送：

```bash
git push -u origin feature/tooluse-login-fix
```

或：

```bash
git push -u origin HEAD
```

## 9. 如果不小心提交错了怎么办

如果刚提交完，尚未 push，并且想撤销最近一次 commit 但保留本地改动：

```bash
git reset --mixed HEAD~1
```

效果：

- 撤销最近一次 commit
- 保留本地文件修改
- 清空暂存区，需要重新 `git add`

然后恢复不该提交的删除：

```bash
git restore data/corpus/.gitkeep data/index/.gitkeep
git restore data/interactions/feedback.jsonl data/interactions/interactions.jsonl
```

重新只添加正确文件：

```bash
git add .gitignore
git add pyproject.toml
git add src/nutrimaster/agent/agent.py
git add src/nutrimaster/agent/tools/__init__.py
git add src/nutrimaster/agent/tools/experiment.py
git add src/nutrimaster/agent/tools/rag.py
git add src/nutrimaster/rag/evidence.py
git add src/nutrimaster/web/static/app.js
git add src/nutrimaster/web/static/style.css
git add uv.lock
```

检查：

```bash
git status --short
git diff --cached --stat
```

重新提交并推送：

```bash
git commit -m "feat: improve agent tool login behavior"
git push -u origin feature/tooluse-login-fix
```

## 10. 一套完整可复制命令

如果之前错误提交了 data 删除，且还没有 push，可以使用：

```bash
# 撤销最近一次错误 commit，但保留本地改动
git reset --mixed HEAD~1

# 确认当前分支
git branch --show-current

# 恢复不想提交的 data 删除
git restore data/corpus/.gitkeep data/index/.gitkeep
git restore data/interactions/feedback.jsonl data/interactions/interactions.jsonl

# 只添加本次真正要提交的文件
git add .gitignore
git add pyproject.toml
git add src/nutrimaster/agent/agent.py
git add src/nutrimaster/agent/tools/__init__.py
git add src/nutrimaster/agent/tools/experiment.py
git add src/nutrimaster/agent/tools/rag.py
git add src/nutrimaster/rag/evidence.py
git add src/nutrimaster/web/static/app.js
git add src/nutrimaster/web/static/style.css
git add uv.lock

# 如果这两个文件也属于本次改动，再添加
git add EVOMASTER_EVAL_GUIDE.md eval_evomaster.py

# 检查将要提交的内容
git status --short
git diff --cached --stat

# 提交
git commit -m "feat: improve agent tool login behavior"

# 推送当前分支
git push -u origin HEAD
```

## 11. 提交前最小检查清单

提交前至少确认：

- [ ] 当前在正确分支上：`git branch --show-current`
- [ ] 没有误提交 `.env`、密钥、token
- [ ] 没有误提交 `*.ipynb`
- [ ] 没有误提交 `no-use/`、`artifacts/` 等本地大目录
- [ ] 没有误提交运行时数据或大索引文件
- [ ] `git diff --cached --stat` 只包含本次想提交的文件
- [ ] commit message 已替换占位符，不含 `<summary>`、`<short-topic>`
