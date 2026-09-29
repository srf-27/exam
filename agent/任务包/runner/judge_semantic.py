#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
judge_semantic.py —— 用模型对语义判分点 S1-S4 打分（rubric 中标注为「待人工/模型判定」的 12 分）

verify.py 本身不判这 4 条（保持程序化/语义边界清晰），本脚本是可选的外部判分器。
与旧版的关键区别：判分点按 **规则类别** 定义（不是写死设备编号），所以能直接吃任意一组数据 ——
脚本先用 verifier 的规则引擎算出每组里属于各类别的设备，再把「类别 + 设备编号」喂给模型；
某组若不含某类别的行，该项按「不适用」计满分并在输出里注明。

用法：
    python3 runner/judge_semantic.py                       # 判 runs/ 下全部运行记录
    python3 runner/judge_semantic.py runs/d1/no_skill/r1   # 只判指定目录
    python3 runner/judge_semantic.py --group no_skill      # 只判某一组

环境变量：DEEPSEEK_API_KEY（必填，走 OpenAI 兼容接口）
"""

import argparse
import glob
import io
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG, "verifier"))
import verify  # noqa: E402  —— 复用同一套规则引擎，保证程序化/语义口径一致

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
SEM_IDS = ["S1", "S2", "S3", "S4"]

PROMPT = """你是一个严格的评分员。任务是「按机房设备维护规范给巡检数据逐台做停机检修判定」。

下面给你：① 参考答案（Gold，含 4 台设备的判定说明）；② 一份待评提交物的判定说明；
③ 本组数据里**属于各语义判分点类别的设备编号**（由规则引擎从原始巡检数据算出，不是提交物自报）。

请只针对 4 条语义判分点打分，只看推理链是否讲到要点，不关心措辞与篇幅。
若提交物中**不存在**下表中某类别的设备，该项**直接给满分**并在 notes 里注明「本组无此类行」。

待评类别与设备：
{targets}

评分标准：
S1（满分 4）：对「额定上限列为空、且判定为超限」的设备，是否讲清回退推理链。
    四要素：①该列为空；②规范里存在空缺时的回退规则；③因而改用哪个限值（机柜级限值）；④再与实测比较。
    四要素齐全 4 分；缺②或③（只说超限却不说限值从哪来）1 分；完全没解释 0 分。本组无此类行则 4 分。
S2（满分 4）：对「由 24h 告警次数触发」的设备，是否讲清该判据独立于温度。
    三要素：①温度未超限（或不必看温度）；②由告警次数触发；③明确该判据与温度无关。
    齐全 4 分；只给结论、没点明与温度无关 2 分；说成因温度超限 0 分。本组无此类行则 4 分。
S3（满分 2）：对「状态为厂商保修返修中、不纳入判定」的设备，是否讲清状态排除优先于数值判据。
    三要素：①数值/告警表面可能触发；②状态命中排除条款；③故判不适用。齐全 2 分；只写不适用不解释 1 分。
    本组无此类行则 2 分。
S4（满分 2）：对「额定上限已列出、且判定为超限」的设备，是否说明未启用回退值。
    点明额定已列出、故不适用机柜级回退给 2 分；未排除回退但结论对给 1 分；没写 0 分。本组无此类行则 2 分。

只输出 JSON，不要任何其它文字：
{{"S1": <0-4整数>, "S2": <0-4整数>, "S3": <0-2整数>, "S4": <0-2整数>, "notes": "<一句话说明哪类行缺失或扣分原因>"}}
"""


def call_model(messages, model, temperature, retries=3):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("缺少环境变量 DEEPSEEK_API_KEY")
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": 768}
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL, data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit("模型调用失败：%s" % last)


def classify(run_dir):
    """去掉提交物自报，用规则引擎算出本组各类别的设备编号。"""
    path = os.path.join(run_dir, "巡检数据.csv")
    if not os.path.isfile(path):
        return None
    text = io.open(path, encoding="utf-8-sig").read()
    rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
    if not rows:
        return None
    idx = {c.strip(): i for i, c in enumerate(rows[0])}
    g = {"S1": [], "S2": [], "S3": [], "S4": []}
    for r in rows[1:]:
        sid = verify.cell(r, idx, "id")
        if sid is None:
            continue
        _, _, cat = verify.expected(r, idx)
        limit_raw = verify.cell(r, idx, "limit")
        if cat == "告警":
            g["S2"].append(sid)
        elif cat == "状态":
            g["S3"].append(sid)
        elif cat == "超限":
            (g["S1"] if limit_raw == "" else g["S4"]).append(sid)
    return g


def targets_text(g):
    label = {
        "S1": "「额定上限列为空且判定为超限」（回退到机柜级限值）",
        "S2": "「由 24h 告警次数触发」（该判据独立于温度）",
        "S3": "「状态为厂商保修返修中」（状态排除优先）",
        "S4": "「额定上限已列出且判定为超限」（不启用回退值）",
    }
    lines = []
    for k in SEM_IDS:
        ids = g[k]
        lines.append("  %s %s：%s" % (k, label[k], "、".join(ids) if ids else "（本组无此类行）"))
    return "\n".join(lines)


def grade(g, md_text, model):
    gold = io.open(os.path.join(PKG, "gold", "判定说明.md"), encoding="utf-8").read()
    user = (PROMPT.format(targets=targets_text(g))
            + "\n===== 参考答案（Gold）=====\n" + gold
            + "\n\n===== 待评分提交物（判定说明.md）=====\n" + md_text[:9000])
    resp = call_model([{"role": "system", "content": "你是严格、只输出 JSON 的评分员。"},
                       {"role": "user", "content": user}], model, 0.0)
    content = resp["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None, content
    try:
        return json.loads(m.group(0)), content
    except json.JSONDecodeError:
        return None, content


def find_runs(group="*"):
    pat = os.path.join(PKG, "runs", group, "**", "r*")
    out = []
    for d in glob.glob(pat, recursive=True):
        b = os.path.basename(d)
        if os.path.isdir(d) and b.startswith("r") and b[1:].isdigit():
            out.append(d)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="要判的运行目录；缺省判 runs/ 下全部")
    ap.add_argument("--group", default="*")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args()

    dirs = a.dirs or find_runs(a.group)
    for d in dirs:
        md_path = os.path.join(d, "判定说明.md")
        if not os.path.exists(md_path):
            continue
        g = classify(d)
        if g is None:
            print("%-40s 跳过（缺 巡检数据.csv）" % os.path.relpath(d, PKG))
            continue
        uids = {k: len(v) for k, v in g.items()}
        scores, raw = grade(g, io.open(md_path, encoding="utf-8").read(), a.model)
        if scores is None:
            scores = {k: None for k in SEM_IDS}
            scores["notes"] = "解析失败"
        scores["_targets"] = g
        scores["_total"] = sum(v for k, v in scores.items()
                               if k in SEM_IDS and isinstance(v, int))
        with io.open(os.path.join(d, "semantic.json"), "w", encoding="utf-8") as f:
            json.dump(scores, f, ensure_ascii=False, indent=2)
        print("%-40s 语义 %2s/12  类别行数 %s  注:%s"
              % (os.path.relpath(d, PKG), scores["_total"], uids,
                 str(scores.get("notes", ""))[:40]))
        sys.stdout.flush()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
