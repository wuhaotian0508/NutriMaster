import time
from datetime import datetime
from pathlib import Path

from eval.configs import DEFAULT_RESULTS_FILE, EVAL_DIR, LOCAL_TIMEZONE, RESULT_DB_ID
from eval.datamanager.local_storage import LocalStorage
from eval.datamanager.notion_storage import NotionStorage


def save_local_results(results: list[dict], agent_name: str, version: str) -> Path | None:
    """保存本次评测结果到日期目录，并更新 latest。"""
    if not results:
        return None

    today = datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d")
    local_dir = EVAL_DIR / "data" / today / agent_name
    local_dir.mkdir(parents=True, exist_ok=True)
    local_file = local_dir / f"{version}.jsonl"

    LocalStorage.save_results(str(local_file), results)
    LocalStorage.save_results(str(DEFAULT_RESULTS_FILE), results)

    print(f"\n✓ 已保存到本地: {local_file}")
    print(f"✓ 已更新 latest: {DEFAULT_RESULTS_FILE}")
    print("  未写入 Notion；确认满意后运行: python -m eval.main push")
    return local_file


def push_results(file: str = str(DEFAULT_RESULTS_FILE), dry_run: bool = False) -> None:
    result_file = Path(file)
    if not result_file.exists():
        raise FileNotFoundError(f"结果文件不存在: {result_file}")

    results = LocalStorage.load_results(str(result_file))
    q_ids = [r.get("题目编号") for r in results if r.get("题目编号") is not None]
    agents = sorted({str(r.get("Agent名称", "")) for r in results if r.get("Agent名称")})
    versions = sorted({str(r.get("版本", "")) for r in results if r.get("版本")})

    print(f"结果文件: {result_file}")
    print(f"目标 Notion 结果库: {RESULT_DB_ID}")
    print(f"总条数: {len(results)}")
    print(f"Agent: {', '.join(agents) or '-'}")
    print(f"版本: {', '.join(versions) or '-'}")
    if q_ids:
        print(f"题号范围: {min(q_ids)} ~ {max(q_ids)}")

    if dry_run:
        print("dry-run: 未写入 Notion")
        return

    storage = NotionStorage()
    saved = failed = 0

    for result in results:
        last_error = None
        for attempt in range(1, 4):
            try:
                storage.save_result(RESULT_DB_ID, result)
                saved += 1
                break
            except Exception as e:
                last_error = e
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
        else:
            failed += 1
            print(f"✗ Notion 单条保存失败: 题目 {result.get('题目编号')} - {last_error}")

    if failed:
        print(f"✗ 上传到 Notion 部分失败: 成功 {saved} 条，失败 {failed} 条")
    else:
        print(f"✓ 已上传到 Notion: {saved} 条结果")
