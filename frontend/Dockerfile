# ─── Build stage ───
FROM node:20-alpine AS builder
WORKDIR /app
# Use npmmirror (taobao) for npm package fetch — registry.npmjs.org too slow from China
RUN npm config set registry https://registry.npmmirror.com
COPY package.json package-lock.json* ./
RUN npm ci --silent || npm install --silent
COPY . .
# VITE_BASE: production base path (default '/' for root, '/portfoliom/' for cloud)
ARG VITE_BASE=/
ENV VITE_BASE=$VITE_BASE
# 删除 .env.production（在 COPY 之后、build 之前）— 否则 Vite build 会自动 load 它并注入
# zeabur 部署走 zeabur.json 的 build_command（动态写入正确 URL），不受影响
RUN rm -f .env.production
RUN npm run build

# ─── Serve stage ───
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
