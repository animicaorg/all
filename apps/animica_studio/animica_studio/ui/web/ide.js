/* Animica Studio IDE — bridge + Monaco integration */
'use strict';

var bridge = null;
var editor = null;
var tabs = []; // [{path, absPath, model, dirty, viewState}]
var openTabs = {}; // normalized abs path -> index
var activeTab = -1;
var pendingOpenFiles = [];

window.currentFilePath = null;

function normalizePath(path) {
  return String(path || '').replace(/\\/g, '/');
}

function initChannel() {
  if (typeof QWebChannel === 'undefined') {
    console.error('qwebchannel.js not loaded');
    return;
  }
  new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge = channel.objects.bridge;
    setupBridgeSignals();
    flushPendingOpens();
    bridge.log('IDE ready');
  });
}

function flushPendingOpens() {
  if (!bridge || !pendingOpenFiles.length) return;
  var queued = pendingOpenFiles.slice();
  pendingOpenFiles = [];
  queued.forEach(function(file) {
    openFile(file.path, file.absPath);
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
}

var pendingRequests = {};
var _reqCounter = 0;
function makeReqId(prefix) { return prefix + '_' + (++_reqCounter); }
function callBridge(method, args, cb) {
  var reqId = makeReqId(method);
  if (cb) pendingRequests[reqId] = cb;
  bridge[method].apply(bridge, [reqId].concat(args));
}
function safeParse(str) {
  try { return JSON.parse(str); } catch (e) { return {ok: false, error: 'JSON parse error: ' + e}; }
}

function rebuildOpenTabsIndex() {
  openTabs = {};
  tabs.forEach(function(tab, i) {
    openTabs[normalizePath(tab.absPath || tab.path)] = i;
  });
}

function openFile(path, absPath) {
  if (!bridge) {
    pendingOpenFiles.push({path: path, absPath: absPath});
    return;
  }
  var normalizedAbsPath = normalizePath(absPath || path);
  if (Object.prototype.hasOwnProperty.call(openTabs, normalizedAbsPath)) {
    activateTab(openTabs[normalizedAbsPath]);
    return;
  }
  callBridge('readFile', [path], function(data) {
    if (!data.ok) { showError('Cannot open ' + path + ': ' + data.error); return; }
    addTab(path, normalizedAbsPath, data.content);
  });
}

function addTab(path, absPath, content) {
  var model = null;
  if (editor && window.monaco) {
    model = monaco.editor.createModel(content, detectLanguage(path));
  }
  tabs.push({path: path, absPath: absPath, model: model, dirty: false, content: content, viewState: null});
  rebuildOpenTabsIndex();
  activateTab(tabs.length - 1);
  renderTabs();
}

function activateTab(idx) {
  if (idx < 0 || idx >= tabs.length) return;
  if (activeTab >= 0 && tabs[activeTab] && editor) {
    tabs[activeTab].viewState = editor.saveViewState();
  }
  activeTab = idx;
  var tab = tabs[idx];
  window.currentFilePath = tab.path;
  if (editor) {
    if (tab.model) editor.setModel(tab.model);
    else editor.setValue(tab.content || '');
    if (tab.viewState) editor.restoreViewState(tab.viewState);
    editor.focus();
  }
  renderTabs();
}

function closeTab(idx) {
  if (!tabs[idx]) return;
  var tab = tabs[idx];
  if (tab.dirty) {
    var choice = prompt('Unsaved changes in ' + tab.path + '. Type: save / discard / cancel', 'save');
    if (choice === null || choice.toLowerCase() === 'cancel') return;
    if (choice.toLowerCase() === 'save') {
      var content = tab.model ? tab.model.getValue() : (editor ? editor.getValue() : tab.content);
      callBridge('writeFile', [tab.path, content], function(data) {
        if (!data.ok) {
          showError('Save failed: ' + data.error);
          return;
        }
        tab.dirty = false;
        doCloseTab(idx);
      });
      return;
    }
  }
  doCloseTab(idx);
}

function doCloseTab(idx) {
  if (tabs[idx] && tabs[idx].model) tabs[idx].model.dispose();
  tabs.splice(idx, 1);
  rebuildOpenTabsIndex();
  if (activeTab >= tabs.length) activeTab = tabs.length - 1;
  if (activeTab >= 0) activateTab(activeTab);
  else if (editor) {
    window.currentFilePath = null;
    editor.setValue('');
  }
  renderTabs();
}

function renderTabs() {
  var container = document.getElementById('tabs');
  container.innerHTML = '';
  tabs.forEach(function(tab, i) {
    var div = document.createElement('div');
    div.className = 'tab' + (i === activeTab ? ' active' : '') + (tab.dirty ? ' dirty' : '');
    div.title = tab.path;
    div.textContent = tab.path.split('/').pop();
    div.onclick = function() { activateTab(i); };
    var closeBtn = document.createElement('span');
    closeBtn.className = 'close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = function(e) { e.stopPropagation(); closeTab(i); };
    div.appendChild(closeBtn);
    container.appendChild(div);
  });
}

window.saveCurrentFile = function() {
  if (activeTab < 0 || !tabs[activeTab]) return;
  var tab = tabs[activeTab];
  var content = editor ? (tab.model ? tab.model.getValue() : editor.getValue()) : tab.content;
  callBridge('writeFile', [tab.path, content], function(data) {
    if (data.ok) { tab.dirty = false; renderTabs(); }
    else { showError('Save failed: ' + data.error); }
  });
};

window.saveAllFiles = function() {
  tabs.forEach(function(_tab, i) {
    if (!tabs[i].dirty) return;
    var tab = tabs[i];
    var content = tab.model ? tab.model.getValue() : (editor ? editor.getValue() : tab.content);
    callBridge('writeFile', [tab.path, content], function(data) {
      if (data.ok) { tab.dirty = false; renderTabs(); }
      else { showError('Save failed for ' + tab.path + ': ' + data.error); }
    });
  });
};

window.openFileFromHost = function(relPath, absPath) {
  openFile(relPath, absPath);
};

window.getCurrentEditorState = function() {
  if (activeTab < 0 || !tabs[activeTab]) return JSON.stringify({ok: false});
  var tab = tabs[activeTab];
  var content = tab.model ? tab.model.getValue() : (editor ? editor.getValue() : (tab.content || ''));
  return JSON.stringify({
    ok: true,
    path: tab.path,
    absPath: tab.absPath,
    content: content
  });
};

window.resetStudioEditor = function() {
  tabs.forEach(function(tab) {
    if (tab && tab.model) tab.model.dispose();
  });
  tabs = [];
  openTabs = {};
  activeTab = -1;
  window.currentFilePath = null;
  if (editor && typeof monaco !== 'undefined') {
    var model = monaco.editor.createModel('', 'python');
    editor.setModel(model);
  }
  renderTabs();
};

function detectLanguage(path) {
  var ext = path.split('.').pop().toLowerCase();
  var map = {py: 'python', js: 'javascript', ts: 'typescript', json: 'json', md: 'markdown', yaml: 'yaml', yml: 'yaml', html: 'html', css: 'css', sh: 'shell', toml: 'toml', rs: 'rust'};
  return map[ext] || 'plaintext';
}

function showError(msg) {
  console.error('IDE:', msg);
  if (bridge) bridge.log('error: ' + msg);
}

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
    wordWrap: 'off'
  });

  editor.onDidChangeModelContent(function() {
    if (activeTab >= 0 && tabs[activeTab]) {
      tabs[activeTab].dirty = true;
      renderTabs();
    }
  });

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() { window.saveCurrentFile(); });
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyS, function() { window.saveAllFiles(); });
}

document.addEventListener('keydown', function(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    if (e.shiftKey) window.saveAllFiles();
    else window.saveCurrentFile();
  }
});

(function boot() {
  var monacoScript = document.createElement('script');
  monacoScript.src = 'monaco/vs/loader.js';
  monacoScript.onload = function() {
    require.config({paths: {vs: 'monaco/vs'}});
    require(['vs/editor/editor.main'], function() { initMonaco(); initChannel(); });
  };
  monacoScript.onerror = function() { initMonaco(); initChannel(); };
  document.head.appendChild(monacoScript);
})();
