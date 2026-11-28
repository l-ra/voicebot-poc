"""Local-only runtime settings; never serialize them to clients."""
import base64
import binascii
import json
import os
import re
from pathlib import Path

PHONE = re.compile(r'\+[1-9][0-9]{6,14}\Z')
REGION_HOSTS={'global':'api.openai.com','us':'us.api.openai.com','eu':'eu.api.openai.com'}

def openai_endpoints(region):
    if not isinstance(region,str) or region not in REGION_HOSTS:
        raise ValueError('openai_region must be global, us or eu')
    host=REGION_HOSTS[region]
    return (f'https://{host}/v1/live/sessions',f'wss://{host}/v1/live/sessions')

def load(path=None):
    raw=json.loads(Path(path or os.getenv('VOICEBOT_CONFIG','config.json')).read_text())
    # Explicit deployment configuration overrides the JSON origin, never request headers.
    if 'VOICEBOT_PUBLIC_ORIGIN' in os.environ:
        raw['public_origin'] = os.environ['VOICEBOT_PUBLIC_ORIGIN']
    for key in ('openai_api_key','operator_token','public_origin'):
        if not isinstance(raw.get(key),str) or not raw[key]: raise ValueError(f'Missing {key}')
    if len(raw['operator_token'])<24: raise ValueError('operator_token must have at least 24 characters')
    if not re.fullmatch(r'https?://[^/]+',raw['public_origin']): raise ValueError('public_origin must be an origin without path')
    raw.setdefault('openai_region','global')
    openai_endpoints(raw['openai_region'])
    for key in ('diagnostic_models_probe','diagnostic_body_preview'):
        raw.setdefault(key,False)
        if type(raw[key]) is not bool: raise ValueError(f'{key} must be boolean')
    raw.setdefault('log_level','INFO')
    if raw['log_level'] not in ('DEBUG','INFO','WARNING','ERROR'): raise ValueError('Invalid log_level')
    raw.setdefault('model','gpt-live-1.1'); raw.setdefault('voice','marin')
    raw.setdefault('decision_model','gpt-6-luna')
    raw.setdefault('decision_min_confidence',.8)
    if type(raw['decision_min_confidence']) not in (int,float) or not 0<=raw['decision_min_confidence']<=1: raise ValueError('Invalid decision_min_confidence')
    if not isinstance(raw['decision_model'],str) or not raw['decision_model']: raise ValueError('Invalid decision_model')
    raw.setdefault('max_call_seconds',300)
    if type(raw['max_call_seconds']) is not int or not 30<=raw['max_call_seconds']<=600: raise ValueError('max_call_seconds must be 30–600')
    sip=raw.get('sip')
    if sip:
        if not re.fullmatch(r'sips:[A-Za-z0-9.-]+(?::[0-9]+)?(?:;transport=tcp)?',sip.get('provider_url','')): raise ValueError('Invalid SIP provider URL')
        if not PHONE.fullmatch(sip.get('caller_number','')): raise ValueError('Invalid caller number')
        if not all(isinstance(sip.get(k),str) and sip[k] for k in ('username','password')): raise ValueError('Missing SIP credentials')
    # Inbound is disabled in this version. Ignore legacy inbound settings.
    raw['sip_inbound']={'enabled':False}
    raw.pop('quiz_code',None)
    if sip: sip.pop('allowed_destinations',None)
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
    if not isinstance(number,str): raise ValueError('Invalid Czech number')
    number=re.sub(r'[\s()-]','',number)
    if number.startswith('00420'): number='+'+number[2:]
    if re.fullmatch(r'[1-9][0-9]{8}',number): number='+420'+number
    if not re.fullmatch(r'\+420[1-9][0-9]{8}',number): raise ValueError('Only Czech nine-digit telephone numbers are supported')
    return dict(type='sip',destination=number,trunk=dict(provider_url=sip['provider_url'],caller_number=sip['caller_number'],auth=dict(type='digest',username=sip['username'],password=sip['password'])))
