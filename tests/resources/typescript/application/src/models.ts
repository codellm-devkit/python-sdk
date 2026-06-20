/** Domain models for the sample app. */

export interface Identifiable<T = string> {
  readonly id: T;
}

export interface Named {
  name: string;
  describe(): string;
}

export type UserId = string | number;

export enum Role {
  Admin = "admin",
  Member = "member",
  Guest = "guest",
}

export const enum Flag {
  None = 0,
  Active = 1,
}

/** A user of the system. */
export abstract class Entity<ID = string> implements Identifiable<ID> {
  constructor(public readonly id: ID) {}
  abstract describe(): string;
}

export class User extends Entity<UserId> implements Named {
  private loginCount = 0;
  static instances = 0;

  constructor(
    id: UserId,
    public name: string,
    private role: Role = Role.Member,
  ) {
    super(id);
    User.instances++;
  }

  get isAdmin(): boolean {
    return this.role === Role.Admin;
  }

  describe(): string {
    return `${this.name} (${this.role})`;
  }

  async recordLogin(): Promise<number> {
    this.loginCount += 1;
    return this.loginCount;
  }
}

/** A second, unrelated implementer of Named — drives RTA subtype expansion. */
export class Robot implements Named {
  constructor(public name: string) {}
  describe(): string {
    return `robot:${this.name}`;
  }
}
