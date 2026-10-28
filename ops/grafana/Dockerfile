# linux/amd64 monitoring artifact. No runtime plugin download or credentials.
FROM docker.io/library/python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS plugin
ADD --checksum=sha256:d24df3eaff9ae61f00d7e857f508fc306912dcf50d657150d1e472e851799fc4 https://grafana.com/api/plugins/prometheus/versions/13.2.5/download?os=linux&arch=amd64 /plugin.zip
RUN python -m zipfile -e /plugin.zip /plugins && chmod 0555 /plugins/prometheus/gpx_grafana-prometheus-datasource_linux_amd64

FROM grafana/grafana:13.2.5-distroless-slim@sha256:84157cef34688322cd542b3137f4c6928d1f491143da4228be1f764aa643de52
COPY --from=plugin --chown=472:0 /plugins/prometheus /opt/customer-service-plugins/prometheus
ENV GF_PATHS_PLUGINS=/opt/customer-service-plugins \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
USER 472
