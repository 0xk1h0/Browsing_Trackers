(function() {
  if (window.__fp_calls) return;
  window.__fp_calls = [];

  function _getCallerScript() {
    try {
      var err = new Error();
      var stack = err.stack || '';
      var lines = stack.split('\n');
      for (var i = 2; i < lines.length; i++) {
        var m = lines[i].match(/(?:at\s+)?(?:.*?\s)?\(?(https?:\/\/[^\s\)]+)/);
        if (m) return m[1];
      }
    } catch(e) {}
    return 'inline';
  }

  function _hashValue(val) {
    if (val === undefined || val === null) return null;
    var s = String(val);
    var hash = 0;
    for (var i = 0; i < s.length; i++) {
      hash = ((hash << 5) - hash) + s.charCodeAt(i);
      hash |= 0;
    }
    return hash.toString(16);
  }

  function _record(apiName, args, retVal) {
    window.__fp_calls.push({
      api_name: apiName,
      caller_script: _getCallerScript(),
      timestamp: new Date().toISOString(),
      arguments: args ? Array.prototype.map.call(args, String) : null,
      return_value_hash: _hashValue(retVal)
    });
  }

  try {
    var _orig_HTMLCanvasElement_prototype_toDataURL = HTMLCanvasElement.prototype.toDataURL;
    if (typeof _orig_HTMLCanvasElement_prototype_toDataURL === 'function') {
      HTMLCanvasElement.prototype.toDataURL = function() {
        var ret = _orig_HTMLCanvasElement_prototype_toDataURL.apply(this, arguments);
        _record('HTMLCanvasElement.prototype.toDataURL', arguments, ret);
        return ret;
      };
    }
  } catch(e) {}

  try {
    var _orig_WebGLRenderingContext_prototype_getParameter = WebGLRenderingContext.prototype.getParameter;
    if (typeof _orig_WebGLRenderingContext_prototype_getParameter === 'function') {
      WebGLRenderingContext.prototype.getParameter = function() {
        var ret = _orig_WebGLRenderingContext_prototype_getParameter.apply(this, arguments);
        _record('WebGLRenderingContext.prototype.getParameter', arguments, ret);
        return ret;
      };
    }
  } catch(e) {}

  try {
    var _orig_AudioContext_prototype_createOscillator = AudioContext.prototype.createOscillator;
    if (typeof _orig_AudioContext_prototype_createOscillator === 'function') {
      AudioContext.prototype.createOscillator = function() {
        var ret = _orig_AudioContext_prototype_createOscillator.apply(this, arguments);
        _record('AudioContext.prototype.createOscillator', arguments, ret);
        return ret;
      };
    }
  } catch(e) {}

  try {
    var _orig_plugins = Object.getOwnPropertyDescriptor(navigator, 'plugins');
    if (_orig_plugins && _orig_plugins.get) {
      Object.defineProperty(navigator, 'plugins', {
        get: function() {
          var val = _orig_plugins.get.call(this);
          _record('navigator.plugins', null, val);
          return val;
        },
        configurable: true
      });
    }
  } catch(e) {}

  try {
    var _orig_languages = Object.getOwnPropertyDescriptor(navigator, 'languages');
    if (_orig_languages && _orig_languages.get) {
      Object.defineProperty(navigator, 'languages', {
        get: function() {
          var val = _orig_languages.get.call(this);
          _record('navigator.languages', null, val);
          return val;
        },
        configurable: true
      });
    }
  } catch(e) {}

  try {
    var _orig_width = Object.getOwnPropertyDescriptor(screen, 'width');
    if (_orig_width && _orig_width.get) {
      Object.defineProperty(screen, 'width', {
        get: function() {
          var val = _orig_width.get.call(this);
          _record('screen.width', null, val);
          return val;
        },
        configurable: true
      });
    }
  } catch(e) {}

  try {
    var _orig_height = Object.getOwnPropertyDescriptor(screen, 'height');
    if (_orig_height && _orig_height.get) {
      Object.defineProperty(screen, 'height', {
        get: function() {
          var val = _orig_height.get.call(this);
          _record('screen.height', null, val);
          return val;
        },
        configurable: true
      });
    }
  } catch(e) {}

})();

