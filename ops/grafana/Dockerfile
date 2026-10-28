# linux/amd64 monitoring artifact. No runtime plugin download or credentials.
# Security rebuild: official v13.2.5 source, fixed go-jose, existing official UI.
FROM docker.io/library/golang:1.27.3-bookworm@sha256:05237276236793ebedaf0b87dab86add9ec9adc66ebef2dbc3107c18254cde1c AS server
ENV GOTOOLCHAIN=local GOPROXY=https://goproxy.cn|https://proxy.golang.org|direct GOSUMDB=sum.golang.org CGO_ENABLED=0 GOMAXPROCS=2
ADD --checksum=sha256:f3ca3f7100cedf7ce454824e871943768f23714140390e3e65f4188715f8a105 https://codeload.github.com/grafana/grafana/tar.gz/ce471d869a4810cd83c987dd3521c4b67689dbb6 /source.tar.gz
WORKDIR /source
RUN tar -xzf /source.tar.gz --strip-components=1
RUN go get github.com/go-jose/go-jose/v4@v4.1.6 && go mod verify
RUN go build -p 2 -tags oss -trimpath -buildvcs=false -ldflags='-buildid= -X main.version=13.2.5+cs.s2 -X main.commit=ce471d869a4810cd83c987dd3521c4b67689dbb6 -X main.buildBranch=cs-s2-security -X main.buildstamp=0' -o /out/grafana ./pkg/cmd/grafana

FROM docker.io/library/python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS plugin
ADD --checksum=sha256:d24df3eaff9ae61f00d7e857f508fc306912dcf50d657150d1e472e851799fc4 https://grafana.com/api/plugins/prometheus/versions/13.2.5/download?os=linux&arch=amd64 /plugin.zip
RUN python -m zipfile -e /plugin.zip /plugins && chmod 0555 /plugins/prometheus/gpx_grafana-prometheus-datasource_linux_amd64

FROM grafana/grafana:13.2.5-distroless-slim@sha256:84157cef34688322cd542b3137f4c6928d1f491143da4228be1f764aa643de52
COPY --from=server /out/grafana /usr/share/grafana/bin/grafana
COPY --from=server /source/go.mod /source/go.sum /source/go.work /source/go.work.sum /source/LICENSE /usr/share/customer-service-grafana/
COPY --from=plugin --chown=472:0 /plugins/prometheus /opt/customer-service-plugins/prometheus
ENV GF_PATHS_PLUGINS=/opt/customer-service-plugins \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
USER 472
