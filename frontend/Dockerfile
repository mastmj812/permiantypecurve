FROM node:20-alpine

WORKDIR /app

# npm ci installs the EXACT versions in package-lock.json — `npm
# install` floats on the package.json ranges, so a rebuild could bump
# maplibre/react/vite silently. Regenerate the lock deliberately with
# `npm install --package-lock-only` and re-run tsc + vitest.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

EXPOSE 5173
CMD ["npm", "run", "dev"]
