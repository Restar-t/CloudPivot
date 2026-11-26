/// <reference lib="webworker" />
/** 分片/整文件 SHA-256 Worker（M6.9）：纯实现在 utils/sha256.ts，此处仅消息循环。 */
import { sha256Stream, type ProgressFn } from "../utils/sha256";

interface HashMessage {
  type: "hash";
  file: File;
  chunkSize: number;
}

self.onmessage = async (event: MessageEvent<HashMessage>) => {
  if (event.data.type !== "hash") return;
  const { file, chunkSize } = event.data;
  try {
    const onProgress: ProgressFn = (percent) =>
      (self as unknown as Worker).postMessage({ type: "progress", percent });
    const sha256 = await sha256Stream(file, chunkSize, onProgress);
    (self as unknown as Worker).postMessage({ type: "done", sha256 });
  } catch (e) {
    (self as unknown as Worker).postMessage({ type: "error", message: String(e) });
  }
};
