const path = require("path");

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
    directory: path.join(__dirname, "src", "migrations")
  },
  seeds: {
    directory: path.join(__dirname, "src", "seeds")
  }
};

module.exports = {
  development: baseConfig,
  production: baseConfig
};
