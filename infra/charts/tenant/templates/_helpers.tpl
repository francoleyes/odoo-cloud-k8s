{{- define "tenant.host" -}}
{{- if .Values.isMain -}}
{{ .Values.baseDomain }}
{{- else -}}
{{ .Values.subdomain | default .Values.name }}.{{ .Values.baseDomain }}
{{- end -}}
{{- end -}}

{{- define "tenant.env" -}}
- name: PGUSER
  valueFrom:
    secretKeyRef: { key: username, name: postgres-app }
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef: { key: username, name: postgres-app }
- name: PGPASSWORD
  valueFrom:
    secretKeyRef: { key: password, name: postgres-app }
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef: { key: password, name: postgres-app }
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef: { key: MINIO_ROOT_USER, name: minio-creds }
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef: { key: MINIO_ROOT_PASSWORD, name: minio-creds }
{{- end -}}

{{- define "tenant.envBlock" -}}
env:
{{ include "tenant.env" . | indent 2 }}
envFrom:
  - configMapRef:
      name: odoo-env
{{- end -}}
