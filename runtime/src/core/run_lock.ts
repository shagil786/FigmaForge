import * as fs from "node:fs";
import * as path from "node:path";

export class RunLockError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RunLockError";
  }
}

/** Exclusive, recoverable lock for one output-dir/run-id pair. */
export class RunLock {
  private held = false;
  readonly lockPath: string;

  constructor(outputDir: string, runId: string) {
    this.lockPath = path.join(outputDir, runId, ".run.lock");
  }

  acquire(): void {
    fs.mkdirSync(path.dirname(this.lockPath), { recursive: true });
    try {
      const fd = fs.openSync(this.lockPath, "wx");
      fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, runId: path.basename(path.dirname(this.lockPath)), createdAt: new Date().toISOString() }));
      fs.closeSync(fd);
      this.held = true;
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }

    let owner: { pid?: number; createdAt?: string } = {};
    try {
      owner = JSON.parse(fs.readFileSync(this.lockPath, "utf8")) as typeof owner;
    } catch {
      // An incomplete lock is safe to recover because it cannot identify a live owner.
    }
    if (typeof owner.pid === "number") {
      try {
        process.kill(owner.pid, 0);
        throw new RunLockError(`run is already locked by process ${owner.pid}: ${this.lockPath}`);
      } catch (error) {
        if (error instanceof RunLockError) throw error;
        const code = (error as NodeJS.ErrnoException).code;
        if (code !== "ESRCH") {
          throw new RunLockError(
            `cannot verify lock owner ${owner.pid}: ${code ?? "unknown error"}`,
          );
        }
        // ESRCH means the owner is gone; recover the stale lock below.
      }
    }
    fs.rmSync(this.lockPath, { force: true });
    this.acquire();
  }

  release(): void {
    if (!this.held) return;
    fs.rmSync(this.lockPath, { force: true });
    this.held = false;
  }
}
