#!/usr/bin/env python3
"""
RunManager 功能测试
"""

import asyncio
import json
from pathlib import Path
from eval.run_manager import RunManager


async def test_basic_functionality():
    """测试基本功能"""
    print("=" * 60)
    print("测试 RunManager 基本功能")
    print("=" * 60)

    # 创建 RunManager
    manager = RunManager(
        max_concurrency=2,
        max_retries=2,
        checkpoint_dir=".test_checkpoints",
        auto_save_interval=2,
        enable_progress=False,
    )

    # 测试 1: 检查点路径
    print("\n[测试 1] 检查点路径生成")
    path = manager.get_checkpoint_path("TestAgent", "v1")
    print(f"  路径: {path}")
    assert path.name == "TestAgent_v1.jsonl"
    print("  ✓ 通过")

    # 测试 2: 保存和加载检查点
    print("\n[测试 2] 保存和加载检查点")
    test_results = [
        {"题目编号": 1, "总分": 8.5, "满分": 10.0, "Agent名称": "TestAgent", "版本": "v1"},
        {"题目编号": 2, "总分": 9.0, "满分": 10.0, "Agent名称": "TestAgent", "版本": "v1"},
    ]
    manager.save_checkpoint("TestAgent", "v1", test_results, append=False)
    loaded = manager.load_checkpoint("TestAgent", "v1")
    print(f"  保存: {len(test_results)} 条")
    print(f"  加载: {len(loaded)} 条")
    assert len(loaded) == 2
    assert 1 in loaded and 2 in loaded
    print("  ✓ 通过")

    # 测试 3: 检查点状态
    print("\n[测试 3] 检查点状态")
    status = manager.get_checkpoint_status("TestAgent", "v1")
    print(f"  已完成: {status['已完成']}")
    print(f"  成功: {status['成功']}")
    print(f"  失败: {status['失败']}")
    assert status["已完成"] == 2
    assert status["成功"] == 2
    print("  ✓ 通过")

    # 测试 4: 过滤剩余题目
    print("\n[测试 4] 过滤剩余题目")
    all_questions = [
        {"编号": 1, "正文": "问题1"},
        {"编号": 2, "正文": "问题2"},
        {"编号": 3, "正文": "问题3"},
    ]
    remaining = manager.filter_remaining_questions(all_questions, loaded, retry_failed=False)
    print(f"  总题目: {len(all_questions)}")
    print(f"  已完成: {len(loaded)}")
    print(f"  剩余: {len(remaining)}")
    assert len(remaining) == 1
    assert remaining[0]["编号"] == 3
    print("  ✓ 通过")

    # 测试 5: 重试机制
    print("\n[测试 5] 重试机制")
    attempt_count = 0

    async def failing_task():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise ValueError(f"模拟失败 (尝试 {attempt_count})")
        return "成功"

    result = await manager.run_with_retry(failing_task, max_retries=3, retry_delay=0.1)
    print(f"  尝试次数: {attempt_count}")
    print(f"  结果: {result}")
    assert attempt_count == 3
    assert result == "成功"
    print("  ✓ 通过")

    # 测试 6: 并行执行
    print("\n[测试 6] 并行执行")

    async def mock_task(task_id):
        await asyncio.sleep(0.1)
        return f"任务 {task_id} 完成"

    tasks = [lambda i=i: mock_task(i) for i in range(5)]
    results = await manager.run_batch(tasks, desc="测试任务", use_retry=False)
    print(f"  任务数: {len(tasks)}")
    print(f"  结果数: {len(results)}")
    assert len(results) == 5
    print("  ✓ 通过")

    # 清理
    print("\n[清理] 删除测试检查点")
    manager.clear_checkpoint("TestAgent", "v1")
    Path(".test_checkpoints").rmdir()
    print("  ✓ 完成")

    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_basic_functionality())
