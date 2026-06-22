import { UserService } from "./services";

// Minimal decorator factories (NestJS/Angular-flavored) to exercise structured TSDecorator capture.
function Controller(prefix: string): ClassDecorator {
  return () => undefined;
}
function Get(path: string): MethodDecorator {
  return () => undefined;
}
function Param(name: string): ParameterDecorator {
  return () => undefined;
}

@Controller("/users")
export class UserController {
  constructor(private readonly service: UserService) {}

  @Get("/:id")
  show(@Param("id") id: string): string {
    const user = this.service.create(id);
    return user.describe();
  }

  @Get("/")
  list(): string[] {
    return this.service.describeAll();
  }
}
