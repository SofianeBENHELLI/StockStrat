# StockStrat web interface. /api is forwarded to the backend service; the
# address is baked in at build time because Next computes rewrites then.
FROM node:22-alpine AS build
WORKDIR /app
ARG BACKEND_URL=http://api:8001
ENV BACKEND_URL=$BACKEND_URL NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/package.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.mjs ./
USER node
EXPOSE 3001
CMD ["npx", "next", "start", "-p", "3001"]
