"""P2-03A POST /api/v1/questionnaire/next 的 HTTP 接口单测（6 条核心用例）。

覆盖设计文档 §2.6 规定的 A–F 6 类场景：
A 空答案首调 / B 完成所有必填+自适应→FINISH / C 改上游答案→下游被 invalidated
/ D 嵌套 source_type→400 / E 非法 entry_intent→422 / F 版本不存在→404
并额外覆盖：
- 顶层/嵌套都命中 G-07 都返回 400
- 成功响应剔除 deprecated 两字段
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

URL = "/api/v1/questionnaire/next"

BASE_ENTRY_VERSION = {
    "entry_intent": "ai_recommend",
    "questionnaire_version": "v1.0",
}


# 复用 TestClient；因为 conftest 已经设置 APP_ENV=test，这里直接 create_app()
_client = TestClient(create_app())


def _post(payload: object):
    return _client.post(URL, json=payload)


# ======================================================
# A 用例：空答案首调
# ======================================================
def test_A_empty_answers_first_call() -> None:
    resp = _post({**BASE_ENTRY_VERSION, "answers_by_question_id": {}})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 响应顶层字段：不能出现 deprecated
    assert "recommendations_source" not in body
    assert "questionnaire_mismatch" not in body

    # 状态机一次最多返回 2 题（减少 UI 压力），所以首次只会出现在 required 前两道。
    next_ids = [q["question_id"] for q in body["next_questions"]]
    assert next_ids == ["q01_meal_period", "q02_explicit_food"]
    # required_missing 里应当还有 q03_budget（3 道 required，只展示了前 2 道，但缺的还是 3 道）
    assert set(body["required_not_yet_answered_question_ids"]) == {
        "q01_meal_period",
        "q02_explicit_food",
        "q03_budget",
    }

    # 还没答 → invalidated 空；未完成 → next_action=proceed_questionnaire
    assert body["invalidated_answer_ids"] == []
    assert body["is_complete"] is False
    assert body["next_action"] == "proceed_questionnaire"
    # 3 required 都没答 progress=0
    assert body["progress"] == 0
    assert body["progress_pct"] == 0


# ======================================================
# B 用例：完整 answers → 全 required 已答完 → proceed_generate_recommendations
# ======================================================
def test_B_complete_answers_finish() -> None:
    complete_answers = {
        # 基础 3 题（required for ai_recommend）
        "q01_meal_period": ["lunch"],
        "q02_explicit_food": ["undecided"],
        "q03_budget": ["from_20_to_30"],
        # 自适应 3 题（q06 display_if：q01 ∈ lunch/dinner/midnight_snack；这里 lunch 满足）
        "q04_tastes": ["light", "spicy"],
        "q05_avoidances": ["none"],
        "q06_appetite": ["normal"],
    }
    resp = _post({**BASE_ENTRY_VERSION, "answers_by_question_id": complete_answers})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["is_complete"] is True
    assert body["next_action"] == "proceed_generate_recommendations"
    assert body["next_questions"] == []
    assert body["required_not_yet_answered_question_ids"] == []
    assert body["invalidated_answer_ids"] == []
    # 3 required / 3 answered = 100%
    assert body["progress"] == 100
    assert body["progress_pct"] == 100
    # 覆盖维度：meal_period/explicit_food_preference/budget/tastes/avoidances/appetite ≥6
    covered = {d["field_name"] for d in body["covered_dimensions"] if d["covered"]}
    assert len(covered) >= 6


# ======================================================
# C 用例：改上游 → 下游被 invalidated
#    用户先"午餐+饿"，再把餐次改成"早餐"（q06 display_if 不满足 breakfast）
#    则保留的答案中 q06=hungry 应出现在 invalidated_answer_ids
# ======================================================
def test_C_modify_upstream_invalidates_downstream() -> None:
    resp = _post(
        {
            **BASE_ENTRY_VERSION,
            "answers_by_question_id": {
                "q01_meal_period": ["breakfast"],  # 改后：早餐不触发 q06
                # q06 条件 (q01 in lunch/dinner/midnight_snack) 不满足 → 应该 invalidated
                "q06_appetite": ["hungry"],
                # 其他基础题也填一下提高 covered_total，但不是必须
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["from_20_to_30"],
            },
        }
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "q06_appetite" in body["invalidated_answer_ids"]


# ======================================================
# D 用例：G-07 source_type 命中 → 400
#   顶层直接放 source_type；由于我们先于 Pydantic 检查，应该 400 而不是 422
# ======================================================
def test_D_source_type_rejected_400_top_level() -> None:
    payload = {
        **BASE_ENTRY_VERSION,
        "answers_by_question_id": {},
        "source_type": "ai_recommended",  # G-07：命中
    }
    resp = _post(payload)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "BAD_REQUEST"
    assert "source_type" in body["error"]["message"]
    assert body["error"]["details"]["g_rule"] == "G-07"


# ======================================================
# D' 用例（同 G-07 类别补一条）：嵌套 answer value 是一个 object，里面带 source_type
#    注意：answers_by_question_id 要求 value 是 list[str]，
#    但如果客户端硬塞 list[dict]，Pydantic 会 422；我们要保证 G-07 在 Pydantic 之前先拦
# ======================================================
def test_D_nested_source_type_rejected_400() -> None:
    payload = {
        **BASE_ENTRY_VERSION,
        "answers_by_question_id": {
            "q01_meal_period": [{"value": "lunch", "source_type": "user"}],
        },
    }
    resp = _post(payload)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "BAD_REQUEST"
    assert "G-07" == body["error"]["details"]["g_rule"]


# ======================================================
# E 用例：entry_intent 非法 → 422 VALIDATION_ERROR
# ======================================================
def test_E_bogus_entry_intent_422() -> None:
    payload = {
        "entry_intent": "bogus_entry",  # 不在枚举
        "questionnaire_version": "v1.0",
        "answers_by_question_id": {},
    }
    resp = _post(payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # details 里至少有一条指向 entry_intent
    fields = [d["field"] for d in body["error"]["details"]]
    assert any("entry_intent" in f for f in fields)


# ======================================================
# F 用例：版本正则合法但文件不存在 → 404 NOT_FOUND
# ======================================================
def test_F_version_not_found_404() -> None:
    payload = {
        **BASE_ENTRY_VERSION,
        "questionnaire_version": "v9.9",  # 正则 ^v\d+\.\d+$ 合法，但没有 question_bank_v9.9.json
    }
    resp = _post(payload)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "v9.9" in body["error"]["message"]
