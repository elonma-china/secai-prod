# Sidecar specialist-tunnel (stack/compose.yml). Dựng trên máy đích, uid = chủ tệp khoá ssh:
#   docker build --build-arg TUNNEL_UID=$(id -u) -t secai-specialist-tunnel:<ngày> stack/images/tunnel
FROM alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
ARG TUNNEL_UID=10002
RUN apk add --no-cache openssh-client-default=10.3_p1-r2 socat=1.8.1.3-r0 \
 && adduser -D -H -h /tmp -s /sbin/nologin -u "$TUNNEL_UID" tunnel
COPY entrypoint.sh /usr/local/bin/tunnel-entrypoint
COPY health.sh /usr/local/bin/tunnel-health
ENV HOME=/tmp
USER ${TUNNEL_UID}
ENTRYPOINT ["/usr/local/bin/tunnel-entrypoint"]
