# Deployment Checklist

## Current Status (2026-05-08 23:34)

✅ **Implementation Complete**
- Real-time incremental indexing in `_on_paper_done()` callback
- Pipeline end indexing (regardless of stop/complete)
- Admin Panel API endpoints (`/api/index/status`, `/api/index/rebuild`)
- Dashboard UI with index status card and rebuild button
- CLI tools (`rebuild_index_robust.py`, `check_index_status.py`, `test_index_api.py`)
- Comprehensive documentation (`INDEX_ROBUSTNESS.md`, `DEPLOYMENT_GUIDE.md`)

🔄 **In Progress**
- Index rebuild: 535/2814 files (19%) - PID 3550713
- Estimated completion: ~1.5 hours (around 01:00)

## Deployment Steps (Execute After Rebuild Completes)

### Step 1: Verify Rebuild Completion
```bash
# Check if process is still running
ps aux | grep 3550713

# Verify final status
python3 check_index_status.py

# Expected output:
#   总文件数: 18,181
#   已索引: 18,181
#   缺失: 0
#   状态: ✅ 完全同步
```

### Step 2: Restart Flask Application
```bash
# Find Flask process
ps aux | grep "python.*app.py\|flask run\|gunicorn" | grep -v grep

# Kill the process (replace PID with actual)
kill <FLASK_PID>

# Restart (adjust command based on your setup)
cd /data/haotianwu/biojson
python -m nutrimaster.web.app &

# Or if using systemd:
# sudo systemctl restart nutrimaster

# Or if using supervisor:
# supervisorctl restart nutrimaster
```

### Step 3: Verify API Endpoints
```bash
# Test index status API
python3 test_index_api.py

# Expected: ✅ 索引 API 端点工作正常
```

### Step 4: Test Admin Panel UI
1. Open browser: `http://localhost:8000/admin`
2. Login with credentials
3. Check Dashboard:
   - Index status card should show "Indexed Files: 18,181 / 18,181"
   - Status badge should show "✅ Synced"
   - Last updated timestamp should be recent

### Step 5: Test Manual Rebuild (Optional)
1. Click "🔄 Rebuild Index" button
2. Confirm dialog
3. Wait 5 seconds
4. Verify status refreshes automatically

### Step 6: End-to-End Test
**Scenario: Upload → Pipeline → Auto Index**

1. Prepare test ZIP with 1-2 markdown files
2. Upload via Admin Panel
3. Run Pipeline (process all papers)
4. Observe:
   - Pipeline log shows paper processing
   - No explicit index messages (silent real-time updates)
   - At end: "🔄 Rebuilding RAG index..." → "✅ RAG index rebuilt successfully"
5. Check Dashboard: index count should increase by number of papers processed

**Scenario: Stop Pipeline Mid-Run**

1. Upload ZIP with multiple files
2. Start Pipeline
3. After 2-3 papers processed, click "Stop"
4. Observe:
   - Pipeline stops
   - Index rebuild still triggers
   - Dashboard shows processed papers are indexed

## Verification Commands

```bash
# Quick status check
python3 check_index_status.py

# Detailed manifest inspection
python3 -c "
import json
from pathlib import Path
manifest = json.loads(Path('data/index/manifest.json').read_text())
print(f'Files in manifest: {len(manifest[\"files\"])}')
print(f'Total chunks: {manifest[\"total_chunks\"]}')
print(f'Last updated: {manifest.get(\"last_updated\", \"N/A\")}')
"

# Check corpus file count
ls -1 data/corpus/*_nutri_plant_verified.json | wc -l

# Check embeddings shape
python3 -c "
import numpy as np
emb = np.load('data/index/embeddings.npy')
print(f'Embeddings shape: {emb.shape}')
"
```

## Rollback Plan (If Issues Occur)

```bash
# Restore previous version
git checkout HEAD~1 src/nutrimaster/web/admin/app.py
git checkout HEAD~1 src/nutrimaster/web/admin/static/

# Restart Flask
kill <FLASK_PID>
python -m nutrimaster.web.app &

# Index can still be rebuilt via CLI
python3 rebuild_index_robust.py
```

## Success Criteria

- [ ] Rebuild process completed successfully (18,181 files indexed)
- [ ] Flask application restarted without errors
- [ ] `/api/index/status` returns correct data
- [ ] Dashboard shows index status card
- [ ] Manual rebuild button works
- [ ] Pipeline auto-indexes new papers in real-time
- [ ] Stopping Pipeline mid-run still updates index
- [ ] No files missing from manifest

## Monitoring

After deployment, periodically check:
```bash
# Daily check
python3 check_index_status.py

# If issues found
python3 rebuild_index_robust.py
```

## Notes

- Real-time indexing adds ~2-3 seconds per paper (acceptable overhead)
- Incremental mode is fast - only processes new/modified files
- All operations are idempotent - safe to run multiple times
- Four-layer defense ensures no files are ever missed
