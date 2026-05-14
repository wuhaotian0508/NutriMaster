#!/bin/bash
# 补跑5/6缺失的题目

# 5/6缺失的题目编号
MISSING_QUESTIONS=(8 10 12 16 19 20 25 26 27 32 33 35 36 38 39 41 42 43 44 46 49 51 52 53 61 65 66 67)

# 设置环境变量（从当前环境继承）
export EVAL_VERSION="${EVAL_VERSION:-v3}"
export EVAL_AGENTS="${EVAL_AGENTS:-evomaster}"
export NUM_RUNS="${NUM_RUNS:-1}"
export AGENT_CONCURRENCY="${AGENT_CONCURRENCY:-10}"
export JUDGE_CONCURRENCY="${JUDGE_CONCURRENCY:-10}"

# 创建临时Python脚本来只跑指定题目
cat > /tmp/rerun_missing.py << 'PYTHON_SCRIPT'
import os
import sys
sys.path.insert(0, '/data/haotianwu/biojson')

from eval_pipeline import *

# 覆盖fetch_questions函数，只返回指定题目
original_fetch_questions = fetch_questions
MISSING_IDS = [8, 10, 12, 16, 19, 20, 25, 26, 27, 32, 33, 35, 36, 38, 39, 41, 42, 43, 44, 46, 49, 51, 52, 53, 61, 65, 66, 67]

def fetch_questions_filtered():
    all_questions = original_fetch_questions()
    filtered = [q for q in all_questions if q['编号'] in MISSING_IDS]
    print(f"过滤后题目: {len(filtered)} 道 (编号: {sorted(q['编号'] for q in filtered)})")
    return filtered

# 替换函数
import eval_pipeline
eval_pipeline.fetch_questions = fetch_questions_filtered

# 运行评测
import asyncio
asyncio.run(main())
PYTHON_SCRIPT

echo "开始补跑缺失的 ${#MISSING_QUESTIONS[@]} 道题目..."
echo "题目编号: ${MISSING_QUESTIONS[@]}"
echo ""
echo "环境变量:"
echo "  EVAL_VERSION=$EVAL_VERSION"
echo "  EVAL_AGENTS=$EVAL_AGENTS"
echo "  AGENT_CONCURRENCY=$AGENT_CONCURRENCY"
echo "  JUDGE_CONCURRENCY=$JUDGE_CONCURRENCY"
echo ""

python /tmp/rerun_missing.py
