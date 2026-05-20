---
name: experiment-design
description: >
  植物基因实验设计技能，支持 CRISPR 基因敲除/编辑（crispr）和过表达/转基因（gene_transfer）
  两种实验类型。当用户询问如何敲除某基因、设计 sgRNA、构建过表达载体或生成实验 SOP 时触发。
  工具采用两步交互：先预览基因验证结果，用户确认后生成完整 SOP。
tools: [experiment_design]
---

# 实验设计技能

使用 `experiment_design` 工具为植物基因实验生成方案和 SOP。

## 实验类型选择

| 用户意图 | experiment_type |
|---------|----------------|
| 敲除某基因、基因编辑、sgRNA 设计、CRISPR 靶点 | `crispr` |
| 过表达某基因、转基因、上下游序列获取、GoldenBraid 载体构建 | `gene_transfer` |

当用户意图不明确时，根据以下原则判断：
- 提到"敲除""突变""loss of function""KO"→ `crispr`
- 提到"过表达""超表达""OE""gain of function""转入"→ `gene_transfer`

## 两步交互流程

### 第一步：预览（output="advice", confirmed=false）

首次调用时**不要**设 `confirmed=true`，工具会：
1. 从 goal 文本中提取基因名和物种信息
2. 通过 NCBI 验证基因是否存在
3. 对于 gene_transfer，额外推断受体物种

返回内容示例（CRISPR）：
```
实验设计预览：
- GmFAD2 (Glycine max)  ✓ NCBI 已验证
- GmFAD3 (Glycine max)  ✓ NCBI 已验证

如需完整 CRISPR/SOP，请明确要求生成完整实验方案。
```

**收到预览后，将结果呈现给用户，询问是否确认继续。**

### 第二步：生成完整 SOP（output="full_sop", confirmed=true）

用户确认后，设 `confirmed=true, output="full_sop"` 再次调用。工具将：
- **CRISPR**：查询 NCBI 获取基因 accession 和序列，提交 CRISPR-P 设计靶点，填充物种专属 SOP 模板
- **gene_transfer**：从 NCBI 获取基因本体及上下游 2000 bp 序列，填充过表达/GoldenBraid2.0 SOP 模板

完整 SOP 为 Markdown 格式，包含逐步操作指导和所有必要序列信息。

## 调用示例

**CRISPR 首次调用：**
```json
{
  "experiment_type": "crispr",
  "goal": "在大豆中敲除 GmFAD2 基因，降低不饱和脂肪酸含量",
  "output": "advice",
  "confirmed": false
}
```

**gene_transfer 首次调用：**
```json
{
  "experiment_type": "gene_transfer",
  "goal": "将拟南芥 AtDREB1A 过表达到烟草中提高抗旱性",
  "output": "advice",
  "confirmed": false
}
```

**用户确认后生成完整 SOP：**
```json
{
  "experiment_type": "crispr",
  "goal": "在大豆中敲除 GmFAD2 基因",
  "genes": [{"gene": "GmFAD2", "species": "Glycine max"}],
  "output": "full_sop",
  "confirmed": true
}
```

## 注意事项

- **不要**在用户未确认时设 `confirmed=true`，完整 pipeline 会调用外部 API（NCBI、CRISPR-P），耗时较长
- 若用户在对话中已提供具体基因名和物种，将其填入 `genes` 参数可跳过 LLM 提取步骤，提高准确性
- 支持多基因：`genes` 数组可包含多个条目，工具会为每个物种分别生成 SOP
- 物种名建议使用拉丁名（如 `Glycine max`），工具内部会做规范化处理
- 支持的物种模板：水稻（Oryza sativa）、玉米（Zea mays）、烟草（Nicotiana）、小麦（Triticum aestivum）、大豆（Glycine max）、拟南芥（Arabidopsis thaliana），其他物种使用通用模板
