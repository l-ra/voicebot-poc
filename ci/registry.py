"""Authenticated GHCR checks. Fail closed on unexpected registry responses."""
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
import base64

def request(url, headers):
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as resp:
        return json.load(resp), resp.headers

def token(repo):
    auth=base64.b64encode((os.environ['GITHUB_ACTOR']+':'+os.environ['GH_TOKEN']).encode()).decode()
    url='https://ghcr.io/token?'+urllib.parse.urlencode(dict(service='ghcr.io',scope=f'repository:{repo}:pull'))
    data,_=request(url,{'Authorization':'Basic '+auth})
    return data['token']

def tags(image):
    if not image.startswith('ghcr.io/'): raise ValueError('Expected GHCR')
    repo=image.removeprefix('ghcr.io/')
    headers={'Authorization':'Bearer '+token(repo)}
    url=f'https://ghcr.io/v2/{repo}/tags/list?n=100'
    result=set()
    while url:
        try: data,h=request(url,headers)
        except urllib.error.HTTPError as e:
            if e.code==404:
                # A missing repository is distinct from a denied or unavailable registry.
                body=json.loads(e.read())
                if any(x.get('code')=='NAME_UNKNOWN' for x in body.get('errors',[])): return set()
            raise
        result.update(data.get('tags') or [])
        link=h.get('Link','')
        match=re.search(r'<([^>]+)>;\s*rel="?next"?',link)
        url=urllib.parse.urljoin(url,match.group(1)) if match else None
        if url and urllib.parse.urlparse(url).netloc!='ghcr.io': raise ValueError('Unexpected pagination host')
    return result

def stable(v):
    if re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',v):
        return tuple(map(int,v.split('.')))
    return None

if __name__=='__main__':
    command,*args=sys.argv[1:]
    if command=='absent':
        for ref in args:
            image,tag=ref.rsplit(':',1)
            if tag in tags(image): raise SystemExit(f'Refusing to overwrite {ref}')
    elif command=='newest':
        image,version=args
        current=stable(version)
        if current is None: raise ValueError('Expected stable version')
        if any(v is not None and v>current for v in map(stable,tags(image))): sys.exit(3)
    else: raise ValueError('Unknown command')
