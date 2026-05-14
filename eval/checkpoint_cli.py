#!/usr/bin/env python3
"""
检查点管理工具

使用示例:
  # 查看所有检查点
  python -m eval.checkpoint_cli list

  # 查看特定检查点状态
  python -m eval.checkpoint_cli status --agent NutriMaster --version v3

  # 清除检查点
  python -m eval.checkpoint_cli clear --agent NutriMaster --version v3

  # 导出检查点
  python -m eval.checkpoint_cli export --agent NutriMaster --version v3 -o results.jsonl
"""

import argparse
from eval.run_manager import RunManager


def cmd_list(args):
    """列出所有检查点"""
    manager = RunManager(checkpoint_dir=args.checkpoint_dir)
    checkpoints = manager.list_checkpoints()

    if not checkpoints:
        print("📭 没有找到检查点")
        return

    print(f"\n{'='*80}")
    print(f"{'Agent':<25} {'版本':<10} {'已完成':<10} {'成功':<10} {'失败':<10}")
    print(f"{'='*80}")

    for cp in checkpoints:
        print(
            f"{cp['agent']:<25} {cp['version']:<10} "
            f"{cp['已完成']:<10} {cp['成功']:<10} {cp['失败']:<10}"
        )

    print(f"{'='*80}\n")


def cmd_status(args):
    """查看检查点状态"""
    manager = RunManager(checkpoint_dir=args.checkpoint_dir)
    status = manager.get_checkpoint_status(args.agent, args.version)

    print(f"\n{'='*60}")
    print(f"Agent: {args.agent}")
    print(f"版本: {args.version}")
    print(f"{'='*60}")
    print(f"总题数: {status['总题数']}")
    print(f"已完成: {status['已完成']}")
    print(f"成功: {status['成功']}")
    print(f"失败: {status['失败']}")
    print(f"完成率: {status['完成率']}")
    print(f"{'='*60}\n")


def cmd_clear(args):
    """清除检查点"""
    manager = RunManager(checkpoint_dir=args.checkpoint_dir)

    if not args.force:
        confirm = input(f"确认清除检查点 {args.agent}/{args.version}? (y/N): ")
        if confirm.lower() != "y":
            print("❌ 已取消")
            return

    manager.clear_checkpoint(args.agent, args.version)
    print(f"✅ 已清除检查点: {args.agent}/{args.version}")


def cmd_export(args):
    """导出检查点"""
    manager = RunManager(checkpoint_dir=args.checkpoint_dir)
    manager.export_checkpoint(args.agent, args.version, args.output)


def main():
    parser = argparse.ArgumentParser(description="检查点管理工具")
    parser.add_argument(
        "--checkpoint-dir",
        default=".eval_checkpoints",
        help="检查点目录",
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # list 命令
    parser_list = subparsers.add_parser("list", help="列出所有检查点")
    parser_list.set_defaults(func=cmd_list)

    # status 命令
    parser_status = subparsers.add_parser("status", help="查看检查点状态")
    parser_status.add_argument("--agent", required=True, help="Agent 名称")
    parser_status.add_argument("--version", required=True, help="版本")
    parser_status.set_defaults(func=cmd_status)

    # clear 命令
    parser_clear = subparsers.add_parser("clear", help="清除检查点")
    parser_clear.add_argument("--agent", required=True, help="Agent 名称")
    parser_clear.add_argument("--version", required=True, help="版本")
    parser_clear.add_argument("--force", action="store_true", help="强制清除，不确认")
    parser_clear.set_defaults(func=cmd_clear)

    # export 命令
    parser_export = subparsers.add_parser("export", help="导出检查点")
    parser_export.add_argument("--agent", required=True, help="Agent 名称")
    parser_export.add_argument("--version", required=True, help="版本")
    parser_export.add_argument("-o", "--output", required=True, help="输出文件路径")
    parser_export.set_defaults(func=cmd_export)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
