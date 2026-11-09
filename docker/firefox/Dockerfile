FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/browser \
    DISPLAY=:1 \
    XAUTHORITY=/home/browser/.Xauthority \
    MERIDIAN_BROWSER_DIAGNOSTICS_DIR=/home/browser/.meridian/diagnostics \
    MOZ_CRASHREPORTER=1 \
    MOZ_CRASHREPORTER_NO_REPORT=1 \
    MOZ_CRASHREPORTER_SHUTDOWN=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        dbus-x11 \
        firefox-esr \
        fonts-dejavu-core \
        fonts-noto-color-emoji \
        gosu \
        novnc \
        openbox \
        procps \
        tigervnc-standalone-server \
        websockify \
        xdotool \
        x11-utils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin browser \
    && mkdir -p /home/browser/Downloads /home/browser/.config /home/browser/.mozilla \
        /home/browser/.meridian/diagnostics \
        /etc/firefox-esr/policies \
    && chown -R browser:browser /home/browser

COPY policies.json /etc/firefox-esr/policies/policies.json
COPY meridian.html /usr/share/novnc/meridian.html
COPY entrypoint.sh /usr/local/bin/entrypoint
COPY start-session.sh /usr/local/bin/start-session
COPY healthcheck.sh /usr/local/bin/healthcheck

RUN chmod 0755 /usr/local/bin/entrypoint /usr/local/bin/start-session /usr/local/bin/healthcheck

HEALTHCHECK --interval=15s --timeout=8s --start-period=60s --retries=3 \
    CMD ["/usr/local/bin/healthcheck"]

STOPSIGNAL SIGTERM

EXPOSE 6080

ENTRYPOINT ["/usr/local/bin/entrypoint"]
