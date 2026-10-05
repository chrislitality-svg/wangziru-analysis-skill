#!/usr/bin/env python3
"""B 站视频「真实表现」指纹 —— 零依赖，Python 3.8+。

对标一个博主前，先把被投流或抽奖活动放大的视频挑出来；只拿剩下的"有机"视频学。

用法：
    python tools/bili_fingerprint.py BV1xxxx BV1yyyy ...      # 直接给 BV 号
    python tools/bili_fingerprint.py -f bvids.txt             # 每行一个 BV 号
    python tools/bili_fingerprint.py -f bvids.txt --json

读的是 B 站公开接口 api.bilibili.com/x/web-interface/view（不需要登录），每个视频间隔 0.6 秒。

三种指纹（都在"同形态"里比：≥10 分钟算长视频，<10 分钟算短视频）：
  投流型      投币率 < 形态中位 30%，收藏率 < 45%，且播放高于形态中位 → 播放被放大，认可没跟上
  点赞活动型  赞币比（点赞 ÷ 投币）的稳健 z 分 > 2.2                → 点赞远远多于投币
  转发活动型  分享率 > 形态中位 8 倍
同形态不足 5 条时，改用「王自如AI」71 个视频的实测中位作基准（见 BASE）。

指纹是启发式规则，说明"数据形态异常"，不等于断定买量。真认可看投币率：投币要花硬币，最难被活动刺激。
"""
import json
import math
import statistics
import sys
import time
import urllib.request

# 「王自如AI」71 个视频实测（每千次播放的中位数；赞币比为 log(赞/币) 的中位与 MAD 换算的尺度）
BASE = {
    "长视频": {"coin": 8.5, "fav": 8.0, "share": 2.5, "lr": 0.87, "lr_scale": 0.59},   # ≥10 分钟，42 条
    "短视频": {"coin": 3.0, "fav": 4.2, "share": 1.3, "lr": 2.02, "lr_scale": 0.55},   # <10 分钟，29 条
}
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}


def fetch(bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    for k in range(3):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))
            if d.get("code") == 0:
                x = d["data"]
                return {"bvid": bvid, "title": x["title"], "owner": x["owner"]["name"], "duration": x["duration"], **x["stat"]}
            return {"bvid": bvid, "error": d.get("message")}
        except Exception as e:  # 网络抖动重试
            err = str(e)
            time.sleep(2 + 2 * k)
    return {"bvid": bvid, "error": err}


def analyse(rows):
    ok = [r for r in rows if "error" not in r and r["view"] > 0]
    for r in ok:
        v = r["view"]
        r["fmt"] = "长视频" if r["duration"] >= 600 else "短视频"
        r.update(coin_r=r["coin"] / v * 1000, fav_r=r["favorite"] / v * 1000, share_r=r["share"] / v * 1000,
                 like_r=r["like"] / v * 1000, lr=math.log((r["like"] + 1) / (r["coin"] + 1)))
    for fmt in ("长视频", "短视频"):
        g = [r for r in ok if r["fmt"] == fmt]
        if not g:
            continue
        if len(g) >= 5:
            med = {k: statistics.median(r[k + "_r"] for r in g) for k in ("coin", "fav", "share")}
            lrs = [r["lr"] for r in g]
            lr_m = statistics.median(lrs)
            lr_s = statistics.median(abs(x - lr_m) for x in lrs) * 1.4826 or BASE[fmt]["lr_scale"]
            view_m = statistics.median(r["view"] for r in g)
            base_src = "本组"
        else:
            b = BASE[fmt]
            med = {"coin": b["coin"], "fav": b["fav"], "share": b["share"]}
            lr_m, lr_s, view_m, base_src = b["lr"], b["lr_scale"], 0, "参考基准"
        for r in g:
            flags = []
            if r["coin_r"] < 0.3 * med["coin"] and r["fav_r"] < 0.45 * med["fav"] and r["view"] > view_m:
                flags.append("投流型")
            if (r["lr"] - lr_m) / lr_s > 2.2:
                flags.append("点赞活动型")
            if r["share_r"] > 8 * med["share"]:
                flags.append("转发活动型")
            r["flags"] = flags
            r["base"] = base_src
    return ok, [r for r in rows if "error" in r]


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    bvids = []
    if "-f" in args:
        path = args[args.index("-f") + 1]
        bvids = [l.strip() for l in open(path, encoding="utf-8") if l.strip().startswith("BV")]
    bvids += [a for a in args if a.startswith("BV")]
    rows = []
    for b in dict.fromkeys(bvids):
        rows.append(fetch(b))
        time.sleep(0.6)
    ok, bad = analyse(rows)
    if "--json" in args:
        print(json.dumps({"videos": ok, "errors": bad}, ensure_ascii=False, indent=1))
        return
    flagged = [r for r in ok if r["flags"]]
    total = sum(r["view"] for r in ok) or 1
    print(f"## 指纹检测：{len(ok)} 个视频，{len(flagged)} 个带指纹，占播放 {sum(r['view'] for r in flagged) / total:.0%}\n")
    print("| 标题 | 形态 | 播放 | 投币‰ | 收藏‰ | 分享‰ | 赞币比 | 指纹 |\n|---|---|---|---|---|---|---|---|")
    for r in sorted(ok, key=lambda r: -r["view"]):
        print(f"| {r['title'][:28]} | {r['fmt']} | {r['view']:,} | {r['coin_r']:.1f} | {r['fav_r']:.1f} | {r['share_r']:.1f} | "
              f"{(r['like'] + 1) / (r['coin'] + 1):.1f} | {'、'.join(r['flags'])} |")
    organic = [r for r in ok if not r["flags"]]
    if organic:
        print(f"\n有机样本 {len(organic)} 个：投币率中位 {statistics.median(r['coin_r'] for r in organic):.1f}‰，"
              f"收藏率中位 {statistics.median(r['fav_r'] for r in organic):.1f}‰。对标只学这些。")
    if any(r["base"] == "参考基准" for r in ok):
        print("\n> 有形态不足 5 条，已改用「王自如AI」实测中位作基准；样本越多越准。")
    for r in bad:
        print(f"\n> {r['bvid']} 读取失败：{r['error']}")
    print("\n> 指纹说明数据形态异常，不等于断定买量。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
