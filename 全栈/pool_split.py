"""
pool_split.py —— 每日瓜分奖池的分账逻辑

这是我们线上「每日瓜分奖池」的真实代码，为这道题做了简化
（去掉了数据库读写和日期处理，只留下算钱的部分）。

背景：
  平台每天放一个奖池。当天达标的用户，按各自的贡献权重瓜分池子里的钱。

约定：
  · 所有金额都是整数「分」，系统里不存在半分钱。
  · weights 是每个人的贡献权重（线上用的是「合格采集秒数」）。

硬规则（产品定的，不能破）：
  分完之后，所有人拿到的钱加起来必须【正好等于】池子总额。
  多一分 = 平台凭空多发了钱。
  少一分 = 有人的钱卡在系统里没发出去。
"""


def split_pool(pool_cents: int, weights: list[int]) -> list[int]:
    """把 pool_cents 按 weights 的比例分给每个人，返回每人拿到多少分。

    返回列表与 weights 一一对应。
    """
    total = sum(weights)
    return [round(pool_cents * w / total) for w in weights]

def is_valid(pool_cents: int, result: list[int]) -> bool:
    return sum(result) == pool_cents

if __name__ == "__main__":
    # 一个跑得通的例子：奖池 100 元，三个人权重相同
    pool_cents = 10000

    result = split_pool(10000, [1, 1, 1])
    print(result)

    print(is_valid(pool_cents, result))
