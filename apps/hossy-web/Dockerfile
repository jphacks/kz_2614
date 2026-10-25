FROM node:24-bookworm-slim AS build

WORKDIR /workspace/apps/hossy-web
COPY apps/hossy-web/package.json apps/hossy-web/package-lock.json ./
RUN npm ci

COPY apps/hossy-web/ ./
COPY apps/web/voice_call.js apps/web/voice_pcm_worklet.js apps/web/voice_pcm_output_worklet.js /workspace/apps/web/
ENV ASTRO_TELEMETRY_DISABLED=1
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /workspace/apps/hossy-web/dist/ /usr/share/nginx/html/
COPY apps/hossy-web/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY apps/hossy-web/nginx/40-voice-proxy.sh /docker-entrypoint.d/40-voice-proxy.sh
RUN chmod +x /docker-entrypoint.d/40-voice-proxy.sh

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
