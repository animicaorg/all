const path = require("path");

// Validate required environment variables
const requiredEnvVars = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"];
const missingVars = requiredEnvVars.filter(varName => !process.env[varName]);

if (missingVars.length > 0) {
  console.error("\n❌ Error: Missing required environment variables for database connection:");
  missingVars.forEach(varName => {
    console.error(`   - ${varName}`);
  });
  console.error("\nPlease ensure these variables are set in your environment or .env file.");
  console.error("For Docker Compose: Check that cex/ops/env/.env exists and is loaded.\n");
  process.exit(1);
}

const baseConfig = {
  client: "pg",
  connection: {
    host: process.env.DB_HOST,
    port: process.env.DB_PORT || 5432,
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    database: process.env.DB_NAME
  },
  migrations: {
    directory: path.join(__dirname, "src", "migrations"),
    loadExtensions: [".cjs"]
  },
  seeds: {
    directory: path.join(__dirname, "src", "seeds"),
    loadExtensions: [".cjs"]
  }
};

module.exports = {
  development: baseConfig,
  production: baseConfig
};
