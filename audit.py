"""Structured operational logs. Never log raw request bodies, audio or transcripts."""
import json
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

async def api_response(operation,response,cfg,**fields):
    info=dict(operation=operation,http_status=response.status,
              request_id=clean(response.headers.get('x-request-id'),cfg),
              **fields)
    if response.status>=400:
        # Only known structured error fields; bounded read, no raw HTML or unknown body.
        try:
            raw=await response.content.read(16385)
            if len(raw)<=16384:
                info.update(error_fields(json.loads(raw),cfg))
            else: info['error_body']='oversized'
        except (ValueError,UnicodeError): info['error_body']='not_json'
        except Exception as exc: info['error_body']=type(exc).__name__
        emit('openai.http.response','ERROR',**info)
    else:
        emit('openai.http.response','INFO',**info)
    return info
