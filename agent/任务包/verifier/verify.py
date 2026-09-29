#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py <提交目录> [--json]      —— 与 rubric.md（通用版）逐条对应

设计要点（对应 rubric.md 各节）：
  §1 判定规则   期望值不写死，用规则引擎对「输入数据的每一行」现算 → 换一份数据可直接复用。
  §2 分数缩放   G5/G6/G7 总分固定（32/16/12），按可判行数 N 均摊，换数据后总分仍是 100。
  §6.1 边界行   额定上限为空且机柜用途不在映射表 → 记为「不适用」+ 标注 N/A，不计入 G5/G6/G7 分母。
  §6.2 G7 依据  按类别做包含性检查，不做整串精确比对（允许等价措辞与更详细的写法）。

用法：
    python3 verify.py gold                  # Gold 自测
    python3 verify.py runs/no_skill/r1
    python3 verify.py runs/no_skill/r1 --json
"""

import csv
import hashlib
import io
import json
import os
import re
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIAL = os.path.join(PKG, "材料")

# ────────────────────── 判定规则常量（rubric §1） ──────────────────────
# 术语口径：任务书与 Gold 的结论枚举是「需检修 / 无需检修 / 不适用」。
# 规则原文写作「需维修 / 无需维修」；如需切换术语，只改 NEED / NO_NEED 两个常量。
NEED, NO_NEED, NA = "需检修", "无需检修", "不适用"
ENUM = {NEED, NO_NEED, NA}

STEP1_STATE = "厂商保修返修中"          # 第 1 步：状态排除
ALARM_THRESHOLD = 3                     # 第 2 步：24h 告警次数 ≥ 3
CABINET_LIMIT = {"通用计算": 62, "高密度GPU": 80}   # 第 3 步：空额定上限 → 机柜级限值
CLAUSE = {"状态": "4.3", "告警": "4.2", "超限": "4.1", "正常": ""}

HEADER = ["设备编号", "判定", "触发条款", "判定依据"]
COL = {"id": "设备编号", "usage": "机柜用途", "limit": "额定温度上限(℃)",
       "temp": "运行温度(℃)", "alarm": "24h告警次数", "state": "状态"}

# G5/G6/G7 的总分（rubric §2）：按可判行数均摊
ROW_TOTAL = {0: 32, 1: 16, 2: 12}


# ────────────────────────────── 通用工具 ──────────────────────────────
def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def read_text(path):
    b = read_bytes(path)
    if b is None:
        return None
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def md5_nobom(path):
    """忽略可选 UTF-8 BOM 的内容哈希（BOM 是编码标记，不是内容改动）。"""
    b = read_bytes(path)
    if b is None:
        return None
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    return hashlib.md5(b).hexdigest()


def norm_judge(s):
    """G4/G5 判定列归一化：去空白 + 去括号附注 + 去句读标点（rubric §3 G4）。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", (s or "").strip())
    return re.sub(r"[。；;、，,：:]", "", s).strip()


FW = str.maketrans("０１２３４５６７８９．", "0123456789.")


# 「未触发条款」的等价写法：规则原文是「不记」（留空），实际提交常填「无」等占位词。
EMPTY_CLAUSE = {"", "无", "无触发", "无触发条款", "无条款", "未触发", "不触发", "不记录",
                "none", "n/a", "na", "nil", "-", "—", "–", "/", "／", "空", "（空）", "(空)"}


def norm_clause(s):
    """G6 触发条款归一化：去括注、去空白/引号、全角转半角、去「条款/第…条」等装饰词，
    并把「无」这类占位写法归一为「留空」。
    期望值仍是 rubric 规定的 4.3 / 4.2 / 4.1 / 空，归一化只放宽写法差异。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")      # 去括注（与 norm_judge 同口径）
    s = s.strip().strip("「」\"'").translate(FW)
    s = re.sub(r"^(条款|依据|第)", "", s)
    s = re.sub(r"条$", "", s).strip()
    return "" if s.lower() in EMPTY_CLAUSE else s


# 做包含性判断前先「压平」：去掉引号与所有空白。
# 生产提交里常见 `未“超过”`（引号插在词中间）、`78℃ ＜ 80℃`（全角符 + 空格）等写法。
QUOTES = "“”‘’\"'「」『』《》"


def flatten(s):
    s = s or ""
    for ch in QUOTES:
        s = s.replace(ch, "")
    return re.sub(r"\s+", "", s)


def load_input(sub):
    """读输入数据：优先提交目录内的 巡检数据.csv，否则回退到 材料/ 原件。"""
    for path in (os.path.join(sub, "巡检数据.csv"), os.path.join(MATERIAL, "巡检数据.csv")):
        text = read_text(path)
        if not text:
            continue
        rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
        if rows:
            idx = {c.strip(): i for i, c in enumerate(rows[0])}
            return rows[1:], idx, path
    return None, None, None


def cell(row, idx, key):
    name = COL[key]
    if name not in idx:
        return None
    i = idx[name]
    return row[i].strip() if i < len(row) else None


# ────────────────────────── 规则引擎（rubric §1） ──────────────────────────
def expected(row, idx):
    """返回 (期望判定, 期望条款, 类别)。

    类别 ∈ {状态, 告警, 超限, 正常, 边界, 数据异常}；
    后两类按 rubric §1 补充条款与 §6.1 处理为「不适用」，且不计入 G5/G6/G7 分母。
    """
    # 第 1 步：状态排除（优先级最高）
    if cell(row, idx, "state") == STEP1_STATE:
        return NA, CLAUSE["状态"], "状态"

    # 第 2 步：24h 告警次数 ≥ 3 —— 独立于温度，命中即退出
    try:
        alarm = int(cell(row, idx, "alarm"))
    except (TypeError, ValueError):
        return NA, "", "数据异常"
    if alarm >= ALARM_THRESHOLD:
        return NEED, CLAUSE["告警"], "告警"

    # 第 3 步：额定温度上限为空 → 按机柜用途取机柜级限值
    raw_limit = cell(row, idx, "limit")
    usage = cell(row, idx, "usage") or ""
    if raw_limit == "":
        limit = CABINET_LIMIT.get(usage)          # 用途未知 → None → §6.1 边界
    else:
        try:
            limit = float(raw_limit)
        except ValueError:
            return NA, "", "数据异常"
    try:
        temp = float(cell(row, idx, "temp"))
    except (TypeError, ValueError):
        return NA, "", "数据异常"

    if limit is None:                              # 限值无法确定 → 不适用 + N/A
        return NA, "", "边界"
    # 第 4 / 5 步：运行温度 vs 额定温度上限
    return (NEED, CLAUSE["超限"], "超限") if temp > limit else (NO_NEED, CLAUSE["正常"], "正常")


def basis_ok(category, text):
    """G7：按类别做包含性检查（rubric §3 G7 / §6.2），不做整串精确比对。
    先 flatten（去引号与空白）再做包含判断，避免「未“超过”」这类引号夹字被误杀。"""
    t = flatten(text)
    if category == "状态":
        return ("返修" in t) or ("保修" in t) or ("4.3" in t)
    if category == "告警":
        return ("告警" in t) and ("3" in t)
    if category == "超限":
        over = any(k in t for k in ("超", ">", "＞", "高于", "高出", "突破"))
        return over and any(k in t for k in ("温度", "℃", "上限", "限值", "阈值"))
    if category == "正常":
        # 只要依据表达了「未超过限值」即可（不限定措辞与符号：≤ ＜ <= 不超过 未超 低于 等）
        return any(k in t for k in ("正常", "未超", "不超过", "不大于", "≤", "＜", "≦", "⩽",
                                    "<", "低于", "未达", "合格", "达标", "持平"))
    return False


# ────────────────────────────── 上下文构建 ──────────────────────────────
def build_ctx(sub):
    ctx = {"dir": sub, "notes": []}
    ctx["csv_path"] = os.path.join(sub, "检修判定表.csv")
    ctx["csv_exists"] = os.path.isfile(ctx["csv_path"])
    ctx["md_path"] = os.path.join(sub, "判定说明.md")
    ctx["md_exists"] = os.path.isfile(ctx["md_path"])

    rows, csv_err = [], None
    if ctx["csv_exists"]:
        text = read_text(ctx["csv_path"])
        if text is None:
            csv_err = "无法以 UTF-8 解码（可能非 UTF-8 编码）"
        else:
            try:
                rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
            except csv.Error as e:
                csv_err = "CSV 解析失败：%s" % e
    ctx["csv_err"], ctx["rows"] = csv_err, rows
    ctx["by_id"] = {r[0].strip(): r for r in rows[1:] if r}

    ctx["input_rows"], ctx["input_idx"], ctx["input_path"] = load_input(sub)
    ctx["expect"], ctx["order"] = {}, []
    if ctx["input_rows"] and ctx["input_idx"]:
        for r in ctx["input_rows"]:
            sid = cell(r, ctx["input_idx"], "id")
            if sid is None:
                continue
            ctx["order"].append(sid)
            ctx["expect"][sid] = expected(r, ctx["input_idx"])
    ctx["md"] = read_text(ctx["md_path"]) or ""
    # 边界行说明（只登记一次）：rubric §1 补充条款 / §6.1
    for sid in ctx["order"]:
        cat = ctx["expect"].get(sid, (None, None, None))[2]
        if cat == "边界":
            ctx["notes"].append(
                "%s：额定上限为空且机柜用途不在回退映射表 → 限值无法确定，按不适用处理，"
                "不计入 G5/G6/G7 分母" % sid)
        elif cat == "数据异常":
            ctx["notes"].append(
                "%s：数值字段不可解析 → 无法判定，按不适用处理，不计入 G5/G6/G7 分母" % sid)
    return ctx


def sub_row(ctx, sid):
    r = ctx["by_id"].get(sid, [])
    return (r[1].strip() if len(r) > 1 else "",
            r[2].strip() if len(r) > 2 else "",
            r[3].strip() if len(r) > 3 else "")


def scored_rows(ctx):
    """参与 G5/G6/G7 计分的行（排除边界行与数据异常行，rubric §6.1）。"""
    return [s for s in ctx["order"]
            if ctx["expect"].get(s, (None, None, None))[2] in ("状态", "告警", "超限", "正常")]


def share(ctx, which, total):
    """逐行比对型判分点：总分按可判行数均摊（rubric §2）。"""
    rows = scored_rows(ctx)
    ctx.setdefault("detail", {})
    for sid in ctx["order"]:
        exp_judge, exp_clause, exp_cat = ctx["expect"].get(sid, (None, None, None))
        g_judge, g_clause, g_basis = sub_row(ctx, sid)
        ctx["detail"].setdefault(sid, {
            "期望": "%s / %s / %s类" % (exp_judge, exp_clause or "(空)", exp_cat),
            "提交": "%s / %s / %s" % (g_judge or "(空)", g_clause or "(空)", (g_basis or "(空)")[:26]),
            "类别": exp_cat})
    if not rows:
        return 0
    per = total / len(rows)
    got = 0.0
    for sid in rows:
        exp_judge, exp_clause, exp_cat = ctx["expect"][sid]
        g_judge, g_clause, g_basis = sub_row(ctx, sid)
        if which == 0:
            ok = norm_judge(g_judge) == exp_judge
        elif which == 1:
            ok = norm_clause(g_clause) == exp_clause
        else:
            ok = basis_ok(exp_cat, g_basis)
        if ok:
            got += per
        ctx["detail"][sid]["ok%d" % which] = ok
    return got


# ────────────────────────────── 判分点实现 ──────────────────────────────
def g1(ctx):
    """G1 检修判定表.csv 存在。"""
    return ctx["csv_exists"]


def g2(ctx):
    """G2 判定说明.md 存在。"""
    return ctx["md_exists"]


def g3(ctx):
    """G3 表头 + 数据行第 1 列与输入数据逐一相等且同序。"""
    if not ctx["rows"] or not ctx["order"]:
        return False
    header_ok = [c.strip() for c in ctx["rows"][0]] == HEADER
    order_ok = [r[0].strip() for r in ctx["rows"][1:]] == ctx["order"]
    if not header_ok:
        ctx["notes"].append("G3：表头为 %s，期望 %s" % ([c.strip() for c in ctx["rows"][0]], HEADER))
    if not order_ok:
        ctx["notes"].append("G3：数据行首列与输入数据的设备编号序列不一致")
    return header_ok and order_ok


def g4(ctx):
    """G4 判定列取值全部落在三值枚举内（出现 1 个非法值即为 0 分）。"""
    if not ctx["rows"]:
        return False
    vals = [norm_judge(r[1]) if len(r) > 1 else "" for r in ctx["rows"][1:]]
    bad = [v for v in vals if v not in ENUM]
    if bad:
        ctx["notes"].append("G4：判定列出现非枚举值 %s" % bad[:5])
    return not bad


def g8(ctx):
    """G8 判定说明.md 覆盖输入数据的每个设备编号，每缺 1 个扣 2 分。"""
    if not ctx["order"]:
        return 0
    miss = [s for s in ctx["order"] if s not in ctx["md"]]
    if miss:
        ctx["notes"].append("G8：判定说明.md 未提及 %s" % "、".join(miss))
    return max(0, 5 - 2 * len(miss))


def g9(ctx):
    """G9 输入材料未被改动：忽略 BOM 的内容哈希比对；未随提交物附带则视为无法改动，判通过并标注 N/A。"""
    for name in ("巡检数据.csv", "设备维护规范.md"):
        dst = os.path.join(ctx["dir"], name)
        src = os.path.join(MATERIAL, name)
        if not os.path.isfile(dst):
            ctx["notes"].append("G9：%s 未随提交物附带 → 视为无法改动，判通过（N/A）" % name)
            continue
        if md5_nobom(src) != md5_nobom(dst):
            ctx["notes"].append("G9：%s 与 材料/ 原件不一致" % name)
            return False
    return True


def g10(ctx):
    """G10 CSV 编码与格式健康度：无乱码、无空行。"""
    raw = read_bytes(ctx["csv_path"])
    if raw is None:
        return False
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        ctx["notes"].append("G10：检修判定表.csv 非 UTF-8 编码（乱码）")
        return False
    if "\ufffd" in text:
        ctx["notes"].append("G10：检修判定表.csv 含替换字符 U+FFFD（乱码）")
        return False
    lines = text.splitlines()
    if not any(l.strip() for l in lines):
        ctx["notes"].append("G10：检修判定表.csv 无有效内容")
        return False
    blank = [i for i, l in enumerate(lines, 1) if not l.strip()]
    if blank:
        ctx["notes"].append("G10：检修判定表.csv 第 %s 行为空行" % "、".join(map(str, blank[:5])))
        return False
    return True


PROGRAMMATIC = [
    ("G1", 3, "提交物 检修判定表.csv 存在", g1),
    ("G2", 3, "提交物 判定说明.md 存在", g2),
    ("G3", 5, "表头 + 数据行集合与顺序 = 输入数据行", g3),
    ("G4", 5, "判定列取值全部落在三值枚举内", g4),
    ("G5", 32, "逐行判定 = 规则算出的期望值", lambda c: share(c, 0, ROW_TOTAL[0])),
    ("G6", 16, "逐行触发条款 = 规则算出的期望值", lambda c: share(c, 1, ROW_TOTAL[1])),
    ("G7", 12, "逐行判定依据属于规则规定的类别", lambda c: share(c, 2, ROW_TOTAL[2])),
    ("G8", 5, "判定说明覆盖输入数据的每个设备编号", g8),
    ("G9", 4, "输入材料未被改动（只读约束）", g9),
    ("G10", 3, "CSV 编码与格式健康（无乱码无空行）", g10),
]

SEMANTIC = [
    ("S1", 4, "「额定上限为空且超限」的行讲清回退推理链"),
    ("S2", 4, "「告警次数触发」的行讲清该判据独立于温度"),
    ("S3", 2, "「状态排除」的行讲清状态优先于数值判据"),
    ("S4", 2, "「额定已列出且超限」的行说明未启用回退值"),
]


# ────────────────────────────── 评分与输出 ──────────────────────────────
def evaluate(sub):
    if not os.path.isdir(sub):
        raise SystemExit("提交目录不存在：%s" % sub)
    ctx = build_ctx(sub)

    items, total = [], 0.0
    for pid, score, desc, fn in PROGRAMMATIC:
        raw = fn(ctx)
        if raw is True:
            got, verdict = float(score), "通过"
        elif raw is False:
            got, verdict = 0.0, "未通过"
        else:
            got = round(float(raw), 2)          # 逐行均摊会产生浮点尾数，统一保留 2 位
            verdict = ("通过" if got >= score - 1e-9
                       else ("部分通过(%.1f/%d)" % (got, score) if got > 0 else "未通过"))
        total += got
        items.append({"id": pid, "score": score, "got": got, "verdict": verdict,
                      "desc": desc, "type": "程序化"})
    for sid, score, desc in SEMANTIC:
        items.append({"id": sid, "score": score, "got": None, "verdict": "待人工/模型判定",
                      "desc": desc, "type": "语义"})

    rows = scored_rows(ctx)
    return {"dir": sub, "items": items,
            "programmatic": round(total, 2),
            "programmatic_max": sum(s for _, s, _, _ in PROGRAMMATIC),
            "semantic_max": sum(s for _, s, _ in SEMANTIC),
            "notes": ctx.get("notes", []),
            "csv_err": ctx.get("csv_err"),
            "input_path": ctx.get("input_path"),
            "rows": len(rows),
            "skipped": [s for s in ctx["order"] if s not in rows],
            "detail": ctx.get("detail", {})}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit("用法：python3 verify.py <提交目录> [--json]")
    r = evaluate(args[0])

    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False))
        return

    print("提交目录：%s" % r["dir"])
    print("输入数据：%s（可判行数 %d%s）"
          % (r["input_path"], r["rows"],
             "，豁免行 %s" % "、".join(r["skipped"]) if r["skipped"] else ""))
    if r["csv_err"]:
        print("提示：检修判定表.csv —— %s" % r["csv_err"])
    for n in r["notes"]:
        print("注：%s" % n)
    print("-" * 72)
    print("【逐行比对（规则期望 vs 提交）】")
    for sid, d in r["detail"].items():
        if sid in r["skipped"]:
            flag = "N/A"
        else:
            flag = "".join("✓" if d.get("ok%d" % i) else "✗" for i in (0, 1, 2))
        print("  %-8s %-4s 期望[%s]  提交[%s]" % (sid, flag, d["期望"], d["提交"]))
    print("  （✓/✗ 依次为 判定 / 条款 / 依据；N/A = 边界行，不计入分母）")
    print("-" * 72)
    print("【程序化判分点】")
    for it in r["items"]:
        if it["type"] == "程序化":
            print("  %-4s %5.1f/%-2d  %-18s %s" % (it["id"], it["got"], it["score"],
                                                    it["verdict"], it["desc"]))
    print("-" * 72)
    print("程序化小计：%.1f / %d" % (r["programmatic"], r["programmatic_max"]))
    print()
    print("【语义判分点】（本程序不判定）")
    for it in r["items"]:
        if it["type"] == "语义":
            print("  %-4s %5s/%-2d  %-18s %s" % (it["id"], "?", it["score"],
                                                  it["verdict"], it["desc"]))
    print("-" * 72)
    print("语义小计：待人工/模型判定（满分 %d）" % r["semantic_max"])
    print()
    print("总分（程序化口径）：%.1f / %d" % (r["programmatic"], r["programmatic_max"]))
    print("总分（含语义口径）：%.1f / %d + 语义 %d 分待判定"
          % (r["programmatic"], r["programmatic_max"] + r["semantic_max"], r["semantic_max"]))


if __name__ == "__main__":
    main()
