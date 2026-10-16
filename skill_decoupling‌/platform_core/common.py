"""各模块共享的异常、路径约束和 JSON Schema 校验。

这里不访问数据库或远程服务；调用方将 Problem 映射为 HTTP 错误或 MCP 业务错误。
Schema 校验采用明确支持的子集，遇到不支持的断言会拒绝绑定，避免静默放宽契约。"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class Problem(Exception):
    """可公开给前端和模型的业务异常；details 只能填写已经脱敏的诊断信息。"""
    def __init__(self, code, message, status=400, details=None):
        """保存稳定错误码 code、中文说明 message、HTTP 状态和可选诊断信息。
        异常本身不记录凭据或请求原文，服务层决定如何展示。"""
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details

    def as_dict(self):
        """输出统一错误对象；不输出堆栈，未提供 details 时省略该字段。"""
        result = {"code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        return result


def uid():
    """生成无连字符 UUID，作为实体、操作及幂等请求标识；不包含业务信息。"""
    return uuid.uuid4().hex


def now():
    """返回 UTC ISO 时间字符串，供数据库记录跨时区排序和审计使用。"""
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    """生成紧凑 UTF-8 友好的 JSON 字符串；中文保持原文，供 HTTP 和持久化共用。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    """对 JSON 序列化结果计算 SHA256，用于契约修订和请求内容指纹。
    它不是密码散列，也不代替认证令牌。"""
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def safe_path(root, relative):
    """把使用 / 分隔的相对路径解析到 root 内，并校验解析后的真实路径。
    拒绝绝对路径、父级跳转、盘符与越界符号链接；只返回路径，不创建文件。"""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise Problem("PATH_INVALID", "路径必须为非空相对路径，使用 / 分隔")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or ":" in relative:
        raise Problem("PATH_INVALID", "路径不能越过项目目录")
    target = (Path(root) / path).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise Problem("PATH_INVALID", "路径不能越过项目目录")
    return target


def url(value):
    """校验 HTTP/HTTPS 地址，禁止 URL 内嵌账号密码和片段。
    此函数不发起连接；局域网或公网可达性由后续健康检查判断。"""
    p = urlsplit(value or "")
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.fragment:
        raise Problem("URL_INVALID", "需要不含凭据或片段的 HTTP/HTTPS 地址")
    return value


def text(value, field, maximum=4096):
    """检查必填文本和最大字符数，返回去掉首尾空白的字符串。
    错误字段名仅用于用户提示，不回显传入的敏感内容。"""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Problem("PARAMETER_INVALID", f"{field} 需要非空文本（最多 {maximum} 字符）")
    return value.strip()


def json_equal(left, right):
    """按 JSON 类型比较枚举/常量，避免 Python 把 true 与数字 1 当成相同。
    数字 1 和 1.0 可相等；列表保持顺序，对象不依赖键的插入顺序。
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def normalize_tool_values(value, schema):
    """Lossless provider transport normalization, followed by ordinary validation."""
    if not isinstance(schema,dict): return value
    expected=schema.get('type',[])
    types=[expected] if isinstance(expected,str) else expected
    if 'integer' in types and isinstance(value,str) and re.fullmatch(r'-?(0|[1-9][0-9]*)',value):
        return int(value)
    if ('object' in types or 'array' in types) and isinstance(value,str):
        try: decoded=json.loads(value)
        except ValueError: decoded=value
        if ('object' in types and isinstance(decoded,dict)) or ('array' in types and isinstance(decoded,list)):
            value=decoded
    # XML-oriented providers sometimes encode a list as {"item": [...]} or one item.
    if 'array' in types and value=='' and schema.get('minItems',0)==0:
        return []
    if 'array' in types and isinstance(value,dict) and set(value)=={'item'}:
        if value['item']=='' and schema.get('minItems',0)==0: return []
        value=value['item'] if isinstance(value['item'],list) else [value['item']]
    if 'object' in types and isinstance(value,dict):
        extra=schema.get('additionalProperties',{})
        if not isinstance(extra,dict): extra={}
        return {k:normalize_tool_values(v,schema.get('properties',{}).get(k,extra)) for k,v in value.items()}
    if 'array' in types and isinstance(value,list):
        return [normalize_tool_values(v,schema.get('items',{})) for v in value]
    return value


def validate(value, schema, root=None, path="$", depth=0):
    """递归校验值是否满足受支持的 JSON Schema，成功返回 None。
    root 固定为最外层 Schema，便于解析本地 $ref；path 标识错误位置。
    只捕获候选格式不匹配，Schema 本身损坏或不支持时仍向上抛错。"""
    if depth > 64:
        raise Problem("SCHEMA_INVALID", "Schema 引用过深")
    root = schema if root is None else root
    if schema is True:
        return
    if schema is False:
        raise Problem("PARAMETER_INVALID", f"{path} 不允许此值")
    if not isinstance(schema, dict):
        raise Problem("SCHEMA_INVALID", "Schema 必须为对象或布尔值")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise Problem("SCHEMA_UNSUPPORTED", "不加载外部 Schema 引用")
        target = root
        try:
            for key in ref[2:].split("/"):
                target = target[key.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise Problem("SCHEMA_INVALID", "Schema 引用不存在")
        validate(value, target, root, path, depth + 1)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            matched = 0
            for candidate in schema[key]:
                try:
                    validate(value, candidate, root, path, depth + 1)
                    matched += 1
                except Problem as e:
                    if e.code != "PARAMETER_INVALID":
                        raise
            if not matched or (key == "oneOf" and matched != 1):
                raise Problem("PARAMETER_INVALID", f"{path} 不符合 {key}")
    for candidate in schema.get("allOf", []):
        validate(value, candidate, root, path, depth + 1)
    if "not" in schema:
        try:
            validate(value, schema["not"], root, path, depth + 1)
        except Problem as e:
            if e.code != "PARAMETER_INVALID":
                raise
        else:
            raise Problem("PARAMETER_INVALID", f"{path} 符合被禁止的格式")
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
              "string": lambda x: isinstance(x, str), "boolean": lambda x: isinstance(x, bool),
              "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
              "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
              "null": lambda x: x is None}
    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if any(t not in checks for t in types):
            raise Problem("SCHEMA_UNSUPPORTED", "未知 Schema 类型")
        if not any(checks[t](value) for t in types):
            raise Problem("PARAMETER_INVALID", f"{path} 需要 {expected}")
    if "const" in schema and not json_equal(value, schema["const"]):
        raise Problem("PARAMETER_INVALID", f"{path} 必须为 {schema['const']}")
    if "enum" in schema and not any(json_equal(value, candidate) for candidate in schema["enum"]):
        raise Problem("PARAMETER_INVALID", f"{path} 不在允许选项中")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise Problem("PARAMETER_INVALID", f"缺少 {path}.{key}")
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                validate(item, props[key], root, f"{path}.{key}", depth + 1)
            elif schema.get("additionalProperties") is False:
                raise Problem("PARAMETER_INVALID", f"不允许字段 {path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"], root, f"{path}.{key}", depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000000):
            raise Problem("PARAMETER_INVALID", f"{path} 数组长度不符合要求")
        if schema.get("uniqueItems") and len({dumps(x) for x in value}) != len(value):
            raise Problem("PARAMETER_INVALID", f"{path} 不允许重复项")
        for i, item in enumerate(value):
            validate(item, schema.get("items", True), root, f"{path}[{i}]", depth + 1)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10000000):
            raise Problem("PARAMETER_INVALID", f"{path} 文本长度不符合要求")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise Problem("PARAMETER_INVALID", f"{path} 格式不正确")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for key, bad in (("minimum", lambda bound: value < bound), ("maximum", lambda bound: value > bound),
                         ("exclusiveMinimum", lambda bound: value <= bound), ("exclusiveMaximum", lambda bound: value >= bound)):
            if key in schema and bad(schema[key]):
                raise Problem("PARAMETER_INVALID", f"{path} 超出数值范围")


def check_schema(schema, depth=0):
    """先检查 Schema 是否含未实现的断言，再递归检查子 Schema。
    这一步用于工具发现，不负责验证具体参数；不允许外部引用自动下载。"""
    supported = {"$schema", "$id", "$ref", "$defs", "definitions", "type", "title", "description", "default",
                 "examples", "deprecated", "readOnly", "writeOnly", "properties", "required", "additionalProperties",
                 "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "enum", "const",
                 "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "anyOf", "oneOf", "allOf", "not"}
    if depth > 40:
        raise Problem("SCHEMA_INVALID", "Schema 嵌套过深")
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise Problem("SCHEMA_INVALID", "无效 JSON Schema")
    unknown = set(schema) - supported
    if unknown:
        raise Problem("SCHEMA_UNSUPPORTED", "当前校验器尚不支持：" + ", ".join(sorted(unknown)))
    if "$ref" in schema and (not isinstance(schema["$ref"], str) or not schema["$ref"].startswith("#/")):
        raise Problem("SCHEMA_UNSUPPORTED", "不支持外部 Schema 引用")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (re.error, TypeError):
            raise Problem("SCHEMA_INVALID", "Schema 正则表达式无效")
    for key in ("properties", "$defs", "definitions"):
        if key in schema and not isinstance(schema[key], dict):
            raise Problem("SCHEMA_INVALID", f"{key} 必须是对象")
        for child in schema.get(key, {}).values():
            check_schema(child, depth + 1)
    for key in ("items", "additionalProperties", "not"):
        if key in schema:
            check_schema(schema[key], depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and (not isinstance(schema[key], list) or not schema[key]):
            raise Problem("SCHEMA_INVALID", f"{key} 必须是非空数组")
        for child in schema.get(key, []):
            check_schema(child, depth + 1)
