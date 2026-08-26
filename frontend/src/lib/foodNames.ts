/**
 * P7-03：食物名称显示工具
 * 优先使用后端返回的 food_name_zh；无则使用本地映射或格式化 food_code。
 */

export interface FoodLike {
  food_code: string;
  food_name_zh?: string | null;
}

/** 本地保底映射（与后端 _FOOD_NAME_ZH / food_dictionary_v1.0.json 一致，用于后端字段缺失时兜底） */
const FALLBACK_NAMES: Record<string, string> = {
  ramen_tonkotsu: "豚骨拉面",
  sushi_salmon: "三文鱼寿司",
  budae_jjigae: "部队锅",
  bibimbap: "石锅拌饭",
  malatang: "麻辣烫",
  mapo_tofu: "麻婆豆腐",
  chicken_salad: "鸡胸肉沙拉",
  poke_bowl: "三文鱼波奇饭",
  cheesecake_basque: "巴斯克芝士蛋糕",
  yakitori: "烤鸡皮串",
  sushi: "寿司",
  korean_stew: "韩式大酱汤",
  braised_pork_rice: "卤肉饭",
  pasta: "意大利面",
  thai_curry: "泰式咖喱",
  wonton_noodle: "云吞面",
  mapo_tofu_rice: "麻婆豆腐盖饭",
  braised_beef_noodle: "红烧牛肉面",
  korean_bbq: "韩式烤肉",
  buddhist_vegetarian: "罗汉斋",
  spicy_hotpot: "麻辣火锅",
  hotpot_sichuan: "四川火锅",
  hotpot: "火锅",
  pasta_carbonara: "卡邦尼意面",
  pasta_bolognese: "肉酱意面",
  pizza_margherita: "玛格丽特披萨",
  burger_classic: "经典汉堡",
  burger: "汉堡",
  pizza: "披萨",
  bbq: "烧烤",
  fried_chicken: "炸鸡",
  kung_pao_chicken: "宫保鸡丁",
  dumplings_pork: "猪肉水饺",
  xiaolongbao: "小笼包",
  fried_rice_yangzhou: "扬州炒饭",
  congee: "粥",
  noodles_zhajiang: "炸酱面",
  noodles_beef: "牛肉面",
  salad_caesar: "凯撒沙拉",
  steak_ribeye: "肋眼牛排",
  tacos: "墨西哥卷饼",
  curry_rice: "咖喱饭",
  tom_yum: "冬阴功汤",
  phở: "越南河粉",
  pad_thai: "泰式炒河粉",
  sandwich_club: "俱乐部三明治",
  milk_tea_boba: "珍珠奶茶",
  smoothie_mango: "芒果冰沙",
  coffee_espresso: "浓缩咖啡",
  coffee_latte: "拿铁咖啡",
};

/** 将 food_code（snake_case）转换为可读显示名（仅在没有中文名时兜底） */
function formatFoodCode(code: string): string {
  if (FALLBACK_NAMES[code]) return FALLBACK_NAMES[code];
  return code
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** 获取食物的中文显示名 */
export function displayFoodName(item: FoodLike): string {
  if (item.food_name_zh && typeof item.food_name_zh === "string" && item.food_name_zh.trim().length > 0) {
    return item.food_name_zh;
  }
  return formatFoodCode(item.food_code);
}
