import { UserController } from "./controllers";
import { Robot, Role, User } from "./models";
import { UserService, announce } from "./services";
import { StringUtil } from "./util";

export function main(): void {
  const service = new UserService(100);
  service.create("Ada", Role.Admin);
  service.createGuest();

  const controller = new UserController(service);
  controller.list();
  controller.show("42");

  // interface-typed dispatch — RTA should expand announce -> {User,Robot}.describe
  announce(new User(1, "Ada", Role.Admin));
  announce(new Robot("r2d2"));

  const slug = StringUtil.repeat("hello world", 2);
  const builder = new StringUtil.Builder();
  builder.add("a").add("b").build();
  console.log(slug);
}

main();
