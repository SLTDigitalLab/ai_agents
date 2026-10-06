"""Small authorized production audit: GET only, no credentials, no real record IDs."""
import datetime
import json
from pathlib import Path
import urllib.request
import urllib.error
import uuid

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

base = 'https://aiagents.sltdigitallab.lk'
marker = 'security-audit-' + str(uuid.uuid4())
paths = ['/', '/@vite/client', '/api/openapi.json',
         '/api/v1/admin/ingestion-status', '/api/v1/admin/dashboard/stats',
         '/api/v1/helpdesk_dev/tickets?userId=' + marker + '&limit=1',
         '/api/v1/feedback/hr/' + marker,
         '/api/v1/chat/hr/' + marker]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
results = []
for path in paths:
    item = {'path': path, 'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'credentials_sent': False, 'method': 'GET'}
    try:
        request = urllib.request.Request(base + path, headers={
            'User-Agent': 'Ask-SLT-Authorized-ReadOnly-Security-Audit', 'Accept': '*/*'})
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            item['status'] = response.code
            item['content_type'] = response.headers.get('Content-Type')
            item['headers_present'] = {h: bool(response.headers.get(h)) for h in
                ['Strict-Transport-Security', 'Content-Security-Policy', 'X-Content-Type-Options', 'X-Frame-Options']}
            body = response.read(524288).decode('utf-8', 'replace')
        if path == '/':
            item['vite_dev_client_referenced'] = '/@vite/client' in body
        elif path == '/@vite/client':
            item['vite_client_code'] = 'WebSocket' in body and 'text/html' not in (item['content_type'] or '')
        else:
            try:
                data = json.loads(body)
                item['response_keys'] = sorted(data.keys()) if isinstance(data, dict) else []
                if isinstance(data, dict):
                    if path == '/api/openapi.json':
                        item['registered_routes'] = {p: list(v.keys()) for p, v in data.get('paths', {}).items()}
                    if 'tickets' in data:
                        item['returned_ticket_count'] = len(data['tickets'])
                    if 'feedback' in data:
                        item['returned_feedback_entry_count'] = len(data['feedback'])
                    if data.get('detail') in ['Not authenticated', 'Invalid token', 'Invalid or expired token']:
                        item['detail'] = data['detail']
            except ValueError:
                item['json'] = False
    except Exception as exc:
        item['error_type'] = type(exc).__name__
    results.append(item)
    print(json.dumps({k:v for k,v in item.items() if k != 'registered_routes'}), flush=True)
Path(__file__).with_name('production-readonly-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
