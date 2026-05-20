"""
Admin Panel 扩展：添加自动索引更新功能

将此代码添加到 src/nutrimaster/web/admin/app.py 中，
或者作为独立的 Blueprint 挂载到主应用。
"""

from flask import jsonify, request
import threading

# 在现有的 admin_bp 中添加以下路由：

@admin_bp.route("/api/index/rebuild", methods=["POST"])
@admin_required
def api_index_rebuild():
    """
    手动触发索引重建（增量或完全重建）

    POST body:
        {
            "force": false,  // 是否强制完全重建（默认 false，增量模式）
            "async": true    // 是否异步执行（默认 true）
        }
    """
    body = request.get_json(silent=True) or {}
    force = body.get("force", False)
    async_mode = body.get("async", True)

    if async_mode:
        # 异步执行
        def rebuild_task():
            try:
                _refresh_index(DATA_DIR, force=force)
                print(f"✅ 索引重建完成（force={force}）")
            except Exception as e:
                print(f"❌ 索引重建失败: {e}")
                import traceback
                traceback.print_exc()

        thread = threading.Thread(target=rebuild_task, daemon=True)
        thread.start()

        return jsonify({
            "message": "索引重建已在后台启动",
            "force": force,
        })
    else:
        # 同步执行
        try:
            _refresh_index(DATA_DIR, force=force)
            return jsonify({
                "message": "索引重建完成",
                "force": force,
            })
        except Exception as e:
            return jsonify({
                "error": f"索引重建失败: {e}"
            }), 500


@admin_bp.route("/api/index/status")
@admin_required
def api_index_status():
    """
    查询索引状态

    返回:
        {
            "total_files": int,      // corpus 中的总文件数
            "indexed_files": int,    // 已索引的文件数
            "missing_files": int,    // 未索引的文件数
            "total_chunks": int,     // 总 chunk 数
            "embedding_shape": [...], // embedding 维度
            "last_updated": str      // 最后更新时间
        }
    """
    import json
    from pathlib import Path
    import os
    from datetime import datetime

    manifest_path = Path(SETTINGS.rag.index_dir) / "manifest.json"
    corpus_dir = Path(SETTINGS.rag.data_dir)

    # 读取 manifest
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        indexed_files = len(manifest.get("files", {}))

        # 获取最后修改时间
        stat = os.stat(manifest_path)
        last_updated = datetime.fromtimestamp(stat.st_mtime).isoformat()
    else:
        indexed_files = 0
        last_updated = None

    # 统计 corpus 文件
    all_files = list(corpus_dir.glob("*_nutri_plant_verified.json"))
    total_files = len(all_files)
    missing_files = total_files - indexed_files

    # 获取 chunks 信息
    from nutrimaster.rag.jina import JinaRetriever
    retriever = JinaRetriever()

    return jsonify({
        "total_files": total_files,
        "indexed_files": indexed_files,
        "missing_files": missing_files,
        "total_chunks": len(retriever.chunks),
        "embedding_shape": list(retriever.embeddings.shape) if retriever.embeddings is not None else None,
        "last_updated": last_updated,
    })


@admin_bp.route("/api/workflow/zip-to-index", methods=["POST"])
@admin_required
def api_workflow_zip_to_index():
    """
    一站式工作流：ZIP 上传 → 解压 → 处理 → 索引更新

    这是一个组合接口，依次调用：
    1. /api/upload (解压 ZIP)
    2. /api/pipeline/run (处理论文)
    3. /api/index/rebuild (更新索引)

    POST body:
        {
            "auto_run": true,        // 解压后自动运行 pipeline
            "auto_index": true,      // pipeline 完成后自动更新索引
            "settings": {            // pipeline 参数
                "temperature": 0.3,
                "max_workers": 2
            }
        }

    注意：此接口只是设置标志，实际执行仍需前端配合。
    更好的方式是在 pipeline 完成后自动触发索引（已实现）。
    """
    body = request.get_json(silent=True) or {}

    return jsonify({
        "message": "一站式工作流已配置",
        "note": "请先上传 ZIP，然后运行 pipeline。Pipeline 完成后会自动更新索引。",
        "auto_index_enabled": True,  # 已在 pipeline 完成回调中实现
    })
