"""Server-rendered dashboard assets (UI-05).

The dashboard is deliberately dependency-free: three small assets served by the
same origin as the API, a strict CSP (no inline script, no third-party source)
and a bearer token that is kept in memory only. Every datum comes from the
authenticated API, so the UI cannot bypass server-side authorization; the UI
simply shows fewer things when the token's scopes are narrower.
"""

from __future__ import annotations

_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OGX Control Plane</title>
<link rel="stylesheet" href="/dashboard.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="bar">
  <div>
    <h1>OMEGA GUARDIAN X</h1>
    <p class="muted" id="product-line">Control plane</p>
  </div>
  <form id="token-form" autocomplete="off">
    <label for="token">Service token</label>
    <input id="token" name="token" type="password" spellcheck="false"
           aria-describedby="token-help" placeholder="ogxst.…">
    <button type="submit">Connect</button>
    <button type="button" id="disconnect" class="secondary">Clear</button>
  </form>
</header>
<p id="token-help" class="muted small">
  Tokens are held in memory only; reloading the page forgets them. The dashboard
  shows only what the token's tenant and scopes allow.
</p>

<main id="main">
  <div id="status" role="status" aria-live="polite"></div>

  <nav class="tabs" role="tablist" aria-label="Sections">
    <button role="tab" data-view="overview" aria-selected="true">Overview</button>
    <button role="tab" data-view="projects" aria-selected="false">Projects</button>
    <button role="tab" data-view="hosts" aria-selected="false">Hosts</button>
    <button role="tab" data-view="governance" aria-selected="false">Governance</button>
    <button role="tab" data-view="audit" aria-selected="false">Audit</button>
    <button role="tab" data-view="policy" aria-selected="false">Policy</button>
  </nav>

  <section id="view-overview" class="view" aria-labelledby="h-overview">
    <h2 id="h-overview">Overview</h2>
    <div id="overview-cards" class="cards"></div>
    <h3>Queue</h3>
    <div id="queue"></div>
  </section>

  <section id="view-projects" class="view" hidden aria-labelledby="h-projects">
    <h2 id="h-projects">Projects</h2>
    <div id="projects"></div>
  </section>

  <section id="view-hosts" class="view" hidden aria-labelledby="h-hosts">
    <h2 id="h-hosts">Hosts</h2>
    <p class="muted small">Capabilities are reported by the host adapters; a
      registered host is not a live session.</p>
    <div id="hosts"></div>
  </section>

  <section id="view-governance" class="view" hidden aria-labelledby="h-governance">
    <h2 id="h-governance">Governance records</h2>
    <div id="governance"></div>
  </section>

  <section id="view-audit" class="view" hidden aria-labelledby="h-audit">
    <h2 id="h-audit">Audit events</h2>
    <p class="muted small">Newest first. Export requires the
      <code>audit.export</code> scope.</p>
    <button type="button" id="export-audit" class="secondary">Download NDJSON</button>
    <div id="audit"></div>
  </section>

  <section id="view-policy" class="view" hidden aria-labelledby="h-policy">
    <h2 id="h-policy">Policy matrix</h2>
    <p class="muted small">Deny by default: a role must grant an action and, for
      service tokens, the token scope must grant it too.</p>
    <div id="policy"></div>
  </section>
</main>

<footer class="muted small">
  Local control plane. Authentication is service-identity based; interactive
  SSO (OIDC) is not implemented in this build.
</footer>

<script src="/dashboard.js"></script>
</body>
</html>
"""


CSS = """:root {
  color-scheme: light dark;
  --bg: #0b0e14;
  --panel: #141a24;
  --panel-2: #1b2331;
  --text: #e6edf7;
  --muted: #93a1b5;
  --accent: #5cc8ff;
  --ok: #4ade80;
  --bad: #f87171;
  --border: #263043;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 1rem 2rem;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
h1 { font-size: 1.1rem; letter-spacing: .12em; margin: 0; }
h2 { font-size: 1rem; margin: 1.2rem 0 .6rem; }
h3 { font-size: .9rem; margin: 1rem 0 .4rem; color: var(--muted); }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 1rem; top: 1rem; background: var(--panel); padding: .5rem; }
.bar {
  display: flex; flex-wrap: wrap; gap: 1rem; justify-content: space-between;
  align-items: end; padding: 1rem 0; border-bottom: 1px solid var(--border);
}
.muted { color: var(--muted); }
.small { font-size: .82rem; }
form { display: flex; flex-wrap: wrap; gap: .5rem; align-items: end; }
label { font-size: .78rem; color: var(--muted); display: block; }
input {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  padding: .45rem .6rem; border-radius: 6px; min-width: 16rem;
}
button {
  background: var(--accent); color: #04121b; border: 0; border-radius: 6px;
  padding: .5rem .8rem; font-weight: 600; cursor: pointer;
}
button.secondary { background: transparent; color: var(--text); border: 1px solid var(--border); }
button:focus-visible, input:focus-visible, a:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
.tabs { display: flex; flex-wrap: wrap; gap: .4rem; margin: 1rem 0; }
.tabs [role="tab"] {
  background: var(--panel); color: var(--text); border: 1px solid var(--border);
  font-weight: 500;
}
.tabs [aria-selected="true"] { border-color: var(--accent); color: var(--accent); }
.cards { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: .7rem .8rem; }
.card .value { font-size: 1.4rem; font-weight: 700; }
table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 8px; overflow: hidden; }
caption { text-align: left; color: var(--muted); font-size: .82rem; padding: .3rem 0; }
th, td { text-align: left; padding: .45rem .55rem; border-bottom: 1px solid var(--border); font-size: .86rem; }
th { color: var(--muted); font-weight: 600; }
.table-wrap { overflow-x: auto; }
.badge { padding: .1rem .45rem; border-radius: 999px; border: 1px solid var(--border); font-size: .75rem; }
.ok { color: var(--ok); }
.bad { color: var(--bad); }
.empty { color: var(--muted); padding: .6rem 0; }
#status:empty { display: none; }
#status { padding: .6rem 0; }
.scroll { max-height: 26rem; overflow: auto; }
"""


JS = """'use strict';
(function () {
  var token = '';
  var apiVersion = 'v1';
  var state = { org: null, meta: null };

  function byId(id) { return document.getElementById(id); }

  function status(message, kind) {
    var node = byId('status');
    node.textContent = message || '';
    node.className = kind === 'error' ? 'bad' : (kind === 'ok' ? 'ok' : '');
  }

  function el(tag, text, className) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    if (className) { node.className = className; }
    return node;
  }

  function table(caption, columns, rows) {
    var wrap = el('div', null, 'table-wrap');
    var node = el('table');
    if (caption) { var cap = el('caption', caption); node.appendChild(cap); }
    var head = el('thead');
    var headRow = el('tr');
    columns.forEach(function (column) { headRow.appendChild(el('th', column)); });
    head.appendChild(headRow);
    node.appendChild(head);
    var body = el('tbody');
    if (!rows.length) {
      var empty = el('tr');
      var cell = el('td', 'Nothing to show.', 'empty');
      cell.colSpan = columns.length || 1;
      empty.appendChild(cell);
      body.appendChild(empty);
    } else {
      rows.forEach(function (row) {
        var tr = el('tr');
        row.forEach(function (value) { tr.appendChild(el('td', value)); });
        body.appendChild(tr);
      });
    }
    node.appendChild(body);
    wrap.appendChild(node);
    return wrap;
  }

  function api(path, options) {
    var settings = options || {};
    var headers = { 'Accept': 'application/json' };
    if (token) { headers['Authorization'] = 'Bearer ' + token; }
    if (settings.body) { headers['Content-Type'] = 'application/json'; }
    return fetch(path, {
      method: settings.method || 'GET',
      headers: headers,
      body: settings.body ? JSON.stringify(settings.body) : undefined,
      credentials: 'omit'
    }).then(function (response) {
      if (response.status === 401) {
        status('This token was rejected. Sign in again.', 'error');
        throw new Error('unauthorized');
      }
      return response.text().then(function (text) {
        var payload = text ? JSON.parse(text) : {};
        if (!response.ok) {
          var message = payload && payload.error ? payload.error.message : response.statusText;
          throw new Error(message || 'request failed');
        }
        return payload;
      });
    });
  }

  function cards(target, items) {
    target.textContent = '';
    items.forEach(function (item) {
      var card = el('div', null, 'card');
      card.appendChild(el('div', item.label, 'muted small'));
      card.appendChild(el('div', item.value, 'value'));
      if (item.note) { card.appendChild(el('div', item.note, 'muted small')); }
      target.appendChild(card);
    });
  }

  function needOrg() {
    if (state.org) { return Promise.resolve(state.org); }
    return api('/api/v1/organizations').then(function (payload) {
      var items = payload.data.items || [];
      if (!items.length) { throw new Error('This token has no visible organization.'); }
      state.org = items[0];
      return state.org;
    });
  }

  function loadOverview() {
    return needOrg().then(function (org) {
      byId('product-line').textContent = org.slug + ' · ' + (state.meta ? state.meta.product_version : '');
      return Promise.all([
        api('/api/v1/organizations/' + encodeURIComponent(org.id) + '/projects?limit=1'),
        api('/api/v1/organizations/' + encodeURIComponent(org.id) + '/members?limit=1'),
        api('/api/v1/jobs/stats').catch(function () { return { data: null }; }),
        api('/api/v1/hosts').catch(function () { return { data: { items: [] } }; })
      ]);
    }).then(function (results) {
      var jobs = results[2].data || {};
      cards(byId('overview-cards'), [
        { label: 'Projects', value: results[0].data.total },
        { label: 'Members', value: results[1].data.total },
        { label: 'Registered hosts', value: (results[3].data.items || []).length },
        { label: 'Queued jobs', value: jobs.pending === undefined ? 'n/a' : jobs.pending,
          note: jobs.dead ? jobs.dead + ' dead-lettered' : '' }
      ]);
      var queue = byId('queue');
      queue.textContent = '';
      if (!results[2].data) {
        queue.appendChild(el('p', 'Queue statistics are not available to this token.', 'muted'));
        return;
      }
      var rows = Object.keys(jobs).sort().map(function (key) { return [key, jobs[key]]; });
      queue.appendChild(table('Jobs by state', ['Metric', 'Value'], rows));
    });
  }

  function loadProjects() {
    return needOrg().then(function (org) {
      return api('/api/v1/organizations/' + encodeURIComponent(org.id) + '/projects?limit=100');
    }).then(function (payload) {
      var rows = payload.data.items.map(function (project) {
        return [project.name, project.provider, project.repository_ref || '—', project.status];
      });
      var target = byId('projects');
      target.textContent = '';
      target.appendChild(table(payload.data.total + ' project(s)', ['Name', 'Provider', 'Repository', 'Status'], rows));
    });
  }

  function loadHosts() {
    return api('/api/v1/hosts').then(function (payload) {
      var rows = payload.data.items.map(function (host) {
        var supported = (host.capabilities || []).filter(function (item) {
          return item.status === 'supported';
        }).map(function (item) { return item.name; });
        return [host.host_type, host.provider, host.status, supported.join(', ') || 'none'];
      });
      var target = byId('hosts');
      target.textContent = '';
      target.appendChild(table(payload.data.items.length + ' host(s)', ['Type', 'Provider', 'Status', 'Supported'], rows));
    });
  }

  function loadGovernance() {
    return needOrg().then(function (org) {
      return api('/api/v1/organizations/' + encodeURIComponent(org.id) + '/governance/records?limit=100');
    }).then(function (payload) {
      var rows = payload.data.items.map(function (record) {
        return [record.record_type, record.title, record.priority, record.status, record.owner_subject];
      });
      var target = byId('governance');
      target.textContent = '';
      target.appendChild(table(payload.data.total + ' record(s)',
        ['Type', 'Title', 'Priority', 'Status', 'Owner'], rows));
    });
  }

  function loadAudit() {
    return needOrg().then(function (org) {
      return api('/api/v1/organizations/' + encodeURIComponent(org.id) + '/audit?limit=100');
    }).then(function (payload) {
      var rows = payload.data.items.map(function (event) {
        return [event.occurred_at, event.action, event.resource_type, event.resource_id || '—'];
      });
      var target = byId('audit');
      target.textContent = '';
      var wrap = table(payload.data.total + ' event(s)',
        ['When', 'Action', 'Resource', 'Id'], rows);
      wrap.classList.add('scroll');
      target.appendChild(wrap);
    });
  }

  function loadPolicy() {
    return api('/api/v1/policy/matrix').then(function (payload) {
      var matrix = payload.data.matrix;
      var roles = Object.keys(matrix);
      var actions = payload.data.actions;
      var rows = actions.map(function (action) {
        return [action].concat(roles.map(function (role) {
          return matrix[role].indexOf(action) !== -1 ? 'allow' : '—';
        }));
      });
      var target = byId('policy');
      target.textContent = '';
      var wrap = table('Role / action matrix', ['Action'].concat(roles), rows);
      wrap.classList.add('scroll');
      target.appendChild(wrap);
    });
  }

  var loaders = {
    overview: loadOverview,
    projects: loadProjects,
    hosts: loadHosts,
    governance: loadGovernance,
    audit: loadAudit,
    policy: loadPolicy
  };

  function show(view) {
    Object.keys(loaders).forEach(function (name) {
      byId('view-' + name).hidden = name !== view;
    });
    Array.prototype.forEach.call(document.querySelectorAll('[role="tab"]'), function (tab) {
      var selected = tab.getAttribute('data-view') === view;
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
    });
    if (!token) { return; }
    status('Loading…');
    loaders[view]().then(function () { status(''); }).catch(function (error) {
      if (error.message !== 'unauthorized') { status(error.message, 'error'); }
    });
  }

  document.querySelectorAll('[role="tab"]').forEach(function (tab) {
    tab.addEventListener('click', function () { show(tab.getAttribute('data-view')); });
  });

  byId('token-form').addEventListener('submit', function (event) {
    event.preventDefault();
    token = byId('token').value.trim();
    byId('token').value = '';
    if (!token) { status('Enter a service token.', 'error'); return; }
    api('/api/v1/meta').then(function (payload) {
      state.meta = payload.data;
      apiVersion = payload.data.api_version;
      status('Connected.', 'ok');
      show('overview');
    }).catch(function (error) {
      token = '';
      if (error.message !== 'unauthorized') { status(error.message, 'error'); }
    });
  });

  byId('disconnect').addEventListener('click', function () {
    token = '';
    state = { org: null, meta: null };
    byId('overview-cards').textContent = '';
    byId('projects').textContent = '';
    byId('hosts').textContent = '';
    byId('governance').textContent = '';
    byId('audit').textContent = '';
    byId('policy').textContent = '';
    byId('queue').textContent = '';
    byId('product-line').textContent = 'Control plane';
    status('Signed out.');
  });

  byId('export-audit').addEventListener('click', function () {
    if (!token) { status('Sign in first.', 'error'); return; }
    needOrg().then(function (org) {
      return fetch('/api/v1/organizations/' + encodeURIComponent(org.id) + '/audit/export', {
        headers: { 'Authorization': 'Bearer ' + token },
        credentials: 'omit'
      });
    }).then(function (response) {
      if (!response.ok) { throw new Error('Export failed (' + response.status + ').'); }
      return response.text();
    }).then(function (text) {
      var blob = new Blob([text], { type: 'application/x-ndjson' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'ogx-audit.ndjson';
      link.click();
      URL.revokeObjectURL(link.href);
    }).catch(function (error) { status(error.message, 'error'); });
  });

  api('/api/v1/meta').then(function (payload) {
    state.meta = payload.data;
    byId('product-line').textContent = 'Control plane · ' + payload.data.product_version;
  }).catch(function () { /* meta is public; ignore */ });
})();
"""


def render_html() -> str:
    return HTML


def render_css() -> str:
    return CSS


def render_js() -> str:
    return JS


__all__ = ["CSS", "HTML", "JS", "SECURITY_HEADERS", "render_css", "render_html", "render_js"]
