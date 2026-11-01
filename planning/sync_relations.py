"""Run in GitHub Actions to attach and verify native issue blocking links."""
import base64,json,os,re,time,urllib.request,urllib.error

REPO=os.environ['REPOSITORY']
TOKEN=os.environ['GH_TOKEN']
COMMIT=os.environ['PLAN_COMMIT']
BASE='https://api.github.com/repos/'+REPO

def api(path,method='GET',data=None):
    request=urllib.request.Request(BASE+path,method=method,
        headers={'Authorization':'Bearer '+TOKEN,'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2026-03-10','Content-Type':'application/json'},
        data=json.dumps(data).encode() if data is not None else None)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request,timeout=60) as response:
                result=json.load(response)
            if method!='GET':time.sleep(1.2)
            return result
        except urllib.error.HTTPError as error:
            detail=error.read().decode('utf-8','replace')
            temporary=error.code in (429,500,502,503,504) or (error.code==403 and 'rate limit' in detail.lower())
            if not temporary or attempt==3:raise RuntimeError(f'{method} {path}: HTTP {error.code}: {detail[:500]}') from None
            time.sleep(max(int(error.headers.get('Retry-After','0')),30*(attempt+1)))

file=api('/contents/planning/published-tickets.json?ref='+COMMIT)
plan=json.loads(base64.b64decode(file['content']))
rows=plan['tickets']
assert len({x['number'] for x in rows})==len(rows)
numbers={x['id']:x['number'] for x in rows}
snapshots={}
page=1
while True:
    result=api(f'/issues?state=all&per_page=100&page={page}')
    for issue in result:
        if 'pull_request' not in issue:snapshots[issue['number']]=issue
    if len(result)<100:break
    page+=1
assert all(n in snapshots for n in numbers.values())
edge_count=0
for row in rows:
    number=row['number'];issue=snapshots[number]
    marker='<!-- orchara-tracer-v1:'+row['id']+' -->'
    assert marker in issue['body'],f'Issue identity mismatch: {number}'
    expected={numbers[b] for b in row['blockers']}
    existing=set();page=1
    while True:
        result=api(f'/issues/{number}/dependencies/blocked_by?per_page=100&page={page}')
        existing.update(x['number'] for x in result)
        if len(result)<100:break
        page+=1
    for blocker in sorted(expected-existing):
        api(f'/issues/{number}/dependencies/blocked_by','POST',{'issue_id':snapshots[blocker]['id']})
    verified={x['number'] for x in api(f'/issues/{number}/dependencies/blocked_by?per_page=100')}
    assert expected<=verified,(number,expected,verified)
    edge_count+=len(expected)
    # Remove only our exact textual fallback once native edges are verified.
    latest=api(f'/issues/{number}')
    text=latest['body'];assert marker in text
    match=re.search(r'\n## Blocked by\n.*?(?=\n## |\Z)',text,flags=re.S)
    if match:
        listed={int(n) for n in re.findall(r'#(\d+)',match.group())}
        assert listed==expected,f'Blocking text edited concurrently: {number}'
        api(f'/issues/{number}','PATCH',{'body':text[:match.start()]+text[match.end():]})
    print(f'VERIFIED {row["id"]} #{number}: {len(expected)} native blockers',flush=True)
summary=f'Verified {len(rows)} published tickets and {edge_count} native blocking edges. Text fallback removed only after verification.'
print(summary,flush=True)
with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as out:out.write(summary+'\n')
