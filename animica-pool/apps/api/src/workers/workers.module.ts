import { Module } from "@nestjs/common";
import { WorkersController, WorkerMachineController } from "./workers.controller";
import { WorkersService } from "./workers.service";

@Module({
  controllers: [WorkersController, WorkerMachineController],
  providers: [WorkersService],
  exports: [WorkersService],
})
export class WorkersModule {}
