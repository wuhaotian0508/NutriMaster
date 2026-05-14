from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from nutrimaster.experiment.gene_validation import extract_gene_names, has_gene_names

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """获取项目根目录路径。

    返回:
        Path: 从当前文件向上回溯 4 层得到的项目根目录绝对路径。
    """
    return Path(__file__).resolve().parents[4]


def _default_shared_skills_dir() -> Path:
    """获取默认的共享技能目录路径。

    返回:
        Path: 当前文件所在目录下的 "shared" 子目录绝对路径。
    """
    return Path(__file__).resolve().parent / "shared"


@dataclass(frozen=True)
class Skill:
    """技能数据类，表示一个可用的代理技能。

    属性:
        name: 技能名称。
        description: 技能的简要描述。
        content: 技能的详细内容文本。
        tools: 技能关联的工具名列表，为 None 表示可使用所有工具。
        path: 技能文件的路径。
        is_shared: 是否为共享技能（非用户自定义）。
        tags: 技能标签列表。
    """
    name: str
    description: str
    content: str = ""
    tools: list[str] | None = field(default_factory=list)
    path: Path = field(default_factory=Path)
    is_shared: bool = True
    tags: list[str] = field(default_factory=list)


class SkillLoader:
    """技能加载器，负责从文件系统加载、管理共享技能和用户自定义技能。"""

    def __init__(
        self,
        skills_dir: Path | None = None,
        user_skills_dir: Path | None = None,
    ):
        """初始化技能加载器。

        参数:
            skills_dir: 共享技能目录路径，为 None 时使用默认路径。
            user_skills_dir: 用户技能目录路径，为 None 时使用默认路径。
        """
        project_root = _project_root()
        self.skills_dir = Path(skills_dir) if skills_dir is not None else _default_shared_skills_dir()
        self.user_skills_dir = (
            Path(user_skills_dir) if user_skills_dir is not None else project_root / "data" / "user_skills"
        )
        self._skills: dict[str, Skill] = {}
        self.load_skills()

    def load_skills(self) -> dict[str, Skill]:
        """从共享技能目录加载所有技能。

        扫描 skills_dir 下的所有 skill.md 文件（跳过 skill-creator），
        解析并缓存到内部字典中。

        返回:
            dict[str, Skill]: 以技能名为键的技能字典。
        """
        self._skills = {}
        if not self.skills_dir.exists():
            return {}
        for skill_file in sorted(self.skills_dir.glob("*/skill.md")):
            if skill_file.parent.name == "skill-creator":
                continue
            try:
                skill = self._load_skill_file(skill_file, is_shared=True)
                self._skills[skill.name] = skill
            except Exception:
                logger.warning("Failed to load skill: %s", skill_file, exc_info=True)
        return dict(self._skills)

    def list_dir(self, user_id: str | None = None) -> list[Skill]:
        """列出所有可用技能，包括共享技能和指定用户的自定义技能。

        参数:
            user_id: 用户标识符，为 None 时仅返回共享技能。

        返回:
            list[Skill]: 可用技能列表。
        """
        skills = dict(self._skills)
        if user_id:
            skills.update(self._load_user_skills(user_id))
        return list(skills.values())

    def get_skill(self, name: str, user_id: str | None = None) -> Skill | None:
        """按名称获取技能，优先查找共享技能，然后查找用户自定义技能。

        参数:
            name: 技能名称。
            user_id: 用户标识符，为 None 时仅查找共享技能。

        返回:
            Skill | None: 找到的技能对象，不存在时返回 None。
        """
        if name in self._skills:
            return self._skills[name]
        if user_id:
            return self._load_user_skills(user_id).get(name)
        return None

    def load_skill(self, name: str, user_id: str | None = None) -> Skill | None:
        """加载指定名称的技能（get_skill 的别名）。

        参数:
            name: 技能名称。
            user_id: 用户标识符。

        返回:
            Skill | None: 找到的技能对象，不存在时返回 None。
        """
        return self.get_skill(name, user_id=user_id)

    def save_skill(self, name: str, content: str, user_id: str | None = None) -> Skill:
        """保存技能内容到文件系统。

        将技能内容写入 skill.md 文件，如果是共享技能则同时更新内部缓存。

        参数:
            name: 技能名称。
            content: 技能的 Markdown 内容（包含 YAML front matter）。
            user_id: 用户标识符，为 None 时保存为共享技能。

        返回:
            Skill: 保存后重新解析得到的技能对象。
        """
        skill_dir = self.user_skills_dir / user_id / name if user_id else self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "skill.md"
        skill_file.write_text(content, encoding="utf-8")
        skill = self._load_skill_file(skill_file, is_shared=user_id is None)
        if user_id is None:
            self._skills[skill.name] = skill
        return skill

    def delete_skill(self, name: str, user_id: str) -> bool:
        """删除指定用户的自定义技能。

        递归删除技能目录及其所有文件。

        参数:
            name: 技能名称。
            user_id: 用户标识符。

        返回:
            bool: 删除成功返回 True，技能不存在返回 False。
        """
        skill_dir = self.user_skills_dir / user_id / name
        if not skill_dir.exists():
            return False
        shutil.rmtree(skill_dir)
        return True

    def build_tool_call(self, query: str, trigger_source: str = "query") -> dict | None:
        """根据查询文本构建工具调用参数（当前未实现，始终返回 None）。

        参数:
            query: 用户查询文本。
            trigger_source: 触发来源标识。

        返回:
            dict | None: 当前始终返回 None。
        """
        return None

    @staticmethod
    def has_gene_names(text: str) -> bool:
        """检测文本中是否包含基因名称。

        参数:
            text: 待检测的文本。

        返回:
            bool: 包含基因名称返回 True，否则返回 False。
        """
        return has_gene_names(text)

    @staticmethod
    def extract_gene_names(text: str) -> list[str]:
        """从文本中提取基因名称列表。

        参数:
            text: 待提取的文本。

        返回:
            list[str]: 提取到的基因名称列表。
        """
        return extract_gene_names(text)

    def _load_user_skills(self, user_id: str) -> dict[str, Skill]:
        """加载指定用户的所有自定义技能。

        参数:
            user_id: 用户标识符。

        返回:
            dict[str, Skill]: 以技能名为键的用户技能字典。
        """
        user_dir = self.user_skills_dir / user_id
        if not user_dir.exists():
            return {}
        skills = {}
        for skill_file in sorted(user_dir.glob("*/skill.md")):
            try:
                skill = self._load_skill_file(skill_file, is_shared=False)
                skills[skill.name] = skill
            except Exception:
                logger.warning("Failed to load user skill: %s", skill_file, exc_info=True)
        return skills

    def _load_skill_file(self, path: Path, *, is_shared: bool) -> Skill:
        """从 skill.md 文件解析并创建 Skill 对象。

        读取文件内容，解析 YAML front matter 中的元数据（name、description、tools 等），
        提取正文内容，构建 Skill 实例。

        参数:
            path: skill.md 文件的路径。
            is_shared: 是否为共享技能。

        返回:
            Skill: 解析后的技能对象。
        """
        text = path.read_text(encoding="utf-8")
        meta = _parse_front_matter(text)
        content = _strip_front_matter(text)
        tools_raw = meta.get("tools", [])
        if tools_raw == "all":
            tools = None
        elif isinstance(tools_raw, list):
            tools = tools_raw
        else:
            tools = []
        name = str(meta.get("name") or path.parent.name)
        return Skill(
            name=name,
            description=str(meta.get("description") or ""),
            content=content,
            tools=tools,
            path=path,
            is_shared=is_shared,
            tags=[name],
        )


def _strip_front_matter(text: str) -> str:
    """去除文本开头的 YAML front matter 部分，返回正文内容。

    参数:
        text: 包含 YAML front matter 的完整文本。

    返回:
        str: 去除 front matter 后的正文内容；无 front matter 时返回原文。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1:]).strip()
    return text


def _parse_front_matter(text: str) -> dict:
    """解析文本开头的 YAML front matter 为字典。

    支持普通 key: value 对、多行折叠值（以 > 标记）和内联数组（[item1, item2]）。

    参数:
        text: 包含 YAML front matter 的完整文本。

    返回:
        dict: 解析后的元数据字典；无 front matter 时返回空字典。
    """
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}

    parsed: dict[str, object] = {}
    raw_meta = lines[1:end_index]
    index = 0
    while index < len(raw_meta):
        line = raw_meta[index]
        if not line.strip() or ":" not in line:
            index += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == ">":
            desc_lines = []
            index += 1
            while index < len(raw_meta) and raw_meta[index].startswith("  "):
                desc_lines.append(raw_meta[index].strip())
                index += 1
            parsed[key] = " ".join(desc_lines).strip()
            continue
        if value.startswith("[") and value.endswith("]"):
            parsed[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            parsed[key] = value
        index += 1
    return parsed
