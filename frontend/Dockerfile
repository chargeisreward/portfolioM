# ─── Build stage ───
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --silent || npm install --silent
COPY . .
# VITE_BASE: production base path (default '/' for root, '/portfoliom/' for cloud)
ARG VITE_BASE=/
ENV VITE_BASE=$VITE_BASE
RUN npm run build

# ─── Serve stage ───
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
