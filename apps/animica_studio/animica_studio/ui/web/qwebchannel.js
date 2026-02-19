/* qwebchannel.js stub — replaced by Qt's actual qwebchannel.js at runtime.
 * The IdePage Python code will inject the real file via QWebEngineScript
 * or by serving it alongside ide.html.
 *
 * See: https://doc.qt.io/qt-6/qwebchannel.html
 */
if (typeof QWebChannel === 'undefined') {
  console.warn('qwebchannel.js stub loaded — real Qt WebChannel not available.');
  window.QWebChannel = function(transport, cb) {
    console.error('QWebChannel: real implementation not loaded');
  };
}
