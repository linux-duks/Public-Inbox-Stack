{{/*
Expand the name of the chart.
*/}}
{{- define "public-inbox-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "public-inbox-stack.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "public-inbox-stack.labels" -}}
app.kubernetes.io/name: {{ include "public-inbox-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Name of the image pull Secret created by the chart (when
imagePullSecrets.create.enabled is true).
*/}}
{{- define "public-inbox-stack.imagePullSecretName" -}}
{{- $create := .Values.imagePullSecrets.create }}
{{- default (printf "%s-image-pull" (include "public-inbox-stack.fullname" .)) $create.name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
imagePullSecrets list for a pod: the chart-created Secret (if enabled) plus
any pre-existing Secret names from imagePullSecrets.existing. Renders nothing
when empty.
*/}}
{{- define "public-inbox-stack.imagePullSecrets" -}}
{{- $secrets := list }}
{{- if .Values.imagePullSecrets.create.enabled }}
{{- $secrets = append $secrets (dict "name" (include "public-inbox-stack.imagePullSecretName" .)) }}
{{- end }}
{{- range .Values.imagePullSecrets.existing }}
{{- $secrets = append $secrets (dict "name" .) }}
{{- end }}
{{- with $secrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Selector labels for the main app
*/}}
{{- define "public-inbox-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "public-inbox-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Selector labels for anubis
*/}}
{{- define "public-inbox-stack.anubisSelectorLabels" -}}
app.kubernetes.io/name: {{ include "public-inbox-stack.name" . }}-anubis
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
