/** A namespace with nested declarations, to exercise the namespaces{} collection + nested signatures. */
export namespace StringUtil {
  export function repeat(s: string, n: number): string {
    return slug(s).repeat(n);
  }

  export function slug(s: string): string {
    return s.toLowerCase().replace(/\s+/g, "-");
  }

  export class Builder {
    private parts: string[] = [];
    add(part: string): this {
      this.parts.push(slug(part));
      return this;
    }
    build(): string {
      return this.parts.join("/");
    }
  }
}

/** Generic top-level function with a nested helper, to exercise inner_callables + generics. */
export function classify<T extends { name: string }>(items: T[]): Record<string, T[]> {
  function keyOf(item: T): string {
    return item.name.charAt(0);
  }
  const out: Record<string, T[]> = {};
  for (const item of items) {
    const k = keyOf(item);
    (out[k] ??= []).push(item);
  }
  return out;
}
