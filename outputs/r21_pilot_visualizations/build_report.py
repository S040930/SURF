#!/usr/bin/env python3
"""Build the self-contained Chinese analysis HTML report for the r21 pilot.

Reads pilot_aggregate.json for numbers, embeds the four PNG charts as base64,
and writes r21_pilot_analysis_report.html. Purely local; does not touch the DB.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

CSS = """
:root{
  --ink:#172033; --muted:#667085; --line:#e5e7eb;
  --blue:#173f5f; --blue-2:#2f80ed; --orange:#f2994a; --red:#c44536; --green:#27ae60;
  --bg:#f6f8fb; --card:#fff; --soft:#eef3f9;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;color:var(--ink);background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.7;font-size:15px}
main{max-width:1180px;margin:0 auto;padding:40px 22px 70px}
h1{margin:0 0 6px;font-size:31px;letter-spacing:-.02em}
h2{margin:46px 0 6px;font-size:23px;padding-top:8px;border-top:3px solid var(--blue);display:inline-block}
h3{margin:26px 0 8px;font-size:18px}
p{margin:10px 0}
.subtitle{color:var(--muted);margin:0 0 6px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px}
.chip{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:12.5px;color:var(--muted)}
.chip b{color:var(--blue)}
.chip.ok{background:#eaf6ef;border-color:#bfe6cd;color:#1e6b3c}
.notice{padding:14px 18px;border-left:4px solid var(--orange);background:#fff8ef;border-radius:8px;color:#6b4b25;margin:16px 0}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:0 3px 12px rgba(16,24,40,.04)}
.label{color:var(--muted);font-size:13px}
.value{display:block;margin-top:4px;font-size:24px;font-weight:700;color:var(--blue)}
.value small{font-size:13px;font-weight:500;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.grid1{display:grid;grid-template-columns:1fr;gap:18px}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:0 3px 12px rgba(16,24,40,.04)}
figure img{display:block;width:100%;height:auto;border-radius:7px}
figcaption{padding:8px 5px 2px;color:var(--muted);font-size:13px}
.table-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0}
table{width:100%;border-collapse:collapse;min-width:640px}
th,td{padding:10px 14px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-size:13px;background:#fafbfc}
tr:last-child td{border-bottom:0}
td.pos{color:var(--red);font-weight:600}
td.neg{color:var(--green);font-weight:600}
td.hl{background:#fdf1e3}
.small{color:var(--muted);font-size:13px}
.finding{background:var(--soft);border-left:4px solid var(--blue-2);border-radius:8px;padding:12px 16px;margin:14px 0}
.finding b{color:var(--blue)}
.key{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:16px 0;box-shadow:0 3px 12px rgba(16,24,40,.04)}
.key h3{margin-top:0}
.key ul{margin:8px 0 0;padding-left:22px}
.key li{margin:6px 0}
footer{margin-top:36px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:16px}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}h1{font-size:26px}}
"""


def b64img(name: str) -> str:
    return base64.b64encode((HERE / name).read_bytes()).decode("ascii")


def main() -> None:
    data = json.loads((HERE / "pilot_aggregate.json").read_text(encoding="utf-8"))
    op = data["operational"]
    s = data["summary"]

    def get(q: str, h: int, c: str) -> float:
        return next(r["mean_nae"] for r in s if r["question"] == q and r["history"] == h and r["condition"] == c)

    def mem_nm(q: str, h: int = 20) -> float:
        return ((get(q, h, "arm") + get(q, h, "crm")) / 2) - get(q, h, "nm")

    def arm_crm(q: str, h: int = 20) -> float:
        return get(q, h, "arm") - get(q, h, "crm")

    qs = ["4.13", "5.7"]
    macro_mem_nm = sum(mem_nm(q) for q in qs) / 2
    macro_arm_crm = sum(arm_crm(q) for q in qs) / 2
    macro_nm = sum(get(q, 20, "nm") for q in qs) / 2
    macro_mem = sum((get(q, 20, "arm") + get(q, 20, "crm")) / 2 for q in qs) / 2
    h10_all = [get(q, 10, c) for q in qs for c in ("nm", "crm", "arm")]
    h20_all = [get(q, 20, c) for q in qs for c in ("nm", "crm", "arm")]
    h10_mean = sum(h10_all) / len(h10_all)
    h20_mean = sum(h20_all) / len(h20_all)
    rise = sum(1 for q in qs for c in ("nm", "crm", "arm") if get(q, 20, c) > get(q, 10, c))

    model = op["model"]
    completed = op["completed_at"][:16].replace("T", " ")
    img1, img2, img3, img4 = (b64img(n) for n in ("01_operational_dashboard.png", "02_nae_h20_by_question.png", "03_learning_curve.png", "04_effect_heatmap.png"))

    def row(q: str) -> str:
        nm, crm, arm = (get(q, 20, c) for c in ("nm", "crm", "arm"))
        return (
            f"<tr><td>{q}</td><td>{nm:.4f}</td><td>{crm:.4f}</td><td>{arm:.4f}</td>"
            f"<td class='{'pos' if arm_crm(q) > 0 else 'neg'}'>{arm_crm(q):+.4f}</td>"
            f"<td class='{'pos' if mem_nm(q) > 0 else 'neg'}'>{mem_nm(q):+.4f}</td></tr>"
        )

    def mem_rows(q: str) -> str:
        nm = get(q, 20, "nm")
        mem = (get(q, 20, "arm") + get(q, 20, "crm")) / 2
        return f"<tr><td>{q}</td><td>{nm:.4f}</td><td>{mem:.4f}</td><td class='{'pos' if mem_nm(q)>0 else 'neg'}'>{mem_nm(q):+.4f}</td></tr>"

    def arm_rows(q: str) -> str:
        arm = get(q, 20, "arm")
        crm = get(q, 20, "crm")
        return f"<tr><td>{q}</td><td>{arm:.4f}</td><td>{crm:.4f}</td><td class='{'pos' if arm_crm(q)>0 else 'neg'}'>{arm_crm(q):+.4f}</td></tr>"

    lc_rows = ""
    for q in qs:
        for c, label in (("nm", "NM"), ("crm", "CRM"), ("arm", "ARM")):
            d = get(q, 20, c) - get(q, 10, c)
            cls = "pos" if d > 0 else "neg"
            lc_rows += f"<tr><td>{q}</td><td>{label}</td><td>{get(q,10,c):.4f}</td><td>{get(q,20,c):.4f}</td><td class='{cls}'>{d:+.4f}</td></tr>"

    app_rows = ""
    for q in qs:
        for h in (10, 20):
            nm, crm, arm = (get(q, h, c) for c in ("nm", "crm", "arm"))
            app_rows += f"<tr><td>{q}</td><td>{h}</td><td>{nm:.4f}</td><td>{crm:.4f}</td><td>{arm:.4f}</td></tr>"

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>项目1 · r21 试点实验结果分析（GPT-5.6-luna）</title>
<style>{CSS}</style>
</head>
<body>
<main>

<h1>项目1 · r21 试点实验结果分析</h1>
<p class="subtitle">Codex CLI 离线评分记忆（CRM / ARM）vs 无记忆（NM）· 单模型双题目技术试点的事后探索性分析</p>
<div class="meta">
  <span class="chip">题目：<b>4.13</b> · <b>5.7</b></span>
  <span class="chip">条件：<b>NM / CRM / ARM</b></span>
  <span class="chip">模型：<b>{model}</b>（codex-cli 0.147.0，reasoning_effort=high）</span>
  <span class="chip">历史：<b>h=10</b>（探测）· <b>h=20</b>（终点）</span>
  <span class="chip ok">状态：completed · {completed}</span>
</div>

<div class="notice">
本报告是对已完成技术试点 <b>锁定的 technical-only 报告</b>（analysis_status=pilot_technical_only，core=null）的<b>事后探索性解封</b>：样本只有 2 道试点题、单模型，所有读数为<b>描述性观察</b>，不构成正式 RQ1 / RQ2 结论，也不能据此推广到正式题、其他模型或总体准确率。
</div>

<h2>一、核心结论（一页速览）</h2>
<div class="key">
<h3>关键读数（h=20 终点，n=15 个 endpoint×trajectory 单元/格）</h3>
<ul>
  <li><b>运行健康</b>：680 次 attempts、<b>0 次调用级失败</b>、0 次失败记录；延迟 p50 18.3s、p95 39.6s，全部远低于 600s 超时上限——较 r20 的尾部超时问题明显改善。</li>
  <li><b>题目难度差异仍在</b>：h=20 时 4.13 的 NAE（0.46–0.51）远高于 5.7（0.07–0.10）。4.13 仍是"难题"。</li>
  <li><b>记忆未改善误差（RQ2 描述性）</b>：两格 memory mean − NM 均为正（4.13 上 +0.023，5.7 上 +0.012），题目宏平均 <b>+0.018</b>。即本试点中外部记忆并未降低评分误差。</li>
  <li><b>ARM vs CRM 无一致信号（RQ1 描述性）</b>：两格 ARM−CRM 均为正（+0.040 / +0.019），宏平均 +0.030（CRM 略优）；但 n=2 题、无置信区间，视为噪声。</li>
  <li><b>更多历史未降误差</b>：h=10 → h=20，6 格中 4 格误差上升，总体均值 0.280 → 0.280（基本平台）。</li>
</ul>
</div>

<h2>二、运行与数据概况</h2>
<p>r21 pilot 通过 Codex CLI（codex-cli 0.147.0，模型 gpt-5.6-luna，reasoning_effort=high，sandbox=read-only）完整走通了 r20 形状的管线：评分与记忆更新全部成功，无调用级失败。全部 240 个记忆快照均在 6 条上限以内。总体执行稳定，延迟分布集中在 10–40s，p95 39.6s、最大值 75.5s，均低于 600s 超时上限——正式 8 题运行可按此尾部延迟规划并发。</p>

<div class="cards">
  <div class="card"><span class="label">Attempts / 失败</span><span class="value">680<small> / 0</small></span></div>
  <div class="card"><span class="label">端点单元</span><span class="value">180<small>（h=10 与 h=20）</small></span></div>
  <div class="card"><span class="label">延迟 p50 / p95</span><span class="value">{op['latency_p50_ms']/1000:.1f}<small>s / {op['latency_p95_ms']/1000:.1f}s</small></span></div>
  <div class="card"><span class="label">记忆快照</span><span class="value">{op['snapshot_count']}<small> / 0 违规</small></span></div>
</div>

<figure>
  <img src="data:image/png;base64,{img1}" alt="运行指标">
  <figcaption>图 1 · 运行健康度：attempts 680 / 失败 0；延迟 p50 18.3s、p95 39.6s；记忆快照 240 个、最大条目 6、最大可见 token（近似）1295。</figcaption>
</figure>

<div class="finding"><b>注意（近似口径）</b>：快照可见 token 数在此按字符数近似估算，不是 o200k 编码的真实 token 数；r21 文档的可见上限为 720 tokens，此近似值会系统性高估，不能解读为约束违规。需要真实计数应使用项目固定的 o200k_base 校准。</div>

<h2>三、总体结果：题目难度差异显著</h2>
<p>NAE（归一化绝对误差，越低越好）在 h=20 终点的题目×条件均值如下。两组题目差异依然极大：4.13 全部 3 格 NAE ≥ 0.46，5.7 则在 0.07–0.10 的窄区间。任何"记忆是否有帮助"的结论都必须分题目看。</p>

<figure>
  <img src="data:image/png;base64,{img2}" alt="h=20 NAE 对比">
  <figcaption>图 2 · h=20 各题目、各条件的 NAE 对比；每柱为 15 个 endpoint×trajectory 单元，两次 h=20 重复评分先平均再计算误差。</figcaption>
</figure>

<div class="table-wrap">
<table>
  <tr><th>题目</th><th>NM</th><th>CRM</th><th>ARM</th><th>ARM−CRM</th><th>记忆均值−NM</th></tr>
  {row('4.13')}
  {row('5.7')}
</table>
</div>
<p class="small">题目宏平均（2 格等权）h=20：NM 0.2687，记忆条件 0.2862；4.13 贡献了绝大部分误差。</p>

<h2>四、记忆是否降低误差？（RQ2 · 描述性）</h2>
<p>对照"两个记忆条件均值 vs 无记忆"：(ARM+CRM)/2 − NM，负值表示记忆有帮助。</p>

<figure>
  <img src="data:image/png;base64,{img4}" alt="效果差值热图">
  <figcaption>图 3 · h=20 描述性对比：左列 ARM−CRM，右列记忆均值−NM。红色为正值（右侧条件更差），蓝色为负值（右侧条件更优）。</figcaption>
</figure>

<div class="table-wrap">
<table>
  <tr><th>题目</th><th>NM</th><th>记忆均值</th><th>记忆均值−NM</th></tr>
  {mem_rows('4.13')}
  {mem_rows('5.7')}
</table>
</div>
<p>题目宏平均：<b>+0.018</b>（正值，即记忆条件误差更高）。分题目看：4.13 上 +0.023，5.7 上 +0.012。</p>
<div class="finding"><b>读数</b>：本试点中外部记忆<b>没有降低评分误差</b>，两格方向一致（均为正值），但幅度很小（约 0.01–0.02）。与 r20（DeepSeek/Doubao 双模型）的结论方向一致，但更温和。可能的解释方向（均为推测，不能由 n=2 题证实）：记忆内容与该题评分口径不完全一致，或快照序列化与 prompt 竞争注意力；需要正式 8 题 + 置信区间来分辨。</div>

<h2>五、表示对比：ARM vs CRM（RQ1 · 描述性）</h2>
<p>对照两种记忆表示：ARM − CRM，负值表示抽象规则记忆误差更低。两格均为正，方向一致但幅度很小。</p>

<div class="table-wrap">
<table>
  <tr><th>题目</th><th>ARM</th><th>CRM</th><th>ARM−CRM</th></tr>
  {arm_rows('4.13')}
  {arm_rows('5.7')}
</table>
</div>
<p>题目宏平均：<b>+0.030</b>（弱正值，略偏向 CRM），两格都为正、无方向冲突。但在 n=2 题、无 bootstrap 区间下，这一点不构成 CRM 优于 ARM 的证据，只记录为方向一致但幅度很小的探索性观察（较 r20 的"四格交错"更稳定，仍不能作结论）。</p>

<h2>六、学习曲线：更多历史并未降低误差</h2>

<figure>
  <img src="data:image/png;base64,{img3}" alt="学习曲线">
  <figcaption>图 4 · h=10 → h=20 探索性学习曲线。</figcaption>
</figure>

<div class="table-wrap">
<table>
  <tr><th>题目</th><th>条件</th><th>h=10</th><th>h=20</th><th>Δ</th></tr>
  {lc_rows}
</table>
</div>
<p class="small">总体 h=10 均值 {h10_mean:.4f} → h=20 均值 {h20_mean:.4f}（6 格等权；6 格中 {rise} 格上升）。</p>
<div class="finding"><b>注意口径差异</b>：h=10 是 5 个固定端点各评 1 次，h=20 是同样 5 个端点各评 2 次后平均——h10→h20 的差值同时混入了"重复评分取均值"的降噪效应，因此曲线不能纯归因于记忆历史增长。</div>
<p><b>读数</b>：4.13 上 NM/CRM 随历史微升、ARM 略降；5.7 上 NM 略降、CRM/ARM 微升。整体呈现"平台"（0.280→0.280），没有任何"历史越多误差越低"的迹象，与 r20 结论一致。</p>

<h2>七、结论与解释边界</h2>
<div class="key">
<h3>试点层面的结论（描述性，仅限这 2 道题、单模型）</h3>
<ul>
  <li><b>管线工程上稳健</b>：0 调用级失败、0 失败记录、延迟远低于超时上限，具备进入正式 8 题运行的可行性。</li>
  <li><b>本试点未观察到记忆带来的收益</b>：记忆均值−NM 宏平均 +0.018；ARM−CRM 宏平均 +0.030（CRM 略优），均无一致的方向性证据。</li>
  <li><b>题目间异质性仍然极大</b>（4.13 vs 5.7 的 NAE 差约 5–6 倍），正式分析必须坚持"题目等权 + 题目级宏平均"的口径。</li>
</ul>
</div>
<h3>为什么这不能当作正式结论</h3>
<ul>
  <li>只有 <b>n=2 题、单模型</b>，无 10,000 次 bootstrap 区间，无法区分"真效应"与"题目/轨迹噪声"。</li>
  <li>试点协议明确<b>不展示方向性效果</b>；本报告是对锁定 technical-only 报告（core=null）的事后解封，不改变其状态。</li>
  <li>h=10 探测与 h=20 终点测量口径不同，学习曲线差值混入了重复评分降噪。</li>
  <li>模型为单个 operator 选定的 Codex CLI 配置，不代表所有 LLM 或供应商。</li>
</ul>
<h3>对正式运行的提示</h3>
<ul>
  <li><b>延迟健康</b>：p95 39.6s、max 75.5s，远低于 600s 超时——Codex CLI 路线的尾部延迟较 r20 的 API 路线明显更稳，可按 p95 规划并发。</li>
  <li><b>记忆 token 近似偏大</b>：如本报告近似口径所示，建议正式前用 o200k 真实计数复核快照可见 token，避免接近 720 上限。</li>
  <li><b>单模型限制</b>：正式协议若保持单模型，模型级结论范围有限；如需对模型总体作推断，应按协议补充第二个固定模型配置。</li>
</ul>

<h2>附录 · 完整 12 格 NAE 汇总（h=10 与 h=20）</h2>
<p>每格为 15 个 endpoint×trajectory 单元；h=20 两次重复评分先取均值再计算 NAE。数据源：<code>pilot_nae_summary.csv</code> / <code>pilot_aggregate.json</code>。</p>

<div class="table-wrap">
<table>
  <tr><th>题目</th><th>h</th><th>NM</th><th>CRM</th><th>ARM</th></tr>
  {app_rows}
</table>
</div>

<footer>
数据来源：项目1 本地实验数据库（r21 pilot，completed {completed}，模型 {model}）。本报告为探索性分析，不构成正式 RQ1/RQ2 结论。未导出学生答案、教师反馈、提示词或模型输出 payload。
</footer>

</main>
</body>
</html>
"""

    out = HERE / "r21_pilot_analysis_report.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
