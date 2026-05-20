# NutriMaster Project Instructions

## 终端命令执行规范
所有终端命令请用 tmux 执行，确保长时间运行的任务不会因连接断开而中断。

## 服务器信息

### 阿里云生产服务器
- **SSH 别名**: `myserver` 或 `ali`
- **地址**: 39.108.180.113
- **用户**: root
- **项目路径**: `/root/code/nutrimaster`
- **连接方式**: `ssh myserver` 或 `ssh ali`

### 本地开发环境
- **项目路径**: `/data/haotianwu/biojson`

## 常见问题

### setuptools 构建错误：`package directory 'src/eval' does not exist`
**原因**: `pyproject.toml` 配置不当，导致 setuptools 尝试将 `eval/` 目录打包。

**解决方案**: 
- `eval/` 目录是本地评测脚本，**不应该被打包**
- 已修改 `pyproject.toml` 为 `where = ["src"]`，只打包 `nutrimaster`
- 已删除 eval 相关的 CLI 入口点（`nutribench`, `eval-checkpoint`）

**使用 eval 脚本**:
```bash
# 在项目根目录运行
cd /data/haotianwu/biojson
python -m eval.main [args]
python quick_stats.py [args]
```

**同步到服务器**:
```bash
# 推送更改到服务器
git add pyproject.toml
git commit -m "fix: remove eval from package configuration, keep as local scripts only"
git push

# 在服务器上拉取更新
ssh myserver "cd /root/code/nutrimaster && git pull && uv pip install -e ."
```