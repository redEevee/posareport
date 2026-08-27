/* Posa Report browser tracker. Do not send names, phones, emails, or addresses. */
(function () {
  var script = document.currentScript;
  var endpoint = new URL('/api/events', script.src).toString();

  function send(type, properties) {
    var payload = {
      type: type,
      page_url: location.href,
      referrer: document.referrer || null,
      utm_source: new URLSearchParams(location.search).get('utm_source'),
      utm_medium: new URLSearchParams(location.search).get('utm_medium'),
      utm_campaign: new URLSearchParams(location.search).get('utm_campaign'),
      properties: properties || {}
    };
    fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
      keepalive: true
    }).catch(function () {});
  }

  window.PosaTrack = { event: send };
  send('page_view');
}());
