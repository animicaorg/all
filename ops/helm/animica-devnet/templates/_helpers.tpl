{{/*
  Animica Devnet — shared Helm helpers
  These helpers are used across node/miner/explorer/services/studio UIs templates.

  Conventions:
  - Call selector/labels helpers with a dict: {"ctx": . , "component": "node"} to keep root context.
  - Image helper expects: include "animica-devnet.image" (dict "ctx" . "image" .Values.node.image)
*/}}

{{/* ----------------------------------------------------------------------------
Name helpers
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "animica-devnet.fullname" -}}
{{- $name := include "animica-devnet.name" . -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "animica-devnet.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" -}}
{{- end -}}

{{/* ----------------------------------------------------------------------------
Label sets
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.selectorLabels" -}}
{{- $ctx := .ctx -}}
{{- $component := .component | default "app" -}}
app.kubernetes.io/name: {{ include "animica-devnet.name" $ctx }}
app.kubernetes.io/instance: {{ $ctx.Release.Name }}
app.kubernetes.io/component: {{ $component }}
app.kubernetes.io/part-of: animica
{{- end -}}

{{- define "animica-devnet.labels" -}}
{{- $ctx := .ctx -}}
{{- $component := .component | default "app" -}}
helm.sh/chart: {{ include "animica-devnet.chart" $ctx }}
app.kubernetes.io/managed-by: {{ $ctx.Release.Service }}
app.kubernetes.io/version: {{ $ctx.Chart.AppVersion | default "v0" | quote }}
{{ include "animica-devnet.selectorLabels" (dict "ctx" $ctx "component" $component) }}
{{- end -}}

{{/* ----------------------------------------------------------------------------
ServiceAccount name
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.serviceAccountName" -}}
{{- $ctx := . -}}
{{- if $ctx.Values.serviceAccount.create -}}
{{- if $ctx.Values.serviceAccount.name -}}
{{- $ctx.Values.serviceAccount.name -}}
{{- else -}}
{{ include "animica-devnet.fullname" $ctx }}
{{- end -}}
{{- else -}}
default
{{- end -}}
{{- end -}}

{{/* ----------------------------------------------------------------------------
Image reference & pull secrets
Usage:
  {{ include "animica-devnet.image" (dict "ctx" . "image" .Values.node.image) }}
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.image" -}}
{{- $ctx := .ctx -}}
{{- $img := .image -}}
{{- $registry := (coalesce $img.registry $ctx.Values.global.image.registry) -}}
{{- $repo := (coalesce $img.repository "missing-repo") -}}
{{- $tag := (coalesce $img.tag $ctx.Chart.AppVersion "latest") -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "animica-devnet.imagePullSecrets" -}}
{{- $ctx := . -}}
{{- $secrets := $ctx.Values.global.image.pullSecrets | default (list) -}}
{{- if $secrets }}
imagePullSecrets:
{{- range $s := $secrets }}
  - name: {{ $s | quote }}
{{- end }}
{{- end -}}
{{- end -}}

{{/* ----------------------------------------------------------------------------
ServiceMonitor label merger (adds chart/app labels on top of user-provided)
Usage:
  metadata:
    labels:
{{ include "animica-devnet.servicemonitor.labels" (dict "ctx" . "labels" .Values.node.serviceMonitor.labels) | indent 6 }}
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.servicemonitor.labels" -}}
{{- $ctx := .ctx -}}
{{- $user := .labels | default dict -}}
{{- $base := dict "release" "prometheus" -}}
{{- $merged := merge $base $user -}}
{{- range $k, $v := $merged -}}
{{ $k }}: {{ $v | quote }}
{{- end -}}
{{ include "animica-devnet.selectorLabels" (dict "ctx" $ctx "component" "metrics") }}
{{- end -}}

{{/* ----------------------------------------------------------------------------
Tiny utils
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.nindent" -}}
{{- /* like nindent via include: include "animica-devnet.nindent" (dict "n" 4 "str" "key: val\n") */ -}}
{{- $n := .n -}}
{{- $s := .str -}}
{{- nindent $n $s -}}
{{- end -}}

{{- define "animica-devnet.tplvalues.render" -}}
{{- /* Render a value as a template with the root context */ -}}
{{- $root := .root -}}
{{- $value := .value -}}
{{- tpl $value $root -}}
{{- end -}}

{{/* ----------------------------------------------------------------------------
Port name sanitizer (DNS-1123 compliant, ≤15 chars recommended)
Usage: {{ include "animica-devnet.portName" "p2p-quic" }}
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.portName" -}}
{{- $name := . | lower | replace "_" "-" | trunc 15 | trimSuffix "-" -}}
{{- regexReplaceAll "[^a-z0-9-]" $name "-" -}}
{{- end -}}

{{/* ----------------------------------------------------------------------------
Ingress host resolver
Usage:
  {{- $host := include "animica-devnet.ingressHost" (dict "ctx" . "key" "studioWeb") -}}
---------------------------------------------------------------------------- */}}
{{- define "animica-devnet.ingressHost" -}}
{{- $ctx := .ctx -}}
{{- $key := .key -}}
{{- $hosts := $ctx.Values.ingress.hosts -}}
{{- $entry := index $hosts $key | default dict -}}
{{- $host := (get $entry "host") | default "" -}}
{{- $host -}}
{{- end -}}
