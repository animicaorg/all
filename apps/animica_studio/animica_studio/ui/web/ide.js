/* Animica Studio IDE — bridge + Monaco integration */
'use strict';

// -- State ----------------------------------------------------------------
var bridge = null;
var editor = null;
var tabs = [];          // [{path, model, dirty}]
var activeTab = -1;

window.currentFilePath = null;

// -- QWebChannel init -----------------------------------------------------
function initChannel() {
  if (typeof QWebChannel === 'undefined') {
    console.error('qwebchannel.js not loaded');
    return;
  }
  new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge = channel.objects.bridge;
    setupBridgeSignals();
    bridge.log('IDE ready');
  });
}

function setupBridgeSignals() {
  bridge.listDirResult.connect(function(reqId, jsonStr) {
    var data = safeParse(jsonStr);
    var cb = pendingRequests[reqId];
    if (cb) { delete pendingRequests[reqId]; cb(data); }
  });
  bridge.readFileResult.connect(function(reqId, jsonStr) {
    var data = safeParse(jsonStr);
    var cb = pendingRequests[reqId];
    if (cb) { delete pendingRequests[reqId]; cb(data); }
  });
  bridge.writeFileResult.connect(function(reqId, jsonStr) {
    var data = safeParse(jsonStr);
    var cb = pendingRequests[reqId];
    if (cb) { delete pendingRequests[reqId]; cb(data); }
  });
  bridge.runScriptLine.connect(function(line) {
    bridge.log('script: ' + line);
  });
  bridge.workspaceChanged.connect(function(path) {
    bridge.log('workspace: ' + path);
  });
}

// -- Pending request map --------------------------------------------------
var pendingRequests = {};
var _reqCounter = 0;
function makeReqId(prefix) { return prefix + '_' + (++_reqCounter); }

function callBridge(method, args, cb) {
  var reqId = makeReqId(method);
  if (cb) pendingRequests[reqId] = cb;
  bridge[method].apply(bridge, [reqId].concat(args));
}

function safeParse(str) {
  try { return JSON.parse(str); } catch(e) { return {ok: false, error: 'JSON parse error: ' + e}; }
}

// -- Tab management -------------------------------------------------------
function openFile(path) {
  if (!bridge) { showError('Bridge not ready'); return; }
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].path === path) { activateTab(i); return; }
  }
  callBridge('readFile', [path], function(data) {
    if (!data.ok) { showError('Cannot open ' + path + ': ' + data.error); return; }
    addTab(path, data.content);
  });
}

function addTab(path, content) {
  var model = null;
  if (editor && window.monaco) {
    var lang = detectLanguage(path);
    model = monaco.editor.createModel(content, lang);
  }
  var tab = {path: path, model: model, dirty: false, content: content};
  tabs.push(tab);
  activateTab(tabs.length - 1);
  renderTabs();
}

function activateTab(idx) {
  activeTab = idx;
  window.currentFilePath = tabs[idx] ? tabs[idx].path : null;
  if (editor && tabs[idx] && tabs[idx].model) {
    editor.setModel(tabs[idx].model);
  } else if (editor && tabs[idx]) {
    editor.setValue(tabs[idx].content || '');
  }
  renderTabs();
}

function closeTab(idx) {
  if (tabs[idx] && tabs[idx].dirty) {
    if (!confirm('Unsaved changes in ' + tabs[idx].path + '. Close anyway?')) return;
  }
  if (tabs[idx] && tabs[idx].model) tabs[idx].model.dispose();
  tabs.splice(idx, 1);
  if (activeTab >= tabs.length) activeTab = tabs.length - 1;
  if (activeTab >= 0) activateTab(activeTab);
  else { window.currentFilePath = null; if(editor) editor.setValue(''); }
  renderTabs();
}

function renderTabs() {
  var container = document.getElementById('tabs');
  container.innerHTML = '';
  tabs.forEach(function(tab, i) {
    var div = document.createElement('div');
    div.className = 'tab' + (i === activeTab ? ' active' : '') + (tab.dirty ? ' dirty' : '');
    var name = tab.path.split('/').pop();
    div.textContent = name;
    div.onclick = (function(idx) { return function() { activateTab(idx); }; })(i);
    var closeBtn = document.createElement('span');
    closeBtn.className = 'close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = (function(idx) {
      return function(e) { e.stopPropagation(); closeTab(idx); };
    })(i);
    div.appendChild(closeBtn);
    container.appendChild(div);
  });
}

// -- Save -----------------------------------------------------------------
window.saveCurrentFile = function() {
  if (activeTab < 0 || !tabs[activeTab]) return;
  var tab = tabs[activeTab];
  var content = editor ? (tab.model ? tab.model.getValue() : editor.getValue()) : tab.content;
  if (!bridge) { showError('Bridge not ready'); return; }
  callBridge('writeFile', [tab.path, content], function(data) {
    if (data.ok) { tab.dirty = false; renderTabs(); }
    else { showError('Save failed: ' + data.error); }
  });
};

// -- Language detection ---------------------------------------------------
function detectLanguage(path) {
  var ext = path.split('.').pop().toLowerCase();
  var map = {py: 'python', js: 'javascript', ts: 'typescript', json: 'json',
             md: 'markdown', yaml: 'yaml', yml: 'yaml', html: 'html', css: 'css',
             sh: 'shell', toml: 'toml', rs: 'rust'};
  return map[ext] || 'plaintext';
}

// -- Error display --------------------------------------------------------
function showError(msg) {
  console.error('IDE:', msg);
  if (bridge) bridge.log('error: ' + msg);
}

// -- Monaco init ----------------------------------------------------------
function initMonaco() {
  var editorEl = document.getElementById('editor');
  var noMonacoEl = document.getElementById('no-monaco');

  if (typeof monaco === 'undefined') {
    editorEl.style.display = 'none';
    noMonacoEl.style.display = 'flex';
    return;
  }

  editor = monaco.editor.create(editorEl, {
    value: '# Welcome to Animica Studio IDE\n',
    language: 'python',
    theme: 'vs-dark',
    automaticLayout: true,
    minimap: {enabled: false},
    fontSize: 13,
    scrollBeyondLastLine: false,
    wordWrap: 'off',
  });

  editor.onDidChangeModelContent(function() {
    if (activeTab >= 0 && tabs[activeTab]) {
      tabs[activeTab].dirty = true;
      renderTabs();
    }
  });

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {
    window.saveCurrentFile();
  });
}

// -- Keyboard shortcut for save (outside Monaco) --------------------------
document.addEventListener('keydown', function(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    window.saveCurrentFile();
  }
});

// -- Boot sequence --------------------------------------------------------
(function boot() {
  var monacoScript = document.createElement('script');
  monacoScript.src = 'monaco/vs/loader.js';
  monacoScript.onload = function() {
    require.config({paths: {vs: 'monaco/vs'}});
    require(['vs/editor/editor.main'], function() {
      initMonaco();
      initChannel();
    });
  };
  monacoScript.onerror = function() {
    initMonaco();
    initChannel();
  };
  document.head.appendChild(monacoScript);
})();
