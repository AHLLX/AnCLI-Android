/**
 * Build a screenshot harness for the AnCLI WebUI.
 *
 * The real page talks to the KernelSU exec bridge; here we stub `window.ksu` and
 * replay the payloads captured from the device (`.webui-preview/list.json`), so a
 * headless browser renders exactly what the manager's WebView shows.
 *
 * Usage: node .webui-preview/build.js
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const src = path.join(root, 'src', 'module', 'webroot', 'index.html');
const listJson = fs.readFileSync(path.join(__dirname, 'list.json'), 'utf8');

const status = {
  ancli_version: '1.2.3',
  rootfs_ready: true,
  proot_deployed: true,
  installed_count: 5,
};

// A couple of scenarios so the console can be judged with real-looking output.
const scenarios = {
  default: [],
  busy: [
    "logLine('$ ancli update dsh  （后台执行）', 'cmd')",
    "logLine('==> 正在从官方 npm 源拉取 @deepseek-ai/dsh …')",
    "logLine('added 1 package, and audited 2 packages in 41s')",
    "logLine('✓ 完成', 'ok')",
    "logLine('$ ancli check', 'cmd')",
    "logLine('[!] github.com 连接超时，重试 1/3', 'err')",
    "logLine('Grok CLI: 官方 v1.0.41 [channel] -> 可更新')",
    "logLine('exit code: 1', 'err')",
  ],
};

const mock = `
<script>
(function () {
  const STATUS = ${JSON.stringify(status)};
  const LIST = ${listJson};
  const routes = [
    [/\\[ -x .*\\] && echo yes/, 'yes\\n'],
    [/status --json/, JSON.stringify(STATUS)],
    [/list --json/, JSON.stringify(LIST)],
    [/find \\/data\\/local\\/tmp\\/ancli -maxdepth/, ''],
    [/tail -c/, ''],
    [/\\[ -f .* \\] && cat/, 'echo -n'],
  ];
  function respond(cmd) {
    for (const [re, out] of routes) if (re.test(cmd)) return out;
    return '';
  }
  window.ksu = {
    exec: function (cmd, opts, cbName) {
      const out = respond(cmd);
      setTimeout(function () { window[cbName](0, out, ''); }, 3);
    },
  };
})();
</script>
`;

const trailer = (scenario, theme, dialog) => `
<style>
/* Screenshot harness only: headless virtual-time does not advance CSS
   transitions, so shots would otherwise catch the mid-transition state. */
* { transition: none !important; animation: none !important; }
</style>
<script>
(function () {
  applyTheme(${JSON.stringify(theme)});
  ${scenarios[scenario].join('\n  ')}
  ${dialog ? `setTimeout(function () { openConfig(${JSON.stringify(dialog)}); }, 400);` : ''}
  ${scenario === 'busy' ? `setTimeout(function () { document.getElementById('log').closest('section').scrollIntoView({ block: 'center' }); }, 700);` : ''}
})();
</script>
`;

let html = fs.readFileSync(src, 'utf8');
// Inject the mock before the page's own script so findBridge() sees it.
html = html.replace('<script>\n/* ====', mock + '<script>\n/* ====');
if (!html.includes('window.ksu')) throw new Error('mock injection failed');

const targets = [
  { file: 'preview-light.html', scenario: 'default', theme: 'light', dialog: null },
  { file: 'preview-dark.html', scenario: 'default', theme: 'dark', dialog: null },
  { file: 'preview-log.html', scenario: 'busy', theme: 'light', dialog: null },
  { file: 'preview-dialog.html', scenario: 'default', theme: 'dark', dialog: 'claude-code' },
];

for (const t of targets) {
  fs.writeFileSync(path.join(__dirname, t.file), html + trailer(t.scenario, t.theme, t.dialog), 'utf8');
}
console.log('built: ' + targets.map(t => t.file).join(', '));
