"""split_pool_corrected 随机对拍脚本。

用法:
    python test_split_pool.py [样例个数] [--seed 42] [--show 5]

对比对象（均来自 pool_split.py）:
    改正后函数  split_pool_corrected —— ×100 细化 + 末尾 Decimal 回分（结果类型 Decimal）
    原函数      split_pool           —— 按权重比例独立 round（结果类型 int）

检验项:
    1. 总和守恒: sum(结果) 是否精确等于 pool_cents（用 Decimal 精确比较，不信浮点 ==）
    2. 单位合法: 结果是否为「整分」（人民币最小单位）
    3. 非负有界: 0 <= 每项 <= pool_cents
    4. 单调性: 权重大的项分配额不小于权重小的项
    5. 逐项差异: 与参考实现的分项偏差
"""
import argparse
import os
import random
import sys
from decimal import Decimal, localcontext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pool_split import split_pool as split_pool_ref  # noqa: E402
from pool_split import split_pool_corrected  # noqa: E402


# ---------------------------------------------------------------- 待测函数（见 pool_split.py）


# ---------------------------------------------------------------- 样例生成
def gen_case(rng: random.Random) -> tuple[int, list[int]]:
    """随机产出 (pool_cents, weights)，覆盖多种规模形态。"""
    mode = rng.choice(["tiny", "normal", "many", "equal", "mid", "big", "huge"])

    if mode == "tiny":                       # 小额 + 少项，最容易暴露 1 分误差
        pool, k, hi = rng.randint(1, 10), rng.randint(1, 5), 9
    elif mode == "normal":
        pool, k, hi = rng.randint(1, 10**6), rng.randint(2, 10), 10**3
    elif mode == "many":                     # 项数多，diff 接近 n
        pool, k, hi = rng.randint(1, 10**6), rng.randint(50, 500), 10**4
    elif mode == "equal":                    # 等权重，检验公平性
        pool, k = rng.randint(1, 10**6), rng.randint(2, 9)
        w = rng.randint(1, 9)
        return pool, [w] * k
    elif mode == "mid":
        pool, k, hi = rng.randint(10**9, 10**12), rng.randint(2, 20), 10**6
    elif mode == "big":
        pool, k, hi = rng.randint(10**12, 10**18), rng.randint(2, 20), 10**9
    else:                                    # 超出 2**53，浮点必然失真
        pool, k, hi = rng.randint(10**25, 10**30), rng.randint(2, 10), 10**9

    weights = [rng.randint(1, hi) for _ in range(k)]
    return pool, weights


# ---------------------------------------------------------------- 检验工具
def to_dec(v) -> Decimal:
    """把结果值统一成 Decimal 以便精确比较。

    - Decimal: 原样返回（不能走 repr(v)，那会得到 "Decimal('1.23')" 字符串）
    - int    : repr 是纯数字，直接构造
    - float  : repr 是最短往返表示，可还原用户意图的十进制值
    """
    if isinstance(v, Decimal):
        return v
    return Decimal(repr(v))


def dec_sum(values) -> Decimal:
    """精确求和，不做任何浮点近似。

    注意: Decimal 默认上下文精度只有 28 位有效数字，求和大数时会被静默舍入，
    必须显式抬高精度，否则会误报"总和守恒失败"。
    """
    with localcontext() as ctx:
        ctx.prec = 200
        return sum((to_dec(v) for v in values), Decimal(0))


def check_one(pool: int, weights: list[int]) -> dict:
    ref = split_pool_ref(pool, weights)
    try:
        cor = split_pool_corrected(pool, weights)
    except Exception as e:                   # 待测函数抛异常也要被记录，而不是中断对拍
        return {"pool": pool, "weights": weights, "crash": f"{type(e).__name__}: {e}"}

    cor_sum = dec_sum(cor)
    ref_sum = dec_sum(ref)
    n = len(weights)

    return {
        "pool": pool,
        "weights": weights,
        "n": n,
        "cor": cor,
        "ref": ref,
        "cor_sum_ok": cor_sum == pool,
        "ref_sum_ok": ref_sum == pool,
        "cor_sum_err": cor_sum - Decimal(pool),
        "cor_is_int": all(to_dec(v) == to_dec(v).to_integral_value() for v in cor) if cor else True,
        "cor_nonneg": all(v >= 0 for v in cor),
        "cor_bounded": all(v <= pool for v in cor),
        "max_item_diff": max((abs(a - b) for a, b in zip(cor, ref)), default=0),
        "mono_ok": all(
            (cor[i] <= cor[j]) for i in range(n) for j in range(n) if weights[i] <= weights[j]
        ) if cor else True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("count", nargs="?", type=int, default=10000, help="生成的样例个数")
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--show", type=int, default=5, help="最多打印几个失败样例")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cases = [gen_case(rng) for _ in range(args.count)]
    total = len(cases)
    if total == 0:
        print("样例个数为 0，无内容可测")
        return 0

    results = [check_one(pool, ws) for pool, ws in cases]

    def crash_count(fn) -> int:
        """实际调用被测函数，统计抛异常的次数。"""
        n = 0
        for pool, ws in cases:
            try:
                fn(pool, ws)
            except Exception:
                n += 1
        return n

    def sum_fail_count(key: str) -> int:
        """统计非崩溃样例中，总和守恒失败的个数。"""
        return sum(1 for r in results if r.get(f"{key}_sum_ok") is False)

    def report(title: str, fn, key: str) -> None:
        c, f = crash_count(fn), sum_fail_count(key)
        print(f"{title}：")
        print(f"抛异常:            {c}/{total} ({c / total:.1%})")
        print(f"总和守恒失败:       {f}/{total} ({f / total:.1%})")

    report("改正后函数", split_pool_corrected, "cor")
    report("原函数", split_pool_ref, "ref")

    def fmt(values, limit: int = 8) -> str:
        """结果列表统一成纯文本展示，Decimal 不带 Decimal('...') 外壳。"""
        shown = [str(v) for v in values[:limit]]
        if len(values) > limit:
            shown.append("...")
        return "[" + ", ".join(shown) + "]"

    # 选择性输出改正后函数的错误案例
    bad = [r for r in results if "crash" in r or r.get("cor_sum_ok") is False]
    shown = bad[: max(args.show, 0)]
    if shown:
        print("-" * 72)
        print(f"改正后函数错误案例（共 {len(bad)} 例，展示 {len(shown)} 例）：")
        for r in shown:
            ws = r["weights"]
            ws_show = ws if len(ws) <= 6 else ws[:6] + ["..."]
            print(f"\npool={r['pool']}  weights={ws_show}")
            if "crash" in r:
                print(f"       抛异常 = {r['crash']}")
                continue
            print(f"       改正后 = {fmt(r['cor'])}")
            print(f"       原函数 = {fmt(r['ref'])}")
            print(f"       总和误差 = {r['cor_sum_err']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
