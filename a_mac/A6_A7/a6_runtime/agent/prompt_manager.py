"""
A6 提示词管理器

提供提示词的读取、修改、保存功能
供前端页面调用，非 Agent 工具
"""
import re
from datetime import datetime
from typing import Dict, Optional

from . import prompts


class PromptManager:
    """
    提示词管理器

    提供提示词的读取、修改、保存功能
    供前端页面调用，非 Agent 工具
    """

    # 提示词名称映射
    PROMPT_NAMES = {
        "risk_classification": "RISK_CLASSIFICATION_SYSTEM_PROMPT",
        "risk_user_template": "RISK_CLASSIFICATION_USER_PROMPT_TEMPLATE",
        "aggregation": "AGGREGATION_SYSTEM_PROMPT",
        "aggregation_user_template": "AGGREGATION_USER_PROMPT_TEMPLATE",
    }

    def __init__(self):
        # 运行时副本（修改先写入内存）
        self._runtime_copies: Dict[str, str] = {}
        self._load_defaults()

    def _load_defaults(self):
        """加载默认提示词到内存"""
        self._runtime_copies = {
            "risk_classification": prompts.RISK_CLASSIFICATION_SYSTEM_PROMPT,
            "risk_user_template": prompts.RISK_CLASSIFICATION_USER_PROMPT_TEMPLATE,
            "aggregation": prompts.AGGREGATION_SYSTEM_PROMPT,
            "aggregation_user_template": prompts.AGGREGATION_USER_PROMPT_TEMPLATE,
        }

    def read_prompt(self, prompt_name: str) -> Dict:
        """
        读取指定提示词内容

        Args:
            prompt_name: 提示词名称
                - "risk_classification": 风险分级系统提示词
                - "risk_user_template": 风险分级用户模板
                - "aggregation": 事件聚合提示词
                - "aggregation_user_template": 聚合用户模板

        Returns:
            {
                "name": str,
                "content": str,
                "last_modified": str
            }
        """
        if prompt_name not in self._runtime_copies:
            return {
                "name": prompt_name,
                "content": None,
                "error": f"Unknown prompt name: {prompt_name}"
            }

        return {
            "name": prompt_name,
            "content": self._runtime_copies[prompt_name],
            "last_modified": datetime.now().isoformat()
        }

    def update_prompt(
        self,
        prompt_name: str,
        new_content: str,
        reason: str = None
    ) -> bool:
        """
        修改提示词（仅更新内存中的副本）

        Args:
            prompt_name: 提示词名称
            new_content: 新的提示词内容
            reason: 修改原因（记录日志）

        Returns:
            是否修改成功
        """
        if prompt_name not in self._runtime_copies:
            return False

        self._runtime_copies[prompt_name] = new_content
        return True

    def save_to_file(self, prompt_name: str = None) -> bool:
        """
        将内存中的提示词保存到 prompts.py 文件

        Args:
            prompt_name: 提示词名称（None 表示保存所有）

        Returns:
            是否保存成功
        """
        try:
            prompts_path = prompts.__file__
            with open(prompts_path, "r", encoding="utf-8") as f:
                content = f.read()

            if prompt_name:
                # 保存单个提示词
                content = self._update_prompt_in_content(content, prompt_name, self._runtime_copies[prompt_name])
            else:
                # 保存所有提示词
                for name, value in self._runtime_copies.items():
                    content = self._update_prompt_in_content(content, name, value)

            with open(prompts_path, "w", encoding="utf-8") as f:
                f.write(content)

            return True

        except Exception as e:
            print(f"Error saving prompts: {e}")
            return False

    def _update_prompt_in_content(self, content: str, prompt_name: str, new_value: str) -> str:
        """更新内容中的提示词"""
        var_name = self.PROMPT_NAMES.get(prompt_name, prompt_name)

        # 尝试找到并替换
        # 匹配格式: VAR_NAME = """...""" 或 VAR_NAME = '''...'''
        patterns = [
            rf'^({var_name})\s*=\s*"""[\s\S]*?"""',
            rf"^({var_name})\s*=\s*'''[\s\S]*?'''",
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                # 找到后替换
                old_start = match.start()
                old_end = match.end()

                # 保留缩进
                indent = len(content[:old_start]) - len(content[:old_start].rstrip('\n'))

                # 构建新的内容
                if '"""' in content[old_start:old_start+20]:
                    new_block = f'{var_name} = """{new_value}"""'
                else:
                    new_block = f"{var_name} = '''{new_value}'''"

                content = content[:old_start] + new_block + content[old_end:]
                return content

        # 如果没找到，尝试追加
        return content

    def reset_to_default(self, prompt_name: str = None) -> bool:
        """
        重置提示词为默认内容

        Args:
            prompt_name: 提示词名称（None 表示重置所有）

        Returns:
            是否重置成功
        """
        # 重新加载默认
        import importlib
        importlib.reload(prompts)

        self._load_defaults()
        return True

    def get_all_prompts(self) -> Dict:
        """
        获取所有提示词

        Returns:
            所有提示词的字典
        """
        return {
            name: {
                "name": name,
                "content": content,
                "last_modified": datetime.now().isoformat()
            }
            for name, content in self._runtime_copies.items()
        }

    def get_risk_suggestions(self) -> Dict:
        """
        获取风险等级建议模板

        Returns:
            RISK_SUGGESTIONS_BY_LEVEL 字典
        """
        return prompts.RISK_SUGGESTIONS_BY_LEVEL
