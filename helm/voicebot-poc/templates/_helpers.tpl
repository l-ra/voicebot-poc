{{- define "voicebot.ingressHostname" -}}
{{- $host := required "ingress.hostname is required when ingress is enabled" .Values.ingress.hostname -}}
{{- if or (gt (len $host) 253) (not (regexMatch "^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$" $host)) -}}
{{- fail "ingress.hostname must be a lowercase DNS hostname without scheme, wildcard, port or path" -}}
{{- end -}}
{{- range splitList "." $host -}}
{{- if gt (len .) 63 -}}{{- fail "ingress.hostname DNS label exceeds 63 characters" -}}{{- end -}}
{{- end -}}
{{- $host -}}
{{- end -}}
