"""P2-04 推荐生成 E2E 测试。

3 组不同答案链路 → 规则引擎必须给出 3 个不同的 Top1 food_code（解决用户硬约束 MEM-024：
"不同选择链路最后导向同一结果是不对的"）。

还覆盖：
- G-07：请求体里携带 source_type（任意层级）→ 400
- 基础结构：正好 5 条，priority 1–5 连续，generation_mode=rule，source_type=ai_recommended
- 入口非 ai_recommend → 400（P2 阶段硬约束）
- 422：answers_by_question_id key 超过 40 长度正则
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import GenerationMode, SourceType

client = TestClient(create_app())

BASE_PAYLOAD = {
    "entry_intent": "ai_recommend",
    "questionnaire_version": "v1.0",
    "answers_by_question_id": {},
}


def _top_food_code(resp_payload: list[dict]) -> str:
    top = min(resp_payload, key=lambda it: it["priority"])
    return top["food_code"]


class TestBasicShape:
    def test_empty_answers_returns_exactly_five_items_priority_1_through_5(self):
        resp = client.post("/api/v1/recommendations", json=BASE_PAYLOAD)
        assert resp.status_code == 200, resp.text
        items = resp.json()
        assert len(items) == 5
        priorities = sorted(it["priority"] for it in items)
        assert priorities == [1, 2, 3, 4, 5]

    def test_every_item_carries_rule_generation_and_server_derived_source(self):
        resp = client.post("/api/v1/recommendations", json=BASE_PAYLOAD)
        items = resp.json()
        for it in items:
            # G-07 服务端派生
            assert it["source_type"] == SourceType.AI_RECOMMENDED.value
            # P2-02 要求 rule
            assert it["generation_mode"] == GenerationMode.RULE.value
            # G-12 理由可追溯 ≥1
            assert len(it["reason"]["matched_signals"]) >= 1


class TestG07ClientSourceTypeForbidden:
    def test_body_top_level_source_type_returns_400_with_details(self):
        resp = client.post(
            "/api/v1/recommendations",
            json={**BASE_PAYLOAD, "source_type": "user_selected"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "BAD_REQUEST"
        assert "source_type" in body["error"]["message"]
        assert body["error"]["details"]["g_rule"] == "G-07"
        assert body["error"]["details"]["detected_keys"] == ["body.source_type"]

    def test_body_nested_source_type_also_returns_400(self):
        bad_raw = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {
                "q01": [{"value": "lunch", "source_type": "hacked_in"}],
            },
        }
        # top-level answers_by_question_id 值不是 list[str] → 这里 Pydantic 会把它 422 掉，
        # 但我们先 G-07，所以应当先 400（即使嵌套结构非法）
        resp = client.post("/api/v1/recommendations", json=bad_raw)
        # 400 才是对的：source_type 在 any-depth 都要被先拦
        assert resp.status_code == 400, resp.text
        assert "G-07" in resp.text


class TestEntryIntentScope:
    def test_community_entry_rejected_in_p2(self):
        resp = client.post(
            "/api/v1/recommendations",
            json={**BASE_PAYLOAD, "entry_intent": "community"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["details"]["supported_entry_intents"] == ["ai_recommend"]


class TestValidation422:
    def test_answers_key_over_40_chars_returns_422(self):
        bad = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {"a" * 41: ["breakfast"]},
        }
        resp = client.post("/api/v1/recommendations", json=bad)
        assert resp.status_code == 422


# =====================================================================
# 核心：3 组不同问卷链路 → 至少 2 组的 Top1 不同（满足 ≥ 2 组差异化即算 P2 通过；
# 实际我们要保证 3 组 Top1 都不同，对应 MEM-024 "小碗菜不是所有链路的首菜"）
# =====================================================================

QID = {
    "MEAL": "q01_meal_period",
    "EXPL": "q02_explicit_food",
    "BUDG": "q03_budget",
    "TASTE": "q04_tastes",
    "AVO": "q05_avoidances",
    "APP": "q06_appetite",
}


class TestDifferentiatedTop1:
    def test_path_a_breakfast_cheap_any_taste_top1_not_malatang(self):
        """链路 A：早餐 / 20 以内 / 随便推荐"""
        payload = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {
                QID["MEAL"]: ["breakfast"],
                QID["EXPL"]: ["undecided"],
                QID["BUDG"]: ["under_20"],
            },
        }
        resp = client.post("/api/v1/recommendations", json=payload)
        assert resp.status_code == 200, resp.text
        top1 = _top_food_code(resp.json())
        # 早餐不可能首推麻辣烫
        assert top1 != "malatang"

    def test_path_b_explicit_malatang_top1_is_malatang_or_at_least_differs_from_a(self):
        """链路 B：午餐 / 明确想吃麻辣烫 / 预算 20-30 / 辣"""
        payload_a = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {
                QID["MEAL"]: ["breakfast"],
                QID["EXPL"]: ["undecided"],
                QID["BUDG"]: ["under_20"],
            },
        }
        top_a = _top_food_code(
            client.post("/api/v1/recommendations", json=payload_a).json()
        )

        payload_b = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {
                QID["MEAL"]: ["lunch"],
                QID["EXPL"]: ["malatang"],
                QID["BUDG"]: ["from_20_to_30"],
                QID["TASTE"]: ["spicy"],
                QID["APP"]: ["hungry"],
            },
        }
        resp_b = client.post("/api/v1/recommendations", json=payload_b)
        assert resp_b.status_code == 200, resp_b.text
        items_b = resp_b.json()
        top_b = _top_food_code(items_b)

        # MEM-024：链路 B 首菜应和链路 A 不同
        assert top_a != top_b, (
            f"MEM-024 差异化失败：链路 A 首菜={top_a}，链路 B 首菜={top_b} 相同"
        )

        # 明确说要麻辣烫时 malatang 应该出现在 Top5（规则引擎 204 条约定）
        codes_b = [it["food_code"] for it in items_b]
        assert "malatang" in codes_b, f"明确说想吃麻辣烫但 malatang 未在 Top5: {codes_b}"

    def test_path_c_vegetarian_dinner_top1_differs_from_a_and_b(self):
        """链路 C：晚餐 / 严格素食 / 30 以上 / 清淡 + 没啥胃口"""
        # 先拿 A/B 首菜当基准
        top_a = _top_food_code(
            client.post(
                "/api/v1/recommendations",
                json={
                    **BASE_PAYLOAD,
                    "answers_by_question_id": {
                        QID["MEAL"]: ["breakfast"],
                        QID["EXPL"]: ["undecided"],
                        QID["BUDG"]: ["under_20"],
                    },
                },
            ).json()
        )
        top_b = _top_food_code(
            client.post(
                "/api/v1/recommendations",
                json={
                    **BASE_PAYLOAD,
                    "answers_by_question_id": {
                        QID["MEAL"]: ["lunch"],
                        QID["EXPL"]: ["malatang"],
                        QID["BUDG"]: ["from_20_to_30"],
                        QID["TASTE"]: ["spicy"],
                        QID["APP"]: ["hungry"],
                    },
                },
            ).json()
        )

        payload_c = {
            **BASE_PAYLOAD,
            "answers_by_question_id": {
                QID["MEAL"]: ["dinner"],
                QID["EXPL"]: ["undecided"],
                QID["BUDG"]: ["over_30"],
                QID["TASTE"]: ["light"],
                QID["AVO"]: ["vegetarian"],
                QID["APP"]: ["light"],
            },
        }
        resp_c = client.post("/api/v1/recommendations", json=payload_c)
        assert resp_c.status_code == 200, resp_c.text
        top_c = _top_food_code(resp_c.json())

        assert top_c != top_a, f"MEM-024：链路 C 首菜={top_c} 与链路 A 相同"
        assert top_c != top_b, f"MEM-024：链路 C 首菜={top_c} 与链路 B 相同"
