"""外部 CLI 接入样例：固定 echo 子命令，UTF-8 JSON stdin/stdout。

登记解释器为可执行程序，固定参数依次为本文件绝对路径、echo。
输入输出均为 {"text": "示例文字"}；本样例不访问网络或业务文件。
"""
import json
import sys


def main():
    """仅接收声明的子命令；诊断走 stderr，stdout 只提供 JSON 回执。"""
    if sys.argv[1:] != ["echo"]:
        print("仅支持 echo 子命令", file=sys.stderr)
        return 2
    try:
        value = json.load(sys.stdin)
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ValueError()
        print(json.dumps({"text": value["text"]}, ensure_ascii=False))
        return 0
    except (ValueError, UnicodeError):
        print("输入需要 JSON 对象，text 必须为字符串", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # 独立启动也保持与平台约定相同的 UTF-8 编码。
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
