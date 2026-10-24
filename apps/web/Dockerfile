# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
# `npm ci` installs exactly the lockfile, so image builds are reproducible.
RUN npm ci
COPY apps/web .
RUN npm run build

FROM nginx:1.27-alpine
# Run as an unprivileged user. nginx needs to write its pid/temp paths, so those
# are relocated and the listening port stays 80 inside the container (published
# as 5173 by compose).
RUN rm -f /etc/nginx/conf.d/default.conf \
 && mkdir -p /var/cache/nginx /var/run \
 && chown -R nginx:nginx /var/cache/nginx /var/run /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/supercoder.conf
COPY --from=build /web/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /usr/share/nginx/html
USER nginx
EXPOSE 80
