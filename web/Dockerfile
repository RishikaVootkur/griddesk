# Build the React app, then serve it with nginx (as a non-root user) and proxy /api to the GridDesk API.
# The build context is ./web, so web/.dockerignore (not the root one) keeps node_modules and .env out.
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
