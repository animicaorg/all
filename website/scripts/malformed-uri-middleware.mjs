const ESCAPED_PERCENT = /%/g;

export function sanitizeMalformedUri(url) {
  if (typeof url !== 'string') {
    return url;
  }

  try {
    decodeURI(url);
    return url;
  } catch {
    return url.replace(ESCAPED_PERCENT, '%25');
  }
}

export function createMalformedUriMiddleware() {
  return function malformedUriMiddleware(req, _res, next) {
    req.url = sanitizeMalformedUri(req.url);
    next();
  };
}
