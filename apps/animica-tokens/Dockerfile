FROM node:20-alpine AS build
WORKDIR /app

COPY apps/animica-tokens/package.json package.json
RUN npm install

COPY apps/animica-tokens/ .
RUN npm run build

EXPOSE 5182
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5182"]
