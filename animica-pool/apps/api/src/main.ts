import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import { ValidationPipe } from "@nestjs/common";
import helmet from "helmet";
import cookieParser from "cookie-parser";
import { AppModule } from "./app.module";
import { env } from "./config/env";

async function bootstrap() {
  const e = env();
  const app = await NestFactory.create(AppModule, { bodyParser: true });
  app.use(helmet());
  app.use(cookieParser());
  app.enableCors({
    origin: [e.NEXT_PUBLIC_APP_URL, "http://localhost:3000"],
    credentials: true,
  });
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
  await app.listen(e.API_PORT, "0.0.0.0");
  // eslint-disable-next-line no-console
  console.log(`[animica-pool-api] listening on :${e.API_PORT}`);
}

bootstrap();
