#!/bin/bash
# Clash 节点自动健康检查 & 切换
# 检测当前节点是否可用，不可用则自动切换到最快的节点
# 用法: crontab 每 3 分钟跑一次
#   */3 * * * * /root/code/nutrimaster/scripts/clash_health_check.sh >> /root/clash/health_check.log 2>&1

CLASH_API="http://127.0.0.1:9090"
SELECTOR="%F0%9F%94%B0%20%E9%80%89%E6%8B%A9%E8%8A%82%E7%82%B9"  # 🔰 选择节点 (URL encoded)
TEST_URL="https://www.google.com"
TIMEOUT=5000
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

# 获取当前节点
CURRENT=$(curl -s "$CLASH_API/proxies/$SELECTOR" | python3 -c "import sys,json; print(json.load(sys.stdin).get('now',''))" 2>/dev/null)
if [ -z "$CURRENT" ]; then
    echo "$LOG_PREFIX Clash API 不可达，跳过"
    exit 1
fi

# 测试当前节点
CURRENT_ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$CURRENT'))")
RESULT=$(curl -s "$CLASH_API/proxies/$CURRENT_ENCODED/delay?timeout=$TIMEOUT&url=$TEST_URL" 2>/dev/null)
DELAY=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('delay',0))" 2>/dev/null)

if [ "$DELAY" -gt 0 ] 2>/dev/null; then
    echo "$LOG_PREFIX 当前节点 [$CURRENT] 正常，延迟 ${DELAY}ms"
    exit 0
fi

echo "$LOG_PREFIX 当前节点 [$CURRENT] 不可用，开始查找替代节点..."

# 获取所有节点列表
NODES=$(curl -s "$CLASH_API/proxies/$SELECTOR" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d.get('all', []):
    if p != 'DIRECT' and p != 'REJECT':
        print(p)
" 2>/dev/null)

# 逐个测试，找到延迟最低的
BEST_NODE=""
BEST_DELAY=99999

while IFS= read -r node; do
    [ -z "$node" ] && continue
    ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$node'''))")
    RESULT=$(curl -s "$CLASH_API/proxies/$ENCODED/delay?timeout=$TIMEOUT&url=$TEST_URL" 2>/dev/null)
    D=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('delay',0))" 2>/dev/null)

    if [ "$D" -gt 0 ] 2>/dev/null; then
        echo "$LOG_PREFIX   [$node] 延迟 ${D}ms"
        if [ "$D" -lt "$BEST_DELAY" ]; then
            BEST_DELAY=$D
            BEST_NODE=$node
        fi
    fi
done <<< "$NODES"

if [ -z "$BEST_NODE" ]; then
    echo "$LOG_PREFIX 所有节点均不可用！"
    exit 1
fi

# 切换到最快节点
curl -s -X PUT "$CLASH_API/proxies/$SELECTOR" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$BEST_NODE\"}" > /dev/null 2>&1

echo "$LOG_PREFIX 已切换到 [$BEST_NODE]（延迟 ${BEST_DELAY}ms）"
