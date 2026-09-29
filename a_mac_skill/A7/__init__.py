# a/A7/__init__.py — A7 通道适配包(在 a/ 子项目内)

# 通过相对导入暴露核心模块(保留兼容性)
from . import adapters  # noqa: F401
from . import api  # noqa: F401
from . import middleware  # noqa: F401
from . import prompt  # noqa: F401
from . import schema  # noqa: F401
from . import storage  # noqa: F401