#!/usr/bin/env python3
"""AI 味检测（口播稿 / 视频文案）—— 零依赖，Python 3.8+。

用法：
    python tools/ai_flavor_check.py 稿子.txt          # 输出 Markdown 报告
    python tools/ai_flavor_check.py 稿子.txt --json   # 输出 JSON
    cat 稿子.txt | python tools/ai_flavor_check.py -

检什么（全部是可数的，不靠感觉）：
  1. 书面连接套话   首先/其次/此外/综上所述/值得注意的是……
  2. 宏大空词       赋能/抓手/深度融合/降本增效/时代浪潮……
  3. 修饰堆叠       至关重要/不可或缺/显著/有效地……
  4. AI 腔开头结尾  在当今/随着……的发展/希望本文对你有所帮助……
  5. 对称凑数       一方面…另一方面、不仅…而且
  6. 「不是A，而是B」滥用（全稿超过 1 次就扣分）
  7. 书面标点       破折号、分号（口播没有标点，念不出来）
  8. 长句           单句 > 30 字
  9. 人味不足       「你 / 我 / 我们」人称密度、设问密度、数字密度偏低
  另报：确定词密度（一定/绝对/肯定…，上限 2 次/千字；只提示，不计入指数）

基准：「王自如AI」71 个视频（约 50 小时口播、796,821 字转写）的实测频次，报告里逐项对照。
AI 味指数 0–100，公式写在 score() 里，完全透明；它是体检的起点，不是判决书。
"""
import json
import re
import sys

# ── 基准：71 篇转写实测（次 / 万字）；None = 未单独统计 ──
BASE = {
    "首先": 2.00, "其次": 0.09, "此外": 0.04, "与此同时": 0.01, "综上所述": 0.01, "总而言之": 0.05, "总之": 0.23,
    "值得注意的是": 0.0, "需要指出的是": 0.0, "不难看出": 0.01, "由此可见": 0.0, "换言之": 0.0, "毋庸置疑": 0.03,
    "显而易见": 0.01, "总的来说": 0.0,
    "赋能": 0.18, "助力": 0.06, "抓手": 0.0, "全方位": 0.03, "多维度": 0.04, "深度融合": 0.01, "降本增效": 0.05,
    "数字化转型": 0.14, "新质生产力": 0.0, "高质量发展": 0.0, "时代浪潮": 0.0, "日新月异": 0.01,
    "至关重要": 0.0, "不可或缺": 0.09, "显著": 0.10, "有效地": 0.0, "切实": 0.03,
    "在当今": 0.0, "随着": 1.07, "希望对你": 0.0, "希望对大家": 0.0, "一起来看看": 0.0,
}
CATS = {
    "书面连接套话": ["首先", "其次", "此外", "与此同时", "综上所述", "总而言之", "总之", "值得注意的是", "需要指出的是",
                 "不难看出", "由此可见", "换言之", "毋庸置疑", "显而易见", "总的来说", "众所周知"],
    "宏大空词": ["赋能", "助力", "抓手", "全方位", "多维度", "深度融合", "降本增效", "数字化转型", "新质生产力",
             "高质量发展", "时代浪潮", "日新月异", "前所未有", "系统性工程", "顶层设计", "长期主义", "以点带面",
             "一蹴而就", "保驾护航", "重塑", "无缝"],
    "修饰堆叠": ["至关重要", "不可或缺", "显著", "有效地", "切实", "极大地", "不断地"],
    "AI腔开头结尾": ["在当今", "随着", "让我们一起", "希望本文", "希望对你", "希望对大家", "一起来看看",
                  "欢迎在评论区", "本文将"],
}
PAIRS = {
    "一方面…另一方面": r"一方面[^。！？\n]{0,60}?另一方面",
    "不仅…而且/更/还": r"不仅[^。！？\n]{0,30}?(而且|更|还)",
    "不是A，而是B": r"不是[^。！？\n]{1,24}?而是|是[^。！？\n]{1,16}?而不是",   # 含反向「是B，而不是A」
}
# 人味基准（次 / 千字，71 篇实测）
HUMAN_BASE = {"人称（你/我/我们）": 40.6, "设问（？/吗/呢）": 4.2, "数字": 18.1}   # 转写无问号，设问实际更高
NUM = re.compile(r"\d+(?:\.\d+)?%?|[一二两三四五六七八九十百千万亿]+(?=[个次年天岁倍字小时分钟秒万亿元块条件位家款种步样遍])")


def sentences(text):
    parts = re.split(r"[。！？!?\n]+", text)
    return [p.strip() for p in parts if re.search(r"[一-鿿A-Za-z0-9]", p or "")]


def cjk_len(s):
    return len(re.findall(r"[一-鿿A-Za-z0-9]", s))


def snippet(text, i, w):
    a, b = max(0, i - 8), min(len(text), i + len(w) + 8)
    return text[a:b].replace("\n", " ")


def analyse(text):
    n = max(1, cjk_len(text))
    k = n / 1000
    hits = {}
    for cat, words in CATS.items():
        for w in words:
            idx = [m.start() for m in re.finditer(re.escape(w), text)]
            if idx:
                hits.setdefault(cat, []).append({"word": w, "count": len(idx), "base_per_10k": BASE.get(w),
                                                 "where": [snippet(text, i, w) for i in idx[:3]]})
    pairs = {}
    for name, pat in PAIRS.items():
        ms = list(re.finditer(pat, text))
        if ms:
            pairs[name] = {"count": len(ms), "where": [m.group(0)[:40] for m in ms[:3]]}
    sents = sentences(text)
    lens = sorted(cjk_len(s) for s in sents) or [0]
    long_s = [s for s in sents if cjk_len(s) > 30]
    human = {
        "人称（你/我/我们）": (text.count("你") + text.count("我")) / k,   # "我们"已含在"我"里
        "设问（？/吗/呢）": (text.count("？") + text.count("?") + text.count("吗") + text.count("呢")) / k,
        "数字": len(NUM.findall(text)) / k,
    }
    certainty = len(re.findall(r"一定|绝对|肯定|必然|毫无疑问|百分之百", text)) / k
    return {
        "chars": n, "sentences": len(sents),
        "sent_len_median": lens[len(lens) // 2], "sent_len_p90": lens[min(len(lens) - 1, int(len(lens) * 0.9))],
        "long_sentences": long_s, "hits": hits, "pairs": pairs,
        "dash": len(re.findall(r"—+", text)), "semicolon": text.count("；") + text.count(";"),
        "human_per_k": {key: round(v, 1) for key, v in human.items()},
        "certainty_per_k": round(certainty, 1),
    }


def score(r):
    """AI 味指数（0–100，越高越像 AI）。各项封顶，逐项可追溯。"""
    # 按千字归一，但不足 1000 字按 1000 字算：短稿里一个「首先」不该直接判重度
    k = max(1.0, r["chars"] / 1000)
    tells = sum(h["count"] for hs in r["hits"].values() for h in hs)
    pair_n = sum(v["count"] for name, v in r["pairs"].items() if name != "不是A，而是B")
    not_but = r["pairs"].get("不是A，而是B", {}).get("count", 0)
    allow = max(1.0, r["chars"] / 2000)   # 每 2000 字（约一条 8 分钟口播）放行 1 次：纠偏主句专用
    parts = {
        "套话/空词/修饰/AI腔（每千字 ×8，封顶 35）": min(35, tells / k * 8),
        "对称凑数（每千字每处 6，封顶 12）": min(12, pair_n / k * 6),
        "「不是A而是B」超出放行（每 2000 字放行 1 次，多 1 次 6，封顶 18）": min(18, max(0, not_but - allow) * 6),
        "破折号/分号（每千字每个 2，封顶 8）": min(8, (r["dash"] + r["semicolon"]) / k * 2),
        "长句 >30 字（每千字每句 3，封顶 9）": min(9, len(r["long_sentences"]) / k * 3),
        "人称密度 < 12/千字（+8）": 8 if r["human_per_k"]["人称（你/我/我们）"] < 12 else 0,
        "设问密度 < 2/千字（+5）": 5 if r["human_per_k"]["设问（？/吗/呢）"] < 2 else 0,
        "数字密度 < 2/千字（+5）": 5 if r["human_per_k"]["数字"] < 2 else 0,
    }
    total = round(min(100, sum(parts.values())))
    grade = "人话" if total < 20 else "轻微 AI 味" if total < 40 else "明显 AI 味" if total < 60 else "重度 AI 味"
    return total, grade, {key: round(v, 1) for key, v in parts.items() if v}


def to_md(r, total, grade, parts):
    L = [f"## AI 味体检：{total} / 100（{grade}）", "",
         f"- 字数 {r['chars']} · 句数 {r['sentences']} · 句长中位 {r['sent_len_median']} 字 · P90 {r['sent_len_p90']} 字"
         f"（参考：口播句长中位 18 字，极少超过 30 字）", ""]
    if parts:
        L += ["**扣分构成**", ""] + [f"- {key}：+{v}" for key, v in parts.items()] + [""]
    if r["hits"]:
        L += ["**命中的 AI 味词**（括号内为 71 篇口播实测：次/万字）", "", "| 类别 | 词 | 次数 | 口播基准 | 位置 |", "|---|---|---|---|---|"]
        for cat, hs in r["hits"].items():
            for h in hs:
                base = "—" if h["base_per_10k"] is None else f"{h['base_per_10k']:.2f}"
                L.append(f"| {cat} | {h['word']} | {h['count']} | {base} | {' / '.join(h['where'])} |")
        L.append("")
    if r["pairs"]:
        L += ["**句式**", ""] + [f"- {name}：{v['count']} 次 —— {' / '.join(v['where'])}" for name, v in r["pairs"].items()] + [""]
    if r["dash"] or r["semicolon"]:
        L += [f"**书面标点**：破折号 {r['dash']} · 分号 {r['semicolon']}（口播念不出来，换成停顿或拆句）", ""]
    if r["long_sentences"]:
        L += ["**长句 >30 字**", ""] + [f"- {s[:60]}" for s in r["long_sentences"][:5]] + [""]
    L += ["**人味指标**（次/千字；括号内为 71 篇口播实测）", ""]
    for key, v in r["human_per_k"].items():
        L.append(f"- {key}：{v}（{HUMAN_BASE[key]}）")
    flag = "（超过 2，建议删掉一部分：笃定感靠结构，不靠确定词）" if r["certainty_per_k"] > 2 else ""
    L += [f"- 确定词（一定/绝对/肯定…）：{r['certainty_per_k']}（71 篇口播中位 1.67，上限 2）{flag}"]
    L += ["", "> 指数只是起点：先修结构（有没有立场、有没有具体），再删套话；别为了凑指标硬塞「对吧」「其实」——那是学腔调。"]
    return "\n".join(L)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    text = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    r = analyse(text)
    total, grade, parts = score(r)
    if "--json" in sys.argv:
        print(json.dumps({"score": total, "grade": grade, "parts": parts, **r}, ensure_ascii=False, indent=1))
    else:
        print(to_md(r, total, grade, parts))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
