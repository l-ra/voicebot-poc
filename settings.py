"""Local-only runtime settings; never serialize them to clients."""
import base64
import binascii
import json
import os
import re
from pathlib import Path

PHONE = re.compile(r'\+[1-9][0-9]{6,14}\Z')
def load(path=None):
    raw=json.loads(Path(path or os.getenv('VOICEBOT_CONFIG','config.json')).read_text())
    # Explicit deployment configuration overrides the JSON origin, never request headers.
    if 'VOICEBOT_PUBLIC_ORIGIN' in os.environ:
        raw['public_origin'] = os.environ['VOICEBOT_PUBLIC_ORIGIN']
    for key in ('openai_api_key','operator_token','quiz_code','public_origin'):
        if not isinstance(raw.get(key),str) or not raw[key]: raise ValueError(f'Missing {key}')
    if len(raw['operator_token'])<24: raise ValueError('operator_token must have at least 24 characters')
    if not re.fullmatch(r'[0-9]{4}',raw['quiz_code']): raise ValueError('quiz_code must be four digits')
    if not re.fullmatch(r'https?://[^/]+',raw['public_origin']): raise ValueError('public_origin must be an origin without path')
    raw.setdefault('log_level','INFO')
    if raw['log_level'] not in ('DEBUG','INFO','WARNING','ERROR'): raise ValueError('Invalid log_level')
    raw.setdefault('model','gpt-live-1.1'); raw.setdefault('voice','marin')
    raw.setdefault('max_call_seconds',300)
    if type(raw['max_call_seconds']) is not int or not 30<=raw['max_call_seconds']<=600: raise ValueError('max_call_seconds must be 30–600')
    sip=raw.get('sip')
    if sip:
        if not re.fullmatch(r'sips:[A-Za-z0-9.-]+(?::[0-9]+)?(?:;transport=tcp)?',sip.get('provider_url','')): raise ValueError('Invalid SIP provider URL')
        if not PHONE.fullmatch(sip.get('caller_number','')): raise ValueError('Invalid caller number')
        if not all(isinstance(sip.get(k),str) and sip[k] for k in ('username','password')): raise ValueError('Missing SIP credentials')
        allowed=sip.get('allowed_destinations',[])
        if not isinstance(allowed,list) or not allowed or not all(isinstance(n,str) and PHONE.fullmatch(n) for n in allowed): raise ValueError('Configure allowed_destinations explicitly')
    inbound=raw.get('sip_inbound',{})
    if not isinstance(inbound,dict) or type(inbound.get('enabled',False)) is not bool:
        raise ValueError('sip_inbound must be an object with boolean enabled')
    if inbound.get('enabled'):
        secret=inbound.get('webhook_secret','')
        try:
            if not isinstance(secret,str) or not secret.startswith('whsec_') or len(base64.b64decode(secret[6:],validate=True))<16:
                raise ValueError()
        except (ValueError,binascii.Error):
            raise ValueError('Invalid sip_inbound.webhook_secret') from None
        if not raw['public_origin'].startswith('https://'):
            raise ValueError('Inbound SIP requires a public HTTPS origin')
    raw['sip_inbound']=inbound
    return raw

def transport(data, cfg):
    mode=data.get('mode','webrtc')
    if mode=='webrtc':
        sdp=data.get('sdp','')
        if not isinstance(sdp,str) or not sdp.startswith('v=0') or len(sdp)>100000: raise ValueError('Invalid SDP')
        return dict(type='webrtc',sdp=sdp)
    if mode!='sip': raise ValueError('Unknown transport')
    sip=cfg.get('sip')
    if not sip: raise ValueError('SIP not configured')
    number=data.get('destination','')
    if not isinstance(number,str) or not PHONE.fullmatch(number) or number not in sip['allowed_destinations']: raise ValueError('Destination is not allowed')
    return dict(type='sip',destination=number,trunk=dict(provider_url=sip['provider_url'],caller_number=sip['caller_number'],auth=dict(type='digest',username=sip['username'],password=sip['password'])))
