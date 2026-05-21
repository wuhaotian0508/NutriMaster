from __future__ import annotations

from typing import Any


class PromptBuilder:
    """系统提示词构建器，为 NutriMaster 代理生成完整的系统提示词。"""

    def __init__(self, skill_loader: Any | None = None):
        """初始化提示词构建器。

        参数:
            skill_loader: 技能加载器实例，用于加载技能描述并嵌入到系统提示词中。
                          为 None 时不包含技能相关提示。
        """
        self.skill_loader = skill_loader

    def build(self, *, user_id: str | None = None, use_depth: bool = False, use_personal: bool = False) -> str:
        """构建完整的系统提示词。

        根据当前搜索模式和个人库设置，生成包含工具使用规则、回答要求和技能提示的
        系统提示词文本。

        参数:
            user_id: 用户标识符，用于加载用户自定义技能。
            use_depth: 是否为深度搜索模式。
            use_personal: 是否开启个人知识库。

        返回:
            str: 完整的系统提示词 Markdown 文本。
        """
        mode = "深度搜索" if use_depth else "普通搜索"
        personal = "开启" if use_personal else "关闭"
        return f"""你是 NutriMaster，一个专业的植物营养代谢生物学研究助手。

当前模式：{mode}
个人库：{personal}

工具使用规则：
1. 普通问候、闲聊、简单说明不要调用工具。
2. 涉及基因、蛋白、代谢通路、作物营养、文献证据的问题，优先调用 rag_search。
3. rag_search 是复合 RAG 工具；只要调用它，内部会同时检索 PubMed 摘要和本地基因库。
4. 调用 rag_search 时，由你负责生成检索词：query/gene_db_query 保留关键基因、通路、物种、代谢物；pubmed_query 必须是英文 PubMed 关键词或 Boolean 检索式。
5. 用户明确要求 CRISPR 敲除/编辑实验方案时，调用 experiment_design（experiment_type="crispr"）；用户明确要求过表达或转基因实验方案时，调用 experiment_design（experiment_type="gene_transfer"）。experiment_design 需两步交互：先预览基因验证结果，用户确认后再设 confirmed=true 生成完整 SOP。
6. 不要臆造引用。使用 rag_search 返回的证据时，正文必须使用证据中的 [编号]。

回答要求：
- 使用中文 Markdown
- 聚焦题目主旨回答，不随便发散；行文注意句子、段落间的逻辑结构关系，不滥用分点罗列
- 根据使用到的语料回答科学问题本身并标注引文即可，不需要额外声明结论来自检索到的某篇或几篇文献
- 对不确定的结论标明证据不足或需要进一步实验验证

{self._skills_block(user_id)}
"""

    def _skills_block(self, user_id: str | None) -> str:
        """生成技能列表的提示文本块。

        从技能加载器获取可用技能列表，格式化为系统提示词中的技能说明段落。

        参数:
            user_id: 用户标识符，用于加载用户自定义技能。

        返回:
            str: 格式化的技能提示文本；无技能加载器或无可用技能时返回空字符串。
        """
        if self.skill_loader is None:
            return ""
        skills = self.skill_loader.list_dir(user_id)
        if not skills:
            return ""
        lines = ["后台 skills 提示："]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")
            if skill.content:
                lines.append(skill.content[:1200])
        return "\n".join(lines)
