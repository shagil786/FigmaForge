import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, it, assert, assertEqual } from "./test_framework.js";
import { RunLock, RunLockError } from "../src/core/run_lock.js";

export async function runRunLockTests() {
  return [await describe("run lock", async () => {
    await it("rejects a live competing lock and releases its own lock", async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-lock-"));
      const first = new RunLock(dir, "run-1");
      const second = new RunLock(dir, "run-1");
      try {
        first.acquire();
        let rejected = false;
        try { second.acquire(); } catch (error) {
          rejected = error instanceof RunLockError;
        }
        assert(rejected, "a competing process must be rejected");
        first.release();
        second.acquire();
        assertEqual(fs.existsSync(second.lockPath), true);
      } finally {
        first.release();
        second.release();
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    await it("recovers a stale lock", async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-lock-stale-"));
      const lock = new RunLock(dir, "run-1");
      fs.mkdirSync(path.dirname(lock.lockPath), { recursive: true });
      fs.writeFileSync(lock.lockPath, JSON.stringify({ pid: 999999999, runId: "run-1" }));
      try {
        lock.acquire();
        assertEqual(fs.existsSync(lock.lockPath), true);
      } finally {
        lock.release();
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });

    await it("does not delete a lock owned by an inaccessible live process", async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-lock-eperm-"));
      const lock = new RunLock(dir, "run-1");
      fs.mkdirSync(path.dirname(lock.lockPath), { recursive: true });
      fs.writeFileSync(lock.lockPath, JSON.stringify({ pid: 424242, runId: "run-1" }));
      const originalKill = process.kill;
      try {
        process.kill = ((_: number, __?: NodeJS.Signals | number) => {
          const error = new Error("operation not permitted") as NodeJS.ErrnoException;
          error.code = "EPERM";
          throw error;
        }) as typeof process.kill;
        let rejected = false;
        try { lock.acquire(); } catch (error) {
          rejected = error instanceof RunLockError;
        }
        assert(rejected, "EPERM must be treated as an active foreign lock");
        assertEqual(fs.existsSync(lock.lockPath), true);
      } finally {
        process.kill = originalKill;
        fs.rmSync(dir, { recursive: true, force: true });
      }
    });
  })];
}
