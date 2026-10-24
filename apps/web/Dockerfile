# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
# `npm ci` installs exactly the lockfile, so image builds are reproducible.
RUN npm ci
COPY apps/web .
RUN npm run build

FROM nginx:1.27-alpine
# Run as an unprivileged user. The stock nginx.conf writes /run/nginx.pid and
# temp files under root-owned paths, so a private main config is installed that
# points pid, temp paths, and logs at a directory owned by the nginx user.
# Without this, nginx exits with `open() "/run/nginx.pid" failed (13: Permission
# denied)`.
RUN rm -f /etc/nginx/conf.d/default.conf \
 && mkdir -p /var/lib/nginx/tmp/client_body /var/lib/nginx/tmp/proxy \
            /var/lib/nginx/tmp/fastcgi /var/lib/nginx/tmp/uwsgi \
            /var/lib/nginx/tmp/scgi /var/lib/nginx/logs \
 && chown -R nginx:nginx /var/lib/nginx /var/cache/nginx
COPY apps/web/nginx.main.conf /etc/nginx/nginx.conf
COPY apps/web/nginx.conf /etc/nginx/conf.d/supercoder.conf
COPY --from=build /web/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /usr/share/nginx/html
USER nginx
EXPOSE 80