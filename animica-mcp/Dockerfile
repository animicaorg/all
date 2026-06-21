FROM node:20-slim
WORKDIR /app
COPY package.json tsconfig.json ./
RUN npm install
COPY src ./src
RUN npm run build && npm prune --omit=dev
# Hosted remote MCP by default in the container.
ENV MCP_TRANSPORT=http
ENV PORT=8765
EXPOSE 8765
CMD ["node", "dist/server.js"]
