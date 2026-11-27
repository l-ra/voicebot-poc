"""Structured operational logs. Never log raw request bodies, audio or transcripts."""
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit
import logging
import re
import sys
import traceback
from datetime import datetime, timezone

LOG=logging.getLogger('voicebot')
LEVELS=('DEBUG','INFO','WARNING','ERROR')

def configure(level='INFO'):
    handler=logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOG.handlers[:]=[handler]
    LOG.propagate=False
    LOG.setLevel(level)

def clean(value,cfg):
    if not isinstance(value,str): return None
    secrets=[cfg.get('openai_api_key'),cfg.get('operator_token'),cfg.get('quiz_code')]
    sip=cfg.get('sip') or {}
    secrets.extend([sip.get('password'),sip.get('username'),sip.get('caller_number')])
    secrets.extend(sip.get('allowed_destinations') or [])
    secrets.append((cfg.get('sip_inbound') or {}).get('webhook_secret'))
    for secret in sorted({s for s in secrets if isinstance(s,str) and s},key=len,reverse=True):
        value=value.replace(secret,'[REDACTED]')
    value=re.sub(r'(?i)\b(?:sk|whsec)[_-][A-Za-z0-9*_.+/=-]+','[REDACTED]',value)
    value=re.sub(r'(?i)\bBearer\s+\S+','Bearer [REDACTED]',value)
    value=re.sub(r'(?i)(?:sips?|tel):[^\s,;]+','[REDACTED URI]',value)
    value=re.sub(r'(?<!\w)\+?\d(?:[ .()-]?\d){6,}(?!\w)','[REDACTED NUMBER]',value)
    return value[:1200]

def emit(event,level='INFO',**fields):
    LOG.log(getattr(logging,level),json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),
        level=level,event=event,**fields),ensure_ascii=False,default=str))

def failure(event,exc,**fields):
    # Frames without source lines, local variables or exception strings (which may contain secrets).
    emit(event,'ERROR',exception=type(exc).__name__,
         frames=[f'{f.name}:{f.lineno}' for f in traceback.extract_tb(exc.__traceback__)][-10:],**fields)

def error_fields(body,cfg):
    if not isinstance(body,dict): return {}
    error=body.get('error',body)
    if not isinstance(error,dict): return {}
    result={}
    for key in ('type','code','param','message'):
        value=clean(error.get(key),cfg)
        if value is not None: result['error_'+key]=value
    return result

SAFE_HEADERS=('server','content-type','content-length','date','cf-ray','via','x-request-id',
              'openai-processing-ms','openai-version','retry-after','x-envoy-upstream-service-time',
              'x-cache','cf-cache-status','x-amzn-trace-id','x-ms-request-id')

def safe_url(url,cfg):
    if url is None: return None
    try:
        p=urlsplit(str(url))
        # Never log userinfo, query strings or fragments.
        host=p.hostname or ''
        if p.port: host+=f':{p.port}'
        return clean(urlunsplit((p.scheme,host,p.path,'','')),cfg)
    except ValueError: return '[invalid URL]'

def response_headers(headers,cfg):
    # aiohttp headers are case-insensitive; plain dicts in tests may not be.
    h={str(k).lower():v for k,v in headers.items()}
    result={k:clean(h[k],cfg) for k in SAFE_HEADERS if k in h}
    if 'location' in h: result['location']=safe_url(h['location'],cfg)
    return result

async def bounded_read(stream,limit=16384):
    chunks=[]; remaining=limit+1
    while remaining:
        chunk=await stream.read(remaining)
        if not chunk: break
        chunks.append(chunk); remaining-=len(chunk)
    data=b''.join(chunks)
    return data[:limit],len(data)>limit

async def api_response(operation,response,cfg,**fields):
    headers=response_headers(response.headers,cfg)
    info=dict(operation=operation,http_status=response.status,
              request_id=headers.get('x-request-id'),
              response_url=safe_url(getattr(response,'url',None),cfg),
              response_headers=headers,
              redirects=[dict(status=r.status,url=safe_url(r.url,cfg),
                              headers=response_headers(r.headers,cfg))
                         for r in getattr(response,'history',())],
              **fields)
    if response.status>=300:
        try:
            raw,truncated=await bounded_read(response.content)
            info.update(error_body_bytes=len(raw),error_body_truncated=truncated,
                        error_body_prefix_sha256=hashlib.sha256(raw).hexdigest())
            try:
                parsed=json.loads(raw)
                info.update(error_fields(parsed,cfg))
                info['error_body']='json'
            except (ValueError,UnicodeError):
                info['error_body']='not_json'
                # Explicit opt-in; gateway response could echo unknown sensitive data.
                if cfg.get('diagnostic_body_preview',False):
                    info['error_body_preview']=clean(raw.decode('utf-8',errors='replace'),cfg)
        except Exception as exc:
            info['error_body']=type(exc).__name__
        emit('openai.http.response','ERROR',**info)
    else:
        emit('openai.http.response','INFO',**info)
    return info
