"""食物字典加载器与内存仓库。

职责：
- 从 JSON 文件加载食物字典；
- 应用 ValidationHelpers（唯一 food_code / G-11 医学边界 / G-08 非空集合）；
- 暴露 FoodDictionaryRepository（当前为只读内存版本；数据库化后按接口替换，不影响调用方）。

本文件不引入 G-12 差异化逻辑（规则引擎 P2-02），不引入 source_type 派生（服务层 P2-04）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from app.schemas import (
    FoodDictionaryItem,
    ValidationHelpers,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FOOD_DICT_V1_PATH = DATA_DIR / "food_dictionary_v1.0.json"
DEFAULT_DICTIONARY_VERSION = "v1.0"


class FoodDictionaryRepository:
    """内存只读食物字典仓库。
    初始版本：从单一 JSON 文件加载，加载时一次性校验；
    后续数据库化时，只要维持 get / list_enabled / get_version 三个签名，调用方无需改动。
    """

    def __init__(
        self,
        items: list[FoodDictionaryItem],
        dictionary_version: str = DEFAULT_DICTIONARY_VERSION,
    ) -> None:
        # 加载即校验，任何失败都在启动时暴露（P1 质量基线）。
        ValidationHelpers.validate_unique_food_codes(items)
        ValidationHelpers.validate_medical_boundary(items)
        ValidationHelpers.validate_enabled_pool_size(items, min_size=5)
        self._items = list(items)
        self._by_code: dict[str, FoodDictionaryItem] = {
            it.food_code: it for it in self._items
        }
        if len(self._by_code) != len(self._items):
            raise ValueError("food_code 去重后长度与原列表不一致")
        self._dictionary_version = dictionary_version

    @property
    def dictionary_version(self) -> str:
        return self._dictionary_version

    def get(self, food_code: str) -> FoodDictionaryItem | None:
        return self._by_code.get(food_code)

    def require(self, food_code: str) -> FoodDictionaryItem:
        item = self.get(food_code)
        if item is None:
            raise KeyError(f"food_code={food_code!r} 在字典 {self._dictionary_version} 中不存在")
        return item

    def list_all(self) -> list[FoodDictionaryItem]:
        return list(self._items)

    def list_enabled(self) -> list[FoodDictionaryItem]:
        return [it for it in self._items if it.is_enabled]

    def enabled_count(self) -> int:
        return sum(1 for it in self._items if it.is_enabled)

    def codes_enabled(self) -> tuple[str, ...]:
        return tuple(it.food_code for it in self.list_enabled())

    def contains_enabled(self, food_code: str) -> bool:
        it = self._by_code.get(food_code)
        return it is not None and it.is_enabled

    def validate_food_codes(self, codes: Iterable[str]) -> list[str]:
        """返回不存在或未启用的 food_code 列表（空列表表示全部合法）。
        供规则引擎、推荐输出校验（G-02：5 个候选都必须在启用词典内）复用。
        """
        invalid: list[str] = []
        for c in codes:
            it = self._by_code.get(c)
            if it is None or not it.is_enabled:
                invalid.append(c)
        return invalid


def load_food_dictionary_json(path: Path) -> list[FoodDictionaryItem]:
    """从 JSON 文件加载并 Pydantic 校验每一条 FoodDictionaryItem。
    任一记录字段非法都会在加载时失败，避免把坏掉的数据塞进运行时。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{path} 顶层必须是数组")
    return [FoodDictionaryItem.model_validate(obj) for obj in raw]


@lru_cache(maxsize=8)
def get_food_dictionary_repository(
    dictionary_version: str = DEFAULT_DICTIONARY_VERSION,
) -> FoodDictionaryRepository:
    """带缓存的工厂函数。
    当前唯一版本 v1.0；未来多版本时可把 version 作为字典路径开关。
    """
    if dictionary_version != DEFAULT_DICTIONARY_VERSION:
        raise ValueError(f"暂不支持 dictionary_version={dictionary_version}")
    items = load_food_dictionary_json(FOOD_DICT_V1_PATH)
    return FoodDictionaryRepository(items=items, dictionary_version=dictionary_version)
