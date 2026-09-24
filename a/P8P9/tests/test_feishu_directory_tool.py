"""P8 飞书群/人映射查询工具：只读、参数互斥、双向查找。"""

import json

import pytest

from agents import p8_disposition_agent as p8


@pytest.fixture(autouse=True)
def configured_directory(monkeypatch):
    values = {
        "FEISHU_GROUP_MAP": json.dumps({
            "oc_a": {"name": "动火作业群"},
            "oc_b": {"name": "巡检群"},
        }),
        "FEISHU_USER_MAP": json.dumps({
            "ou_a": {"name": "李宗睿", "role": "作业负责人"},
            "ou_b": {"name": "张三", "role": "安全员"},
        }),
    }
    monkeypatch.setattr(p8, "dotenv_values", lambda _path: values)
    return values


def query(**kwargs):
    return json.loads(p8.lookup_feishu_directory.invoke(kwargs))


def test_group_name_to_chat_id_and_reverse():
    assert query(entity_type="group", name="动火作业群")["result"]["chat_id"] == "oc_a"
    assert query(entity_type="群", id="oc_b")["result"]["name"] == "巡检群"


def test_person_name_to_open_id_and_reverse():
    assert query(entity_type="person", name="李宗睿")["result"]["open_id"] == "ou_a"
    assert query(entity_type="人", id="ou_b")["result"]["name"] == "张三"


def test_empty_filters_list_only_selected_type():
    result = query(entity_type="group")["result"]
    assert result["count"] == 2
    assert {row["chat_id"] for row in result["items"]} == {"oc_a", "oc_b"}
    assert all("open_id" not in row for row in result["items"])


def test_required_type_and_mutually_exclusive_filters():
    assert query(entity_type="")["error"]["code"] == "INVALID_ARGUMENT"
    assert query(entity_type="group", name="动火作业群", id="oc_a")["error"]["code"] == "INVALID_ARGUMENT"
    with pytest.raises(Exception):
        query()


def test_missing_and_duplicate_name(configured_directory):
    assert query(entity_type="person", name="不存在")["error"]["code"] == "NOT_FOUND"
    user_map = json.loads(configured_directory["FEISHU_USER_MAP"])
    user_map["ou_c"] = {"name": "李宗睿"}
    configured_directory["FEISHU_USER_MAP"] = json.dumps(user_map)
    assert query(entity_type="person", name="李宗睿")["error"]["code"] == "AMBIGUOUS_NAME"
