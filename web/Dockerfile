# web（Next.js）用のコンテナ。Cloud Run で動かす（design.md §11）。
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production
# standalone の出力と、静的ファイルだけを持ってくる
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
CMD ["sh", "-c", "HOSTNAME=0.0.0.0 PORT=${PORT:-8080} exec node server.js"]
