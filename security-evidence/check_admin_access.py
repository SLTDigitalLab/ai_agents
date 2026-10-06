"""Read-only, anonymous comparison of the two authorized admin stats endpoints."""
import datetime
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


parser = argparse.ArgumentParser()
parser.add_argument('--endpoint', choices=['dashboard/stats', 'ingestion-status'], default='dashboard/stats')
args = parser.parse_args()
results = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
for name, base in [
    ('Staging', 'https://theaisle.raccoon-ai.io'),
    ('Production', 'https://aiagents.sltdigitallab.lk'),
]:
    url = base + '/api/v1/admin/' + args.endpoint
    item = {'environment': name, 'url': url,
            'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'method': 'GET', 'credentials_sent': False}
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Ask-SLT-Authorized-Admin-Access-Check', 'Accept': 'application/json'})
    try:
        with opener.open(req, timeout=30) as response:
            item['status'] = response.status
            body = response.read(262144).decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        item['status'] = exc.code
        body = exc.read(262144).decode('utf-8', 'replace')
    except Exception as exc:
        item['error'] = str(exc)
        results.append(item)
        continue
    try:
        data = json.loads(body)
        item['response_keys'] = sorted(data.keys()) if isinstance(data, dict) else []
        item['dashboard_data_returned'] = (isinstance(data, dict)
            and isinstance(data.get('total_sessions'), int) and isinstance(data.get('agents'), list))
        if args.endpoint == 'ingestion-status':
            item['ingestion_status_returned'] = isinstance(data, dict) and isinstance(data.get('active'), bool)
        if item['dashboard_data_returned']:
            item['total_sessions'] = data['total_sessions']
            item['agent_count'] = data.get('agent_count')
        if isinstance(data, dict) and isinstance(data.get('detail'), str):
            item['detail'] = data['detail'][:200]
    except ValueError:
        item['dashboard_data_returned'] = False
        item['response_format'] = 'Not JSON'
    results.append(item)

output = json.dumps(results, indent=2)
filename = 'admin-access-comparison.json' if args.endpoint == 'dashboard/stats' else 'admin-ingestion-status-comparison.json'
Path(__file__).with_name(filename).write_text(output, encoding='utf-8')
print(output)
