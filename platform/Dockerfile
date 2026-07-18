# MATSO Frontend（M0-3）— Nuxt 4 SSR
FROM node:22-alpine AS build
WORKDIR /app
# lock 由 npm 11 產生；node:22-alpine 內建 npm 10 對 lock 樹解讀不同會 EUSAGE，先對齊版本
RUN npm install -g npm@11
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=build /app/.output .output
ENV NITRO_HOST=0.0.0.0 NITRO_PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=10s \
  CMD wget -qO /dev/null http://127.0.0.1:3000/ || exit 1
CMD ["node", ".output/server/index.mjs"]
