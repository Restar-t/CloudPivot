import { useEffect, useRef, useState } from "react";
import { Button, Progress, Typography, message } from "antd";
import { CloseCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  cancelUpload, completeUpload, initUpload, putPart, uploadStatus,
} from "../api/transfer";
import { formatBytes } from "../utils/format";

type Status = "hashing" | "uploading" | "done" | "error" | "cancelled";

export interface UploadTask {
  uid: string;
  file: File;
  parentId: number;
}

interface ItemState {
  task: UploadTask;
  status: Status;
  percent: number;       // 哈希 0-30，上传 30-100 折算
  phaseLabel: string;
  error?: string;
  uploadId?: string;     // 可续传的任务
}

interface Props {
  queue: UploadTask[];
  onFinished: () => void;
  onRemove: (uid: string) => void;
}

const HASH_WEIGHT = 20;     // 哈希阶段占总进度 0-20%
const PART_CONCURRENCY = 4; // 与后端配置一致（CODING_PLAN §7）
const PART_RETRY = 3;

const fileKey = (task: UploadTask) =>
  `${task.parentId}:${task.file.name}:${task.file.size}:${task.file.lastModified}`;

const loadStored = (key: string): { uploadId: string; sha: string } | null => {
  try {
    return JSON.parse(localStorage.getItem(`nd_up_${key}`) ?? "null");
  } catch {
    return null;
  }
};
const store = (key: string, v: { uploadId: string; sha: string } | null) => {
  if (v) localStorage.setItem(`nd_up_${key}`, JSON.stringify(v));
  else localStorage.removeItem(`nd_up_${key}`);
};

/** 分片上传面板（M6.9）：Worker 哈希 → init（秒传即时完成）→ 并发分片（断点续传/单片重试）→ complete。 */
export default function UploadPanel({ queue, onFinished, onRemove }: Props) {
  const [items, setItems] = useState<Record<string, ItemState>>({});
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const aborts = useRef<Record<string, AbortController>>({});
  const cancelled = useRef<Set<string>>(new Set());

  const update = (uid: string, patch: Partial<ItemState>) =>
    setItems((prev) => ({ ...prev, [uid]: { ...prev[uid], ...patch } }));

  const start = async (task: UploadTask) => {
    const key = fileKey(task);
    update(task.uid, { status: "hashing", phaseLabel: "计算 SHA-256…", percent: 0 });

    // ① 哈希（Web Worker）
    const sha = await hashFile(task.file, (p) =>
      update(task.uid, { percent: Math.round((p / 100) * HASH_WEIGHT) }),
    );

    // ② 已有未完成任务 → 续传；否则 init
    let uploadId = loadStored(key)?.uploadId;
    if (uploadId) {
      try {
        const st = await uploadStatus(uploadId);
        if (["CREATED", "UPLOADING", "VERIFYING"].includes(st.status)) {
          update(task.uid, { uploadId, phaseLabel: "恢复断点…" });
        } else {
          store(key, null);
          uploadId = undefined;
        }
      } catch {
        uploadId = undefined;
      }
    }

    let chunkSize = 8 * 1024 * 1024;
    let missing: number[] = [];
    if (!uploadId) {
      const init = await initUpload({
        file_name: task.file.name, size: task.file.size,
        parent_id: task.parentId, declared_sha256: sha,
      });
      if (init.instant) {
        // 秒传：服务端已建好版本
        update(task.uid, { status: "done", percent: 100, phaseLabel: "秒传完成（服务端已有相同内容）" });
        message.success(`「${task.file.name}」秒传成功（未上传字节）`);
        finishIfIdle(task.uid);
        return;
      }
      uploadId = init.upload_id!;
      chunkSize = init.chunk_size;
      missing = Array.from({ length: init.chunk_count }, (_, i) => i + 1);
      store(key, { uploadId, sha });
    } else {
      const st = await uploadStatus(uploadId);
      chunkSize = st.chunk_size;
      missing = st.missing_parts;
      if (missing.length === 0) {
        // 分片其实已齐（刷新场景）→ 直接 complete
      }
    }

    update(task.uid, { status: "uploading", uploadId, phaseLabel: "", percent: HASH_WEIGHT });
    const controller = new AbortController();
    aborts.current[task.uid] = controller;

    // ③ 并发分片（限流 4），单片重试 3 次
    let cursor = 0;
    let sentBytes = 0;
    const totalBytes = task.file.size;
    const workers = Array.from({ length: Math.min(PART_CONCURRENCY, missing.length || 1) }, async () => {
      while (cursor < missing.length && !cancelled.current.has(task.uid)) {
        const partNo = missing[cursor++];
        const start = (partNo - 1) * chunkSize;
        const blob = task.file.slice(start, start + chunkSize);
        const partSha = await hashBlob(blob);
        let attempt = 0;
        for (; attempt < PART_RETRY; attempt++) {
          if (cancelled.current.has(task.uid) || controller.signal.aborted) return;
          try {
            await putPart(uploadId!, partNo, blob, partSha, controller.signal);
            sentBytes += blob.size;
            const pct = HASH_WEIGHT + Math.min(99, Math.round((sentBytes / totalBytes) * (100 - HASH_WEIGHT)));
            update(task.uid, { percent: pct });
            break;
          } catch (e) {
            if ((e as { code?: number }).code === 3003 || (e as { code?: number }).code === 3004) {
              update(task.uid, { status: "error", error: (e as Error).message });
              return;
            }
            if (attempt === PART_RETRY - 1) {
              update(task.uid, { status: "error", error: `分片 ${partNo} 上传失败（已重试 3 次）` });
              return;
            }
            await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
          }
        }
      }
    });
    await Promise.all(workers);
    if (cancelled.current.has(task.uid)) {
      await cancelUpload(uploadId!).catch(() => undefined);
      store(key, null);
      update(task.uid, { status: "cancelled", phaseLabel: "已取消，预留空间已释放" });
      finishIfIdle(task.uid);
      return;
    }

    // ④ complete（失败可重试：任务保留）
    try {
      const r = await completeUpload(uploadId!);
      store(key, null);
      update(task.uid, { status: "done", percent: 100, phaseLabel: "" });
      message.success(`「${task.file.name}」上传成功（v${r.version_no}）`);
    } catch (e) {
      const err = e as { code?: number; message?: string };
      if (err.code === 3004) {
        // 同名/目标冲突：任务保留，用户可选重试或另存
        update(task.uid, {
          status: "error",
          error: "目标文件已被其他操作更新——可重试（沿用新状态）或取消后另存",
        });
      } else {
        update(task.uid, { status: "error", error: err.message || "完成失败" });
      }
    } finally {
      delete aborts.current[task.uid];
      finishIfIdle(task.uid);
    }
  };

  const finishIfIdle = (uid: string) => {
    const othersRunning = Object.entries(itemsRef.current).some(
      ([k, s]) => k !== uid && (s.status === "uploading" || s.status === "hashing"),
    );
    if (!othersRunning) onFinished();
  };

  useEffect(() => {
    queue.forEach((task) => {
      if (!items[task.uid]) void start(task);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue]);

  const onCancel = async (uid: string) => {
    cancelled.current.add(uid);
    aborts.current[uid]?.abort();
    // start 内部会在返回后调用 cancel API + 清理
  };

  const onRetry = (uid: string) => {
    const st = items[uid];
    if (st) void start(st.task);
  };

  if (queue.length === 0) return null;

  return (
    <div style={{ border: "1px solid #f0f0f0", borderRadius: 8, padding: 12, marginBottom: 12, background: "#fafafa" }}>
      {queue.map((task) => {
        const st = items[task.uid];
        if (!st) return null;
        return (
          <div key={task.uid} style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <Typography.Text ellipsis style={{ flex: 1 }} title={task.file.name}>
                {task.file.name}
                <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                  {formatBytes(task.file.size)} · {st.phaseLabel}
                </Typography.Text>
              </Typography.Text>
              <span style={{ flexShrink: 0 }}>
                {st.status === "uploading" && (
                  <Button size="small" type="text" icon={<CloseCircleOutlined />} onClick={() => void onCancel(task.uid)}>
                    取消
                  </Button>
                )}
                {st.status === "error" && (
                  <Button size="small" type="link" icon={<ReloadOutlined />} onClick={() => onRetry(task.uid)}>
                    重试
                  </Button>
                )}
                {st.status !== "uploading" && (
                  <Button size="small" type="text" onClick={() => onRemove(task.uid)}>
                    移除
                  </Button>
                )}
              </span>
            </div>
            <Progress
              percent={st.percent}
              size="small"
              status={st.status === "error" ? "exception" : st.status === "done" ? "success" : st.status === "cancelled" ? "normal" : "active"}
            />
            {st.error && <Typography.Text type="danger" style={{ fontSize: 12 }}>{st.error}</Typography.Text>}
          </div>
        );
      })}
    </div>
  );
}

// ---------- 哈希辅助 ----------
function hashFile(file: File, onProgress: (percent: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("../workers/hash.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e) => {
      const d = e.data;
      if (d.type === "progress") onProgress(d.percent);
      if (d.type === "done") { worker.terminate(); resolve(d.sha256); }
      if (d.type === "error") { worker.terminate(); reject(new Error(d.message)); }
    };
    worker.onerror = (e) => { worker.terminate(); reject(new Error(e.message || "哈希计算失败")); };
    worker.postMessage({ type: "hash", file, chunkSize: 4 * 1024 * 1024 });
  });
}

async function hashBlob(blob: Blob): Promise<string> {
  const { hashBlobSha256 } = await import("../utils/sha256");
  return hashBlobSha256(blob);
}
