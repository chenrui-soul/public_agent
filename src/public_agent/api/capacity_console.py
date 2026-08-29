# ruff: noqa: E501,RUF001

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>容量治理控制台 · public_agent</title>
  <link rel="stylesheet" href="/console/assets/capacity-governance.css">
</head>
<body>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <header class="topbar">
    <div>
      <p class="eyebrow">PUBLIC_AGENT / OPERATIONS</p>
      <h1>容量治理控制台</h1>
    </div>
    <div class="connection" aria-live="polite">
      <span id="connection-dot" class="dot"></span>
      <span id="connection-label">未连接</span>
    </div>
  </header>

  <main id="main">
    <section id="auth-panel" class="auth-panel" aria-labelledby="auth-title">
      <div>
        <p class="eyebrow">安全会话</p>
        <h2 id="auth-title">使用 Bearer Token 连接</h2>
        <p>Token 只保存在当前标签页的 sessionStorage；关闭标签页后自动清除。</p>
      </div>
      <form id="auth-form" class="auth-form">
        <label for="token">API Token</label>
        <div class="input-row">
          <input id="token" name="token" type="password" autocomplete="off" required>
          <button type="submit" class="primary">连接</button>
        </div>
      </form>
    </section>

    <div id="message" class="message" role="status" aria-live="polite" hidden></div>

    <section class="toolbar" aria-label="治理工具">
      <div>
        <p class="eyebrow">治理租户</p>
        <strong id="handler-version">等待连接</strong>
      </div>
      <div class="toolbar-actions">
        <button id="create-request" type="button">新建变更请求</button>
        <button id="scan-drift" type="button">立即扫描漂移</button>
        <button id="run-drill" type="button">运行只读演练</button>
        <button id="refresh" type="button" class="primary">刷新数据</button>
        <button id="disconnect" type="button" class="ghost">断开</button>
      </div>
    </section>

    <section class="metric-grid" aria-label="治理摘要">
      <article class="metric">
        <span>Active policy</span>
        <strong id="policy-version">—</strong>
        <small id="policy-source">尚无数据</small>
      </article>
      <article class="metric">
        <span>待审批请求</span>
        <strong id="awaiting-count">—</strong>
        <small>awaiting_approval</small>
      </article>
      <article class="metric critical-metric">
        <span>未恢复告警</span>
        <strong id="alert-count">—</strong>
        <small>open + acknowledged</small>
      </article>
      <article class="metric">
        <span>最近告警变化</span>
        <strong id="latest-alert">—</strong>
        <small>本地时间</small>
      </article>
    </section>

    <section class="workspace">
      <article class="panel policy-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">CURRENT POLICY</p>
            <h2>当前策略</h2>
          </div>
          <span id="policy-status" class="badge">unknown</span>
        </div>
        <dl id="thresholds" class="threshold-grid"></dl>
      </article>

      <article class="panel roles-panel">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">RBAC</p>
            <h2>角色模板</h2>
          </div>
        </div>
        <div id="roles" class="role-list"></div>
      </article>
    </section>

    <section class="panel data-panel" aria-labelledby="requests-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">CHANGE REQUESTS</p>
          <h2 id="requests-title">容量变更请求</h2>
        </div>
        <label class="compact-filter">状态
          <select id="request-status">
            <option value="">全部</option>
            <option value="pending_window">pending_window</option>
            <option value="awaiting_approval">awaiting_approval</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="cooling_down">cooling_down</option>
            <option value="effective">effective</option>
            <option value="ineffective">ineffective</option>
            <option value="rolled_back">rolled_back</option>
          </select>
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>状态</th><th>版本</th><th>请求人</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody id="request-rows"></tbody>
        </table>
      </div>
      <p id="requests-empty" class="empty" hidden>当前筛选条件下没有变更请求。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="alerts-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">GOVERNANCE ALERTS</p>
          <h2 id="alerts-title">策略漂移告警</h2>
        </div>
        <label class="compact-filter">状态
          <select id="alert-status">
            <option value="">全部</option>
            <option value="open">open</option>
            <option value="acknowledged">acknowledged</option>
            <option value="resolved">resolved</option>
          </select>
        </label>
      </div>
      <div id="alerts" class="alert-list"></div>
      <p id="alerts-empty" class="empty" hidden>当前筛选条件下没有治理告警。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="incidents-title">
      <div class="panel-heading audit-heading">
        <div>
          <p class="eyebrow">INTERNAL INCIDENT QUEUE</p>
          <h2 id="incidents-title">治理事件队列</h2>
        </div>
        <div class="audit-filters">
          <label class="compact-filter">状态
            <select id="incident-status">
              <option value="">全部</option>
              <option value="open">open</option>
              <option value="acknowledged">acknowledged</option>
              <option value="resolved">resolved</option>
            </select>
          </label>
          <label class="compact-filter">信号
            <select id="incident-signal">
              <option value="">全部</option>
              <option value="audit_failure_spike">audit_failure_spike</option>
              <option value="alert_sla_breached">alert_sla_breached</option>
              <option value="alert_reopen_repeat">alert_reopen_repeat</option>
              <option value="drill_check_failed">drill_check_failed</option>
              <option value="knowledge_unsafe_persistent">knowledge_unsafe_persistent</option>
              <option value="knowledge_degraded_repeat">knowledge_degraded_repeat</option>
              <option value="knowledge_requarantined">knowledge_requarantined</option>
            </select>
          </label>
        </div>
      </div>
      <div id="incidents" class="alert-list"></div>
      <p id="incidents-empty" class="empty" hidden>当前筛选条件下没有治理事件。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="remediations-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">REMEDIATION WORKFLOW</p>
          <h2 id="remediations-title">事件处置审批</h2>
        </div>
        <label class="compact-filter">状态
          <select id="remediation-status">
            <option value="">全部</option>
            <option value="awaiting_approval">awaiting_approval</option>
            <option value="approved">approved</option>
            <option value="verification_pending">verification_pending</option>
            <option value="verified">verified</option>
            <option value="rejected">rejected</option>
            <option value="failed">failed</option>
          </select>
        </label>
      </div>
      <div id="remediations" class="alert-list"></div>
      <p id="remediations-empty" class="empty" hidden>当前筛选条件下没有事件处置单。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="postmortems-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">POSTMORTEM KNOWLEDGE</p>
          <h2 id="postmortems-title">治理复盘与知识发布</h2>
        </div>
        <label class="compact-filter">状态
          <select id="postmortem-status">
            <option value="">全部</option>
            <option value="awaiting_review">awaiting_review</option>
            <option value="published">published</option>
            <option value="quarantined">quarantined</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
      </div>
      <div id="postmortems" class="alert-list"></div>
      <p id="postmortems-empty" class="empty" hidden>当前筛选条件下没有治理复盘。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="knowledge-feedback-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">KNOWLEDGE FEEDBACK</p>
          <h2 id="knowledge-feedback-title">治理知识反馈复核</h2>
        </div>
        <div class="audit-filters">
          <label class="compact-filter">状态
            <select id="knowledge-feedback-status">
              <option value="">全部</option>
              <option value="awaiting_review">awaiting_review</option>
              <option value="confirmed">confirmed</option>
              <option value="dismissed">dismissed</option>
              <option value="superseded">superseded</option>
            </select>
          </label>
          <label class="compact-filter">信号
            <select id="knowledge-feedback-signal">
              <option value="">全部</option>
              <option value="helpful">helpful</option>
              <option value="not_helpful">not_helpful</option>
              <option value="safety_concern">safety_concern</option>
            </select>
          </label>
        </div>
      </div>
      <div id="knowledge-feedback" class="alert-list"></div>
      <p id="knowledge-feedback-empty" class="empty" hidden>当前筛选条件下没有治理知识反馈。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="knowledge-quality-trend-title">
      <div class="panel-heading audit-heading">
        <div>
          <p class="eyebrow">KNOWLEDGE QUALITY TREND</p>
          <h2 id="knowledge-quality-trend-title">治理知识质量趋势</h2>
        </div>
        <div class="audit-filters">
          <label class="compact-filter">粒度
            <select id="knowledge-quality-trend-bucket">
              <option value="hour">hour</option>
              <option value="day">day</option>
            </select>
          </label>
          <label class="compact-filter">评测
            <select id="knowledge-quality-trend-assessment">
              <option value="">全部</option>
              <option value="insufficient">insufficient</option>
              <option value="healthy">healthy</option>
              <option value="degraded">degraded</option>
              <option value="unsafe">unsafe</option>
            </select>
          </label>
          <label class="compact-filter">回看窗口
            <select id="knowledge-quality-trend-lookback">
              <option value="24">24 小时</option>
              <option value="72">72 小时</option>
              <option value="168">7 天</option>
            </select>
          </label>
        </div>
      </div>
      <div id="knowledge-quality-trend" class="table-wrap"></div>
      <p id="knowledge-quality-trend-empty" class="empty" hidden>当前窗口内没有治理知识质量快照。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="knowledge-quality-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">KNOWLEDGE QUALITY</p>
          <h2 id="knowledge-quality-title">治理知识质量快照</h2>
        </div>
        <label class="compact-filter">评测
          <select id="knowledge-quality-assessment">
            <option value="">全部</option>
            <option value="insufficient">insufficient</option>
            <option value="healthy">healthy</option>
            <option value="degraded">degraded</option>
            <option value="unsafe">unsafe</option>
          </select>
        </label>
      </div>
      <div id="knowledge-quality" class="alert-list"></div>
      <p id="knowledge-quality-empty" class="empty" hidden>当前筛选条件下没有治理知识质量快照。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="knowledge-recoveries-title">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">QUARANTINE RECOVERY</p>
          <h2 id="knowledge-recoveries-title">隔离恢复审批</h2>
        </div>
        <label class="compact-filter">状态
          <select id="knowledge-recovery-status">
            <option value="">全部</option>
            <option value="awaiting_review">awaiting_review</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
      </div>
      <div id="knowledge-recoveries" class="alert-list"></div>
      <p id="knowledge-recoveries-empty" class="empty" hidden>当前筛选条件下没有隔离恢复申请。</p>
    </section>

    <section class="panel data-panel" aria-labelledby="audit-title">
      <div class="panel-heading audit-heading">
        <div>
          <p class="eyebrow">APPEND-ONLY AUDIT</p>
          <h2 id="audit-title">治理审计历史</h2>
        </div>
        <div class="audit-filters">
          <label class="compact-filter">Actor subject
            <input id="audit-actor" maxlength="200" autocomplete="off">
          </label>
          <label class="compact-filter">Action
            <input id="audit-action" maxlength="100" autocomplete="off">
          </label>
          <label class="compact-filter">Outcome
            <select id="audit-outcome">
              <option value="">全部</option>
              <option value="success">success</option>
              <option value="denied">denied</option>
              <option value="conflict">conflict</option>
            </select>
          </label>
          <button id="audit-search" type="button">筛选</button>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>Actor</th><th>动作</th><th>结果</th><th>目标</th></tr></thead>
          <tbody id="audit-rows"></tbody>
        </table>
      </div>
      <p id="audit-empty" class="empty" hidden>当前筛选条件下没有审计事件。</p>
      <button id="audit-more" type="button" hidden>加载更多</button>
    </section>
  </main>

  <dialog id="request-dialog">
    <form id="request-form" method="dialog">
      <div class="dialog-heading">
        <div><p class="eyebrow">NEW REQUEST</p><h2>新建容量变更请求</h2></div>
        <button id="close-dialog" type="button" class="icon-button" aria-label="关闭">×</button>
      </div>
      <label>Calibration ID
        <input name="calibration_id" required autocomplete="off">
      </label>
      <label>窗口秒数（留空使用服务端默认）
        <input name="window_required_seconds" type="number" min="60" max="2592000">
      </label>
      <label>最小观测数（留空使用服务端默认）
        <input name="window_minimum_observations" type="number" min="2" max="100000">
      </label>
      <div class="dialog-actions">
        <button type="button" id="cancel-dialog">取消</button>
        <button type="submit" class="primary">创建请求</button>
      </div>
    </form>
  </dialog>

  <dialog id="postmortem-dialog" aria-labelledby="postmortem-dialog-title">
    <form id="postmortem-form" method="dialog">
      <div class="dialog-heading">
        <div><p class="eyebrow">NEW POSTMORTEM</p><h2 id="postmortem-dialog-title">创建治理复盘</h2></div>
        <button id="close-postmortem-dialog" type="button" class="icon-button" aria-label="关闭">×</button>
      </div>
      <p id="postmortem-source" class="dialog-note"></p>
      <div class="postmortem-preview" aria-label="受限分类">
        <div><span>根因</span><strong id="postmortem-root-cause">—</strong></div>
        <div><span>影响</span><strong id="postmortem-impact">—</strong></div>
        <div><span>预防</span><strong id="postmortem-prevention">—</strong></div>
      </div>
      <label for="postmortem-summary">安全摘要（10-1000 字）
        <textarea id="postmortem-summary" name="summary" minlength="10" maxlength="1000" rows="5" required></textarea>
      </label>
      <small class="dialog-note">禁止 Token、连接串、代码块、Shell、SQL 或容器编排命令。</small>
      <div class="dialog-actions">
        <button type="button" id="cancel-postmortem-dialog">取消</button>
        <button type="submit" class="primary">提交独立评审</button>
      </div>
    </form>
  </dialog>

  <dialog id="knowledge-feedback-dialog" aria-labelledby="knowledge-feedback-dialog-title">
    <form id="knowledge-feedback-form" method="dialog">
      <div class="dialog-heading">
        <div><p class="eyebrow">REPORT FEEDBACK</p><h2 id="knowledge-feedback-dialog-title">提交治理知识反馈</h2></div>
        <button id="close-knowledge-feedback-dialog" type="button" class="icon-button" aria-label="关闭">×</button>
      </div>
      <p id="knowledge-feedback-source" class="dialog-note"></p>
      <label for="knowledge-feedback-input-signal">受限信号
        <select id="knowledge-feedback-input-signal" name="signal" required>
          <option value="helpful">helpful</option>
          <option value="not_helpful">not_helpful</option>
          <option value="safety_concern">safety_concern</option>
        </select>
      </label>
      <label for="knowledge-feedback-reason">受限原因
        <select id="knowledge-feedback-reason" name="reason" required>
          <option value="relevance">relevance</option>
          <option value="accuracy">accuracy</option>
          <option value="staleness">staleness</option>
        </select>
      </label>
      <small class="dialog-note">不提交查询、提示词、模型输出或自由文本。安全问题确认后会立即隔离知识。</small>
      <div class="dialog-actions">
        <button type="button" id="cancel-knowledge-feedback-dialog">取消</button>
        <button type="submit" class="primary">提交独立复核</button>
      </div>
    </form>
  </dialog>

  <script src="/console/assets/capacity-governance.js" defer></script>
</body>
</html>
"""

_CSS = """:root{--bg:#f4f1e9;--surface:#fffdf7;--ink:#191914;--muted:#6d6b60;--line:#d9d4c7;--accent:#225c46;--accent-2:#dbeadf;--danger:#a33a2b;--danger-soft:#f3dcd6;--warning:#a56816;--shadow:0 18px 48px rgba(43,39,28,.08);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}*{box-sizing:border-box}body{margin:0;min-width:320px}.skip-link{position:absolute;left:-999px;top:8px;background:#fff;padding:10px;z-index:10}.skip-link:focus{left:8px}.topbar{display:flex;justify-content:space-between;align-items:center;padding:28px clamp(20px,5vw,72px);border-bottom:1px solid var(--line);background:rgba(244,241,233,.96);position:sticky;top:0;z-index:5}.topbar h1{margin:3px 0 0;font:600 clamp(24px,3vw,38px)/1.05 Georgia,serif}.eyebrow{margin:0;color:var(--muted);font-size:11px;letter-spacing:.18em;font-weight:700}.connection{display:flex;gap:9px;align-items:center;font-size:13px}.dot{width:9px;height:9px;border-radius:50%;background:#aaa}.dot.online{background:#2b8a5d;box-shadow:0 0 0 5px #dcece3}main{max-width:1440px;margin:auto;padding:34px clamp(20px,5vw,72px) 80px}.auth-panel,.toolbar,.panel,.metric{border:1px solid var(--line);background:var(--surface)}.auth-panel{display:grid;grid-template-columns:1fr minmax(320px,.8fr);gap:32px;padding:28px;box-shadow:var(--shadow)}.auth-panel h2,.panel h2,dialog h2{font:600 24px/1.1 Georgia,serif;margin:7px 0 10px}.auth-panel p:not(.eyebrow){color:var(--muted);margin:0;max-width:600px}.auth-form label,.compact-filter,dialog label{display:grid;gap:7px;font-size:12px;font-weight:700;color:var(--muted)}.input-row{display:flex;gap:10px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;border:1px solid var(--line);background:#fff;padding:11px 12px;color:var(--ink);border-radius:3px}textarea{resize:vertical;min-height:120px;line-height:1.5}button{border:1px solid var(--line);background:#fffdf7;padding:10px 14px;border-radius:3px;cursor:pointer;font-weight:700}button:hover{border-color:#999488}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #82ab99;outline-offset:2px}.primary{background:var(--accent);border-color:var(--accent);color:#fff}.ghost{background:transparent}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-top:22px;padding:16px 20px}.toolbar-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.message{margin-top:18px;padding:13px 16px;border-left:4px solid var(--accent);background:var(--accent-2)}.message.error{border-color:var(--danger);background:var(--danger-soft)}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}.metric{padding:18px}.metric span,.metric small{display:block;color:var(--muted);font-size:12px}.metric strong{display:block;font:600 30px/1.1 Georgia,serif;margin:10px 0}.critical-metric{border-top:3px solid var(--danger)}.workspace{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr);gap:16px}.panel{padding:22px;box-shadow:var(--shadow)}.panel-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:18px}.badge{font-size:11px;font-weight:800;padding:6px 9px;border-radius:99px;background:#ece8dc}.threshold-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin:0}.threshold-grid div{background:var(--surface);padding:13px}.threshold-grid dt{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.threshold-grid dd{margin:7px 0 0;font-size:18px;font-weight:750}.role-list{display:grid;gap:8px}.role{border-top:1px solid var(--line);padding:11px 0}.role:first-child{border-top:0}.role strong{font-size:13px}.role p{color:var(--muted);font-size:11px;line-height:1.5;margin:5px 0 0;overflow-wrap:anywhere}.data-panel{margin-top:16px}.compact-filter{min-width:190px}.audit-heading{align-items:flex-end}.audit-filters{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}.audit-filters .compact-filter{min-width:160px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:720px}th,td{text-align:left;border-top:1px solid var(--line);padding:13px 10px;font-size:13px}th{color:var(--muted);font-size:11px;letter-spacing:.08em}.status{font-size:11px;font-weight:800;padding:5px 8px;background:#ece8dc;border-radius:99px}.status.critical,.status.open,.status.breached,.status.denied{background:var(--danger-soft);color:var(--danger)}.status.acknowledged,.status.due,.status.conflict{background:#f3e8cf;color:#80500f}.status.resolved,.status.effective,.status.within_sla,.status.success{background:var(--accent-2);color:var(--accent)}.row-actions{display:flex;gap:6px;flex-wrap:wrap}.row-actions button{font-size:11px;padding:7px 9px}.danger{color:var(--danger);border-color:#d4aaa3}.empty{text-align:center;color:var(--muted);padding:26px}.alert-list{display:grid;gap:10px}.alert-card{display:grid;grid-template-columns:8px 1fr auto;gap:14px;border:1px solid var(--line);padding:14px}.alert-stripe{background:var(--warning)}.alert-card.critical .alert-stripe,.alert-card.breached .alert-stripe{background:var(--danger)}.alert-card h3{margin:0 0 6px;font-size:15px}.alert-meta{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px}.fingerprint{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.alert-actions{display:flex;align-items:center}dialog{border:1px solid var(--line);padding:0;max-width:560px;width:calc(100% - 32px);background:var(--surface);box-shadow:0 30px 90px rgba(30,28,20,.28)}dialog::backdrop{background:rgba(26,25,20,.48)}dialog form{display:grid;gap:16px;padding:24px}.dialog-heading,.dialog-actions{display:flex;justify-content:space-between;align-items:center;gap:12px}.dialog-actions{justify-content:flex-end}.dialog-note{margin:0;color:var(--muted);font-size:12px;line-height:1.5}.postmortem-preview{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}.postmortem-preview div{display:grid;gap:6px;background:var(--surface);padding:10px;min-width:0}.postmortem-preview span{font-size:10px;color:var(--muted);text-transform:uppercase}.postmortem-preview strong{font-size:11px;overflow-wrap:anywhere}.icon-button{font-size:24px;line-height:1;padding:4px 9px}button[disabled]{opacity:.5;cursor:wait}@media(max-width:900px){.auth-panel,.workspace{grid-template-columns:1fr}.metric-grid{grid-template-columns:repeat(2,1fr)}.toolbar{align-items:flex-start;flex-direction:column}.toolbar-actions{justify-content:flex-start}.audit-heading{align-items:stretch;flex-direction:column}}@media(max-width:560px){.topbar{position:static}.connection{display:none}.input-row{display:grid}.metric-grid{grid-template-columns:1fr}.threshold-grid,.postmortem-preview{grid-template-columns:1fr}.panel-heading{align-items:stretch;flex-direction:column}.compact-filter,.audit-filters .compact-filter{min-width:0;width:100%}.alert-card{grid-template-columns:6px 1fr}.alert-actions{grid-column:2}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}"""

_CSS = _CSS.replace("body{margin:0;min-width:320px}", "body{margin:0;min-width:0}")
_CSS += ".alert-card>div:nth-child(2){min-width:0}.alert-meta span{overflow-wrap:anywhere}.alert-actions{flex-wrap:wrap;gap:6px}.status.unsafe{background:var(--danger-soft);color:var(--danger)}.status.degraded,.status.insufficient{background:#f3e8cf;color:#80500f}.status.healthy,.status.approved{background:var(--accent-2);color:var(--accent)}.trend-table{min-width:760px}.trend-table td:first-child{font-weight:700;white-space:nowrap}.trend-table .unsafe-count{color:var(--danger);font-weight:800}.trend-table .degraded-count{color:var(--warning);font-weight:800}"

_JS = """'use strict';
const API='/v1/operations/capacity-governance';
const TOKEN_KEY='public_agent.capacity_governance.token';
const $=(id)=>document.getElementById(id);
const state={token:sessionStorage.getItem(TOKEN_KEY)||'',busy:false,auditCursor:null,postmortemRemediation:null,feedbackPostmortem:null};

function setText(id,value){$(id).textContent=value==null?'—':String(value)}
function setMessage(message,isError=false){const box=$('message');box.hidden=!message;box.classList.toggle('error',isError);box.textContent=message||''}
function setConnected(connected){$('connection-dot').classList.toggle('online',connected);setText('connection-label',connected?'已连接':'未连接');$('auth-panel').hidden=connected}
function formatDate(value){return value?new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'medium'}).format(new Date(value)):'—'}
function shortHash(value){return value?value.slice(0,10)+'…'+value.slice(-6):'—'}
function clear(node){node.replaceChildren()}
function el(tag,text,className){const node=document.createElement(tag);if(text!=null)node.textContent=String(text);if(className)node.className=className;return node}
function button(label,action,danger=false){const node=el('button',label,danger?'danger':'');node.type='button';node.addEventListener('click',action);return node}

async function api(path,options={}){if(!state.token)throw new Error('请先输入 API Token。');const headers=new Headers(options.headers||{});headers.set('Authorization','Bearer '+state.token);if(options.body)headers.set('Content-Type','application/json');const response=await fetch(API+path,{...options,headers,cache:'no-store'});let payload={};try{payload=await response.json()}catch(_error){payload={}}if(!response.ok){const code=payload.error?.code||('http_'+response.status);const error=new Error(payload.error?.message||'请求失败');error.code=code;error.status=response.status;throw error}return payload}
function errorMessage(error){if(error.status===401)return '认证失败或 Token 已失效，请重新连接。';if(error.status===403)return '权限不足：当前 Principal 没有执行此操作所需的 RBAC 权限。';if(error.status===409)return '状态冲突：数据可能已被其他操作人更新，请刷新后重试。';return error.message||'操作失败。'}
async function guarded(task,success){if(state.busy)return;state.busy=true;document.querySelectorAll('button').forEach((node)=>{node.disabled=true});setMessage('处理中…');try{await task();setMessage(success||'操作完成。');setConnected(true)}catch(error){setMessage(errorMessage(error),true);if(error.status===401){sessionStorage.removeItem(TOKEN_KEY);state.token='';setConnected(false)}}finally{state.busy=false;document.querySelectorAll('button').forEach((node)=>{node.disabled=false})}}

function renderSummary(summary){setText('handler-version',summary.handler_version);const policy=summary.active_policy;setText('policy-version',policy?'v'+policy.policy_version:'fallback');setText('policy-source',policy?policy.source_type:'Settings fallback');setText('policy-status',policy?.status||'fallback');setText('awaiting-count',summary.request_counts.awaiting_approval||0);setText('alert-count',(summary.alert_counts.open||0)+(summary.alert_counts.acknowledged||0));setText('latest-alert',formatDate(summary.latest_alert_at));const grid=$('thresholds');clear(grid);const values=policy?.thresholds||{};if(!Object.keys(values).length){grid.append(el('div','当前没有 active policy；运行时使用 Settings fallback。'));return}Object.entries(values).forEach(([key,value])=>{const wrap=document.createElement('div');wrap.append(el('dt',key));wrap.append(el('dd',value));grid.append(wrap)})}
function renderRoles(roles){const root=$('roles');clear(root);roles.forEach((role)=>{const item=el('div',null,'role');item.append(el('strong',role.name));item.append(el('p',role.permissions.join(' · ')));root.append(item)})}
function actionForRequest(record,label,path,danger=false,extraBody={}){return button(label,()=>guarded(async()=>{if(danger&&!window.confirm('这是高风险治理动作。确认继续？'))return;let body={expected_version:record.version,...extraBody};if(path==='rollback'){const reason=window.prompt('请输入回滚原因（必填）');if(!reason)return;body.reason=reason}await api('/requests/'+record.id+'/'+path,{method:'POST',body:JSON.stringify(body)});await loadAll()},label+'完成。'),danger)}
function renderRequests(page){const body=$('request-rows');clear(body);$('requests-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const row=document.createElement('tr');const statusCell=document.createElement('td');statusCell.append(el('span',record.status,'status '+record.status));row.append(statusCell);row.append(el('td','v'+record.version));row.append(el('td',record.requested_by));row.append(el('td',formatDate(record.updated_at)));const actions=el('td',null,'row-actions');if(record.status==='pending_window')actions.append(actionForRequest(record,'验证窗口','validate'));if(record.status==='awaiting_approval'){actions.append(actionForRequest(record,'批准','approve'));actions.append(actionForRequest(record,'拒绝','reject',true))}if(record.status==='approved')actions.append(actionForRequest(record,'发布','publish',true));if(record.status==='cooling_down'){actions.append(actionForRequest(record,'效果复核','review'));actions.append(actionForRequest(record,'回滚','rollback',true))}if(record.status==='ineffective')actions.append(actionForRequest(record,'回滚','rollback',true));if(!actions.childNodes.length)actions.append(el('span','无可用动作','empty-action'));row.append(actions);body.append(row)})}
function renderAlerts(page){const root=$('alerts');clear(root);$('alerts-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.severity+' '+record.sla.state);card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3','策略漂移 · '+record.severity));const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','SLA '+record.sla.state,'status '+record.sla.state));meta.append(el('span','响应截止 '+formatDate(record.sla.response_due_at)));meta.append(el('span','样本 '+record.sample_count));meta.append(el('span','观测 '+formatDate(record.last_observation_at)));meta.append(el('span','期望 '+shortHash(record.expected_fingerprint),'fingerprint'));meta.append(el('span','实际 '+shortHash(record.observed_fingerprint),'fingerprint'));if(record.reopened_count)meta.append(el('span','复发 '+record.reopened_count+' 次'));content.append(meta);card.append(content);const actions=el('div',null,'alert-actions');if(record.status==='open')actions.append(button('确认告警',()=>guarded(async()=>{await api('/alerts/'+record.id+'/acknowledge',{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},'告警已确认。')));card.append(actions);root.append(card)})}
function incidentPath(){const params=new URLSearchParams({limit:'50'});const status=$('incident-status').value;const signal=$('incident-signal').value;if(status)params.set('status',status);if(signal)params.set('signal',signal);return '/incidents?'+params.toString()}
const remediationPlaybooks={audit_failure_spike:'audit_failure_containment',alert_sla_breached:'alert_sla_recovery',alert_reopen_repeat:'alert_reopen_stabilization',drill_check_failed:'drill_control_repair',knowledge_unsafe_persistent:'knowledge_safety_containment',knowledge_degraded_repeat:'knowledge_quality_review',knowledge_requarantined:'knowledge_recurrence_review'};
const remediationEvidence={audit_failure_containment:'containment_applied',alert_sla_recovery:'monitoring_extended',alert_reopen_stabilization:'configuration_reviewed',drill_control_repair:'schema_control_restored',knowledge_safety_containment:'knowledge_quarantine_reviewed',knowledge_quality_review:'quality_evidence_reviewed',knowledge_recurrence_review:'restoration_history_reviewed'};
const postmortemDefaults={audit_failure_containment:{root_cause:'authorization_control_gap',impact:'no_external_impact',prevention:'access_review'},alert_sla_recovery:{root_cause:'observability_gap',impact:'governance_delay',prevention:'monitoring_expansion'},alert_reopen_stabilization:{root_cause:'policy_drift',impact:'repeated_alerting',prevention:'policy_validation'},drill_control_repair:{root_cause:'schema_control_gap',impact:'no_external_impact',prevention:'schema_verification'},knowledge_safety_containment:{root_cause:'operational_process_gap',impact:'control_degradation',prevention:'process_hardening'},knowledge_quality_review:{root_cause:'observability_gap',impact:'control_degradation',prevention:'monitoring_expansion'},knowledge_recurrence_review:{root_cause:'operational_process_gap',impact:'repeated_alerting',prevention:'process_hardening'}};
function renderIncidents(page){const root=$('incidents');clear(root);$('incidents-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.severity+' '+record.status);card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3',record.signal+' · '+record.severity));const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','规则 '+record.rule_version));meta.append(el('span','首次 '+formatDate(record.first_seen_at)));meta.append(el('span','证据 '+formatDate(record.last_evidence_at)));meta.append(el('span','命中 '+record.occurrence_count+' 次'));meta.append(el('span','指纹 '+shortHash(record.fingerprint),'fingerprint'));if(record.source_id)meta.append(el('span','来源 '+record.source_id,'fingerprint'));if(record.reopened_count)meta.append(el('span','复发 '+record.reopened_count+' 次'));content.append(meta);card.append(content);const actions=el('div',null,'alert-actions');if(record.status==='open')actions.append(button('确认事件',()=>guarded(async()=>{await api('/incidents/'+record.id+'/acknowledge',{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},'治理事件已确认。')));if(record.status==='acknowledged')actions.append(button('创建处置单',()=>guarded(async()=>{await api('/incidents/'+record.id+'/remediations',{method:'POST',body:JSON.stringify({expected_incident_version:record.version,playbook:remediationPlaybooks[record.signal]})});await loadAll()},'事件处置单已提交审批。')));card.append(actions);root.append(card)})}
function remediationPath(){const params=new URLSearchParams({limit:'50'});const status=$('remediation-status').value;if(status)params.set('status',status);return '/remediations?'+params.toString()}
function remediationAction(record,label,path,danger=false){return button(label,()=>guarded(async()=>{if(danger&&!window.confirm('这是治理处置动作。确认继续？'))return;await api('/remediations/'+record.id+'/'+path,{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},label+'完成。'),danger)}
function requestPostmortem(record){return button('创建复盘',()=>{const defaults=postmortemDefaults[record.playbook];state.postmortemRemediation=record;setText('postmortem-source','处置 '+record.id+' · '+record.playbook+' · version '+record.version);setText('postmortem-root-cause',defaults.root_cause);setText('postmortem-impact',defaults.impact);setText('postmortem-prevention',defaults.prevention);$('postmortem-summary').value='';postmortemDialog.showModal();$('postmortem-summary').focus()})}
function renderRemediations(page){
  const root=$('remediations');clear(root);$('remediations-empty').hidden=page.items.length!==0;
  page.items.forEach((record)=>{
    const card=el('article',null,'alert-card '+record.status);card.append(el('div',null,'alert-stripe'));
    const content=document.createElement('div');content.append(el('h3',record.playbook+' · cycle '+record.incident_cycle));
    const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','事件 '+record.incident_id,'fingerprint'));meta.append(el('span','请求人 '+record.requested_by));meta.append(el('span','请求 '+formatDate(record.requested_at)));if(record.executed_by)meta.append(el('span','执行人 '+record.executed_by));if(record.execution_evidence)meta.append(el('span','证据 '+record.execution_evidence));if(record.verified_by)meta.append(el('span','验证人 '+record.verified_by));content.append(meta);card.append(content);
    const actions=el('div',null,'alert-actions');
    if(record.status==='awaiting_approval'){actions.append(remediationAction(record,'批准','approve'));actions.append(remediationAction(record,'拒绝','reject',true))}
    if(record.status==='approved'){
      actions.append(button('记录完成',()=>guarded(async()=>{await api('/remediations/'+record.id+'/execution',{method:'POST',body:JSON.stringify({expected_version:record.version,result:'completed',evidence:remediationEvidence[record.playbook]})});await loadAll()},'执行证据已记录。')));
      actions.append(button('记录失败',()=>guarded(async()=>{await api('/remediations/'+record.id+'/execution',{method:'POST',body:JSON.stringify({expected_version:record.version,result:'failed',evidence:remediationEvidence[record.playbook]})});await loadAll()},'执行失败已记录。'),true));
    }
    if(record.status==='verification_pending')actions.append(remediationAction(record,'验证恢复','verify'));
    if(record.status==='verified')actions.append(requestPostmortem(record));
    card.append(actions);root.append(card);
  })
}
function postmortemPath(){const params=new URLSearchParams({limit:'50'});const status=$('postmortem-status').value;if(status)params.set('status',status);return '/postmortems?'+params.toString()}
function postmortemAction(record,label,path,danger=false){return button(label,()=>guarded(async()=>{if(danger&&!window.confirm('确认拒绝这份治理复盘？'))return;await api('/postmortems/'+record.id+'/'+path,{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},label+'完成。'),danger)}
function requestKnowledgeFeedback(record){return button('提交质量反馈',()=>{state.feedbackPostmortem=record;setText('knowledge-feedback-source','复盘 '+record.id+' · postmortem v'+record.version+' · knowledge '+record.knowledge_version);$('knowledge-feedback-input-signal').value='helpful';syncFeedbackReasons();knowledgeFeedbackDialog.showModal();$('knowledge-feedback-input-signal').focus()})}
function captureKnowledgeQuality(record){return button('生成质量快照',()=>guarded(async()=>{await api('/postmortems/'+record.id+'/quality-snapshots',{method:'POST',body:JSON.stringify({expected_postmortem_version:record.version})});await loadAll()},'治理知识质量快照已生成。'))}
function renderPostmortems(page){const root=$('postmortems');clear(root);$('postmortems-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.status);card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3',record.root_cause+' · '+record.impact));const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','版本 '+record.version));meta.append(el('span','预防 '+record.prevention));meta.append(el('span','请求人 '+record.requested_by));meta.append(el('span','事件版本 '+record.incident_version));meta.append(el('span','处置版本 '+record.remediation_version));meta.append(el('span','指纹 '+shortHash(record.content_fingerprint),'fingerprint'));if(record.reviewed_by)meta.append(el('span','评审人 '+record.reviewed_by));if(record.knowledge_namespace)meta.append(el('span','知识域 '+record.knowledge_namespace,'fingerprint'));if(record.knowledge_version)meta.append(el('span','知识版本 '+record.knowledge_version,'fingerprint'));if(record.last_quarantined_at)meta.append(el('span','隔离 '+formatDate(record.last_quarantined_at)));if(record.restore_count)meta.append(el('span','恢复 '+record.restore_count+' 次'));content.append(meta);content.append(el('p',record.summary));card.append(content);const actions=el('div',null,'alert-actions');if(record.status==='awaiting_review'){actions.append(postmortemAction(record,'批准并发布','approve'));actions.append(postmortemAction(record,'拒绝','reject',true))}if(record.status==='published')actions.append(requestKnowledgeFeedback(record));if(record.status==='quarantined')actions.append(captureKnowledgeQuality(record));card.append(actions);root.append(card)})}
function knowledgeFeedbackPath(){const params=new URLSearchParams({limit:'50'});const status=$('knowledge-feedback-status').value;const signal=$('knowledge-feedback-signal').value;if(status)params.set('status',status);if(signal)params.set('signal',signal);return '/knowledge-feedback?'+params.toString()}
function knowledgeFeedbackAction(record,label,path,danger=false){return button(label,()=>guarded(async()=>{if(danger&&!window.confirm('确认安全反馈并立即隔离这份治理知识？'))return;await api('/knowledge-feedback/'+record.id+'/'+path,{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},label+'完成。'),danger)}
function renderKnowledgeFeedback(page){const root=$('knowledge-feedback');clear(root);$('knowledge-feedback-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.status+(record.signal==='safety_concern'?' critical':''));card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3',record.signal+' · '+record.reason));const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','报告人 '+record.reported_by));meta.append(el('span','报告 '+formatDate(record.reported_at)));meta.append(el('span','复盘 '+record.postmortem_id,'fingerprint'));meta.append(el('span','复盘版本 '+record.postmortem_version));meta.append(el('span','知识版本 '+record.knowledge_version,'fingerprint'));meta.append(el('span','指纹 '+shortHash(record.content_fingerprint),'fingerprint'));if(record.reviewed_by)meta.append(el('span','复核人 '+record.reviewed_by));content.append(meta);card.append(content);const actions=el('div',null,'alert-actions');if(record.status==='awaiting_review'){actions.append(knowledgeFeedbackAction(record,'确认','confirm',record.signal==='safety_concern'));actions.append(knowledgeFeedbackAction(record,'驳回','dismiss'))}card.append(actions);root.append(card)})}
function knowledgeQualityTrendPath(){const bucket=$('knowledge-quality-trend-bucket').value;const assessment=$('knowledge-quality-trend-assessment').value;const hours=Number($('knowledge-quality-trend-lookback').value)||24;const capturedTo=new Date();const capturedFrom=new Date(capturedTo.getTime()-(hours*60*60*1000));const limit=bucket==='hour'?hours+1:Math.ceil(hours/24)+1;const params=new URLSearchParams({captured_from:capturedFrom.toISOString(),captured_to:capturedTo.toISOString(),bucket,limit:String(limit)});if(assessment)params.set('assessment',assessment);return '/knowledge-quality-trend?'+params.toString()}
function renderKnowledgeQualityTrend(report){const root=$('knowledge-quality-trend');clear(root);const points=Array.isArray(report.points)?report.points:[];const hasData=points.some((point)=>point.total_snapshots>0);$('knowledge-quality-trend-empty').hidden=hasData;if(!hasData)return;const table=el('table',null,'trend-table');const head=document.createElement('thead');const headerRow=document.createElement('tr');['时间桶','总快照','unsafe','degraded','healthy','insufficient','独立复盘'].forEach((label)=>{const cell=el('th',label);cell.scope='col';headerRow.append(cell)});head.append(headerRow);table.append(head);const body=document.createElement('tbody');points.forEach((point)=>{const row=document.createElement('tr');row.append(el('td',formatDate(point.bucket_started_at)));row.append(el('td',point.total_snapshots));row.append(el('td',point.unsafe_count,'unsafe-count'));row.append(el('td',point.degraded_count,'degraded-count'));row.append(el('td',point.healthy_count));row.append(el('td',point.insufficient_count));row.append(el('td',point.distinct_postmortems));body.append(row)});table.append(body);root.append(table)}
function knowledgeQualityPath(){const params=new URLSearchParams({limit:'50'});const assessment=$('knowledge-quality-assessment').value;if(assessment)params.set('assessment',assessment);return '/knowledge-quality-snapshots?'+params.toString()}
function requestKnowledgeRecovery(record){return button('申请恢复',()=>guarded(async()=>{await api('/postmortems/'+record.postmortem_id+'/recoveries',{method:'POST',body:JSON.stringify({expected_postmortem_version:record.postmortem_version,snapshot_id:record.id,reason:'false_positive'})});await loadAll()},'隔离恢复申请已提交独立审批。'))}
function renderKnowledgeQuality(page){const root=$('knowledge-quality');clear(root);$('knowledge-quality-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.assessment+(record.assessment==='unsafe'?' critical':''));card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3',record.assessment+' · feedback '+record.total_feedback));const meta=el('div',null,'alert-meta');meta.append(el('span','评测 '+record.assessment,'status '+record.assessment));meta.append(el('span','复盘 '+record.postmortem_id,'fingerprint'));meta.append(el('span','复盘版本 '+record.postmortem_version));meta.append(el('span','知识版本 '+record.knowledge_version,'fingerprint'));meta.append(el('span','安全 '+record.confirmed_safety_count));meta.append(el('span','正向 '+record.confirmed_helpful_count));meta.append(el('span','负向 '+record.confirmed_not_helpful_count));meta.append(el('span','待复核 '+record.awaiting_review_count));meta.append(el('span','证据 '+shortHash(record.evidence_fingerprint),'fingerprint'));meta.append(el('span','生成 '+formatDate(record.captured_at)));content.append(meta);card.append(content);const actions=el('div',null,'alert-actions');if(record.assessment==='unsafe')actions.append(requestKnowledgeRecovery(record));card.append(actions);root.append(card)})}
function knowledgeRecoveryPath(){const params=new URLSearchParams({limit:'50'});const status=$('knowledge-recovery-status').value;if(status)params.set('status',status);return '/knowledge-recoveries?'+params.toString()}
function knowledgeRecoveryAction(record,label,path,danger=false){return button(label,()=>guarded(async()=>{if(danger&&!window.confirm('批准后将生成新的知识版本并重新进入 RAG。确认继续？'))return;await api('/knowledge-recoveries/'+record.id+'/'+path,{method:'POST',body:JSON.stringify({expected_version:record.version})});await loadAll()},label+'完成。'),danger)}
function renderKnowledgeRecoveries(page){const root=$('knowledge-recoveries');clear(root);$('knowledge-recoveries-empty').hidden=page.items.length!==0;page.items.forEach((record)=>{const card=el('article',null,'alert-card '+record.status);card.append(el('div',null,'alert-stripe'));const content=document.createElement('div');content.append(el('h3',record.reason+' · '+record.status));const meta=el('div',null,'alert-meta');meta.append(el('span','状态 '+record.status,'status '+record.status));meta.append(el('span','复盘 '+record.postmortem_id,'fingerprint'));meta.append(el('span','快照 '+record.snapshot_id,'fingerprint'));meta.append(el('span','复盘版本 '+record.postmortem_version));meta.append(el('span','知识版本 '+record.knowledge_version,'fingerprint'));meta.append(el('span','请求人 '+record.requested_by));meta.append(el('span','请求 '+formatDate(record.requested_at)));if(record.reviewed_by)meta.append(el('span','审批人 '+record.reviewed_by));if(record.restored_knowledge_version)meta.append(el('span','恢复版本 '+record.restored_knowledge_version,'fingerprint'));content.append(meta);card.append(content);const actions=el('div',null,'alert-actions');if(record.status==='awaiting_review'){actions.append(knowledgeRecoveryAction(record,'批准恢复','approve',true));actions.append(knowledgeRecoveryAction(record,'拒绝','reject'))}card.append(actions);root.append(card)})}
function auditPath(cursor=null){const params=new URLSearchParams({limit:'50'});const actor=$('audit-actor').value.trim();const action=$('audit-action').value.trim();const outcome=$('audit-outcome').value;if(actor)params.set('actor_subject',actor);if(action)params.set('action',action);if(outcome)params.set('outcome',outcome);if(cursor)params.set('cursor',cursor);return '/audit-events?'+params.toString()}
function renderAudit(page,append=false){const body=$('audit-rows');if(!append)clear(body);page.items.forEach((record)=>{const row=document.createElement('tr');row.append(el('td',formatDate(record.created_at)));row.append(el('td',record.actor_subject||'系统/已删除 Principal'));row.append(el('td',record.action));const outcome=document.createElement('td');outcome.append(el('span',record.outcome,'status '+record.outcome));row.append(outcome);row.append(el('td',record.request_id||record.alert_id||record.incident_id||record.postmortem_id||'—','fingerprint'));body.append(row)});state.auditCursor=page.next_cursor;$('audit-more').hidden=!page.next_cursor;$('audit-empty').hidden=body.childNodes.length!==0}
async function loadAudit(){const page=await api(auditPath());renderAudit(page)}
function unavailable(kind){if(kind==='summary'){setText('handler-version','权限受限');setText('policy-version','—');setText('policy-source','无容量读取权限');setText('policy-status','restricted');setText('awaiting-count','—');setText('alert-count','—');setText('latest-alert','—');clear($('thresholds'));$('thresholds').append(el('div','当前 Principal 无容量摘要读取权限。'));return}if(kind==='roles'){clear($('roles'));$('roles').append(el('p','当前 Principal 无角色模板读取权限。'));return}if(kind==='requests'){clear($('request-rows'));$('requests-empty').textContent='当前 Principal 无变更请求读取权限。';$('requests-empty').hidden=false;return}if(kind==='alerts'){clear($('alerts'));$('alerts-empty').textContent='当前 Principal 无告警读取权限。';$('alerts-empty').hidden=false;return}if(kind==='incidents'){clear($('incidents'));$('incidents-empty').textContent='当前 Principal 无治理事件读取权限。';$('incidents-empty').hidden=false;return}if(kind==='remediations'){clear($('remediations'));$('remediations-empty').textContent='当前 Principal 无事件处置读取权限。';$('remediations-empty').hidden=false;return}if(kind==='postmortems'){clear($('postmortems'));$('postmortems-empty').textContent='当前 Principal 无治理复盘读取权限。';$('postmortems-empty').hidden=false;return}if(kind==='knowledgeFeedback'){clear($('knowledge-feedback'));$('knowledge-feedback-empty').textContent='当前 Principal 无治理知识反馈读取权限。';$('knowledge-feedback-empty').hidden=false;return}if(kind==='knowledgeQualityTrend'){clear($('knowledge-quality-trend'));$('knowledge-quality-trend-empty').textContent='当前 Principal 无治理知识质量趋势读取权限。';$('knowledge-quality-trend-empty').hidden=false;return}if(kind==='knowledgeQuality'){clear($('knowledge-quality'));$('knowledge-quality-empty').textContent='当前 Principal 无治理知识质量读取权限。';$('knowledge-quality-empty').hidden=false;return}if(kind==='knowledgeRecoveries'){clear($('knowledge-recoveries'));$('knowledge-recoveries-empty').textContent='当前 Principal 无隔离恢复读取权限。';$('knowledge-recoveries-empty').hidden=false;return}clear($('audit-rows'));state.auditCursor=null;$('audit-more').hidden=true;$('audit-empty').textContent='当前 Principal 无治理审计读取权限。';$('audit-empty').hidden=false}
function resetViews(){['summary','roles','requests','alerts','incidents','remediations','postmortems','knowledgeFeedback','knowledgeQualityTrend','knowledgeQuality','knowledgeRecoveries','audit'].forEach(unavailable)}
async function loadAll(){const requestStatus=$('request-status').value;const alertStatus=$('alert-status').value;const tasks=[{kind:'summary',promise:api('/summary'),render:renderSummary},{kind:'roles',promise:api('/roles'),render:renderRoles},{kind:'requests',promise:api('/requests?limit=50'+(requestStatus?'&status='+encodeURIComponent(requestStatus):'')),render:renderRequests},{kind:'alerts',promise:api('/alerts?limit=50'+(alertStatus?'&status='+encodeURIComponent(alertStatus):'')),render:renderAlerts},{kind:'incidents',promise:api(incidentPath()),render:renderIncidents},{kind:'remediations',promise:api(remediationPath()),render:renderRemediations},{kind:'postmortems',promise:api(postmortemPath()),render:renderPostmortems},{kind:'knowledgeFeedback',promise:api(knowledgeFeedbackPath()),render:renderKnowledgeFeedback},{kind:'knowledgeQualityTrend',promise:api(knowledgeQualityTrendPath()),render:renderKnowledgeQualityTrend},{kind:'knowledgeQuality',promise:api(knowledgeQualityPath()),render:renderKnowledgeQuality},{kind:'knowledgeRecoveries',promise:api(knowledgeRecoveryPath()),render:renderKnowledgeRecoveries},{kind:'audit',promise:api(auditPath()),render:renderAudit}];const results=await Promise.allSettled(tasks.map((item)=>item.promise));let loaded=0;let firstError=null;results.forEach((result,index)=>{const item=tasks[index];if(result.status==='fulfilled'){item.render(result.value);loaded+=1;return}if(result.reason?.status===403){unavailable(item.kind);return}if(!firstError)firstError=result.reason});if(firstError)throw firstError;if(!loaded){const error=new Error('当前 Principal 没有任何容量治理控制台权限。');error.status=403;throw error}}

$('auth-form').addEventListener('submit',(event)=>{event.preventDefault();const token=$('token').value.trim();if(!token)return;state.token=token;sessionStorage.setItem(TOKEN_KEY,token);$('token').value='';guarded(loadAll,'连接成功。')});
$('refresh').addEventListener('click',()=>guarded(loadAll,'数据已刷新。'));
$('disconnect').addEventListener('click',()=>{sessionStorage.removeItem(TOKEN_KEY);state.token='';state.postmortemRemediation=null;state.feedbackPostmortem=null;if(postmortemDialog.open)postmortemDialog.close();if(knowledgeFeedbackDialog.open)knowledgeFeedbackDialog.close();resetViews();setConnected(false);setMessage('已断开当前会话。')});
$('request-status').addEventListener('change',()=>guarded(loadAll));
$('alert-status').addEventListener('change',()=>guarded(loadAll));
$('incident-status').addEventListener('change',()=>guarded(loadAll));
$('incident-signal').addEventListener('change',()=>guarded(loadAll));
$('remediation-status').addEventListener('change',()=>guarded(loadAll));
$('postmortem-status').addEventListener('change',()=>guarded(loadAll));
$('knowledge-feedback-status').addEventListener('change',()=>guarded(loadAll));
$('knowledge-feedback-signal').addEventListener('change',()=>guarded(loadAll));
$('knowledge-quality-trend-bucket').addEventListener('change',()=>guarded(loadAll));
$('knowledge-quality-trend-assessment').addEventListener('change',()=>guarded(loadAll));
$('knowledge-quality-trend-lookback').addEventListener('change',()=>guarded(loadAll));
$('knowledge-quality-assessment').addEventListener('change',()=>guarded(loadAll));
$('knowledge-recovery-status').addEventListener('change',()=>guarded(loadAll));
$('audit-search').addEventListener('click',()=>guarded(loadAudit,'审计筛选已更新。'));
$('audit-more').addEventListener('click',()=>guarded(async()=>{if(!state.auditCursor)return;const page=await api(auditPath(state.auditCursor));renderAudit(page,true)},'已加载更多审计事件。'));
$('scan-drift').addEventListener('click',()=>guarded(async()=>{await api('/drift/scan',{method:'POST'});await loadAll()},'漂移扫描完成。'));
$('run-drill').addEventListener('click',()=>guarded(async()=>{const report=await api('/drill-report');const failed=report.checks.filter((item)=>!item.passed).map((item)=>item.name);if(failed.length)throw new Error('治理演练未通过：'+failed.join('、'))},'治理演练全部通过，未修改业务数据。'));
const dialog=$('request-dialog');$('create-request').addEventListener('click',()=>dialog.showModal());$('close-dialog').addEventListener('click',()=>dialog.close());$('cancel-dialog').addEventListener('click',()=>dialog.close());
$('request-form').addEventListener('submit',(event)=>{event.preventDefault();const form=event.currentTarget;const data=new FormData(form);const body={calibration_id:String(data.get('calibration_id')||'').trim()};const seconds=String(data.get('window_required_seconds')||'').trim();const minimum=String(data.get('window_minimum_observations')||'').trim();if(seconds)body.window_required_seconds=Number(seconds);if(minimum)body.window_minimum_observations=Number(minimum);dialog.close();guarded(async()=>{await api('/requests',{method:'POST',body:JSON.stringify(body)});form.reset();await loadAll()},'变更请求已创建。')});
const postmortemDialog=$('postmortem-dialog');
function closePostmortemDialog(){state.postmortemRemediation=null;postmortemDialog.close()}
$('close-postmortem-dialog').addEventListener('click',closePostmortemDialog);$('cancel-postmortem-dialog').addEventListener('click',closePostmortemDialog);
$('postmortem-form').addEventListener('submit',(event)=>{event.preventDefault();const form=event.currentTarget;const record=state.postmortemRemediation;if(!record){postmortemDialog.close();setMessage('复盘来源已失效，请刷新后重试。',true);return}const summary=String(new FormData(form).get('summary')||'').trim();const defaults=postmortemDefaults[record.playbook];postmortemDialog.close();state.postmortemRemediation=null;guarded(async()=>{await api('/remediations/'+record.id+'/postmortems',{method:'POST',body:JSON.stringify({expected_remediation_version:record.version,...defaults,summary})});form.reset();await loadAll()},'治理复盘已提交独立评审。')});
const knowledgeFeedbackDialog=$('knowledge-feedback-dialog');
function syncFeedbackReasons(){const signal=$('knowledge-feedback-input-signal').value;const reason=$('knowledge-feedback-reason');clear(reason);const values=signal==='safety_concern'?['unsafe_content']:['relevance','accuracy','staleness'];values.forEach((value)=>{const option=el('option',value);option.value=value;reason.append(option)})}
function closeKnowledgeFeedbackDialog(){state.feedbackPostmortem=null;knowledgeFeedbackDialog.close()}
$('knowledge-feedback-input-signal').addEventListener('change',syncFeedbackReasons);$('close-knowledge-feedback-dialog').addEventListener('click',closeKnowledgeFeedbackDialog);$('cancel-knowledge-feedback-dialog').addEventListener('click',closeKnowledgeFeedbackDialog);
$('knowledge-feedback-form').addEventListener('submit',(event)=>{event.preventDefault();const form=event.currentTarget;const record=state.feedbackPostmortem;if(!record){knowledgeFeedbackDialog.close();setMessage('反馈来源已失效，请刷新后重试。',true);return}const data=new FormData(form);const body={expected_postmortem_version:record.version,expected_knowledge_version:record.knowledge_version,expected_content_fingerprint:record.content_fingerprint,signal:String(data.get('signal')),reason:String(data.get('reason'))};knowledgeFeedbackDialog.close();state.feedbackPostmortem=null;guarded(async()=>{await api('/postmortems/'+record.id+'/feedback',{method:'POST',body:JSON.stringify(body)});form.reset();syncFeedbackReasons();await loadAll()},'治理知识反馈已提交独立复核。')});
setConnected(Boolean(state.token));if(state.token)guarded(loadAll);else setMessage('输入 Bearer Token 后加载治理数据。');
"""


def install_capacity_governance_console(app: FastAPI) -> None:
    @app.get("/console/capacity-governance", response_class=HTMLResponse)
    async def capacity_governance_console() -> HTMLResponse:
        return HTMLResponse(_HTML, headers=_SECURITY_HEADERS)

    @app.get("/console/assets/capacity-governance.css", response_class=Response)
    async def capacity_governance_css() -> Response:
        return Response(_CSS, media_type="text/css", headers=_SECURITY_HEADERS)

    @app.get("/console/assets/capacity-governance.js", response_class=Response)
    async def capacity_governance_js() -> Response:
        return Response(
            _JS,
            media_type="application/javascript",
            headers=_SECURITY_HEADERS,
        )
