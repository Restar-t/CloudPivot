import { useEffect, useRef, useState } from "react";
import { Button, Progress, Typography, message } from "antd";
import { CloseCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { uploadFile } from "../api/transfer";
import { formatBytes } from "../utils/format";

type Status = "uploading" | "done" | "error" | "cancelled";

export interface UploadTask {
  uid: string;
  file: File;
  parentId: number;
}

interface ItemState {
  task: UploadTask;
  status: Status;
  percent: number;
  error?: string;
}

interface Props {
  queue: UploadTask[];
  onFinished: () => void; // 所有任务结束后通知父组件刷新列表与配额
  onRemove: (uid: string) => void;
}

/** 上传队列面板：逐文件进度条、失败重试、可取消（M3 单请求上传）。 */
export default function UploadPanel({ queue, onFinished, onRemove }: Props) {
  const [items, setItems] = useState<Record<string, ItemState>>({});
  const aborts = useRef<Record<string, AbortController>>({});
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const update = (uid: string, patch: Partial<ItemState>) =>
    setItems((prev) => ({ ...prev, [uid]: { ...prev[uid], ...patch } }));

  const start = async (task: UploadTask) => {
    const controller = new AbortController();
    aborts.current[task.uid] = controller;
    update(task.uid, { status: "uploading", percent: 0, error: undefined });
    try {
      await uploadFile(
        task.parentId,
        task.file,
        (p) => update(task.uid, { percent: p }),
        controller.signal,
      );
      update(task.uid, { status: "done", percent: 100 });
      message.success(`「${task.file.name}」上传成功`);
    } catch (e) {
      if (controller.signal.aborted) {
        update(task.uid, { status: "cancelled" });
      } else {
        const err = e as { message?: string; code?: number };
        update(task.uid, {
          status: "error",
          // 拦截器抛 ApiError(message 已是后端中文文案，如"空间不足"/"同名资源已存在")
          error: err.message || "上传失败",
        });
      }
    } finally {
      delete aborts.current[task.uid];
      // 任一任务结束后通知父组件刷新（列表/配额）。判断"是否还有进行中任务"时，
      // 不能读 itemsRef（本次 setState 尚未提交，本任务仍显示 uploading），
      // 而是排除自身后看其余任务——否则 onFinished 永远不会触发。
      const othersRunning = Object.entries(itemsRef.current).some(
        ([uid, s]) => uid !== task.uid && s.status === "uploading",
      );
      if (!othersRunning) onFinished();
    }
  };

  useEffect(() => {
    // 新进入队列且尚未开始的任务 → 逐个启动（串行，避免大文件并发抢占带宽）
    queue.forEach((task) => {
      if (!items[task.uid]) start(task);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue]);

  const retry = (uid: string) => {
    const st = items[uid];
    if (st) void start(st.task);
  };
  const cancel = (uid: string) => aborts.current[uid]?.abort();

  if (queue.length === 0) return null;

  return (
    <div
      style={{
        border: "1px solid #f0f0f0",
        borderRadius: 8,
        padding: 12,
        marginBottom: 12,
        background: "#fafafa",
      }}
    >
      {queue.map((task) => {
        const st = items[task.uid];
        if (!st) return null;
        return (
          <div key={task.uid} style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <Typography.Text ellipsis style={{ flex: 1 }} title={task.file.name}>
                {task.file.name}
                <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                  {formatBytes(task.file.size)}
                </Typography.Text>
              </Typography.Text>
              <span style={{ flexShrink: 0 }}>
                {st.status === "uploading" && (
                  <Button size="small" type="text" icon={<CloseCircleOutlined />} onClick={() => cancel(task.uid)}>
                    取消
                  </Button>
                )}
                {st.status === "error" && (
                  <Button size="small" type="link" icon={<ReloadOutlined />} onClick={() => retry(task.uid)}>
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
              status={
                st.status === "error"
                  ? "exception"
                  : st.status === "done"
                    ? "success"
                    : st.status === "cancelled"
                      ? "normal"
                      : "active"
              }
            />
            {st.error && <Typography.Text type="danger" style={{ fontSize: 12 }}>{st.error}</Typography.Text>}
            {st.status === "cancelled" && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>已取消</Typography.Text>
            )}
          </div>
        );
      })}
    </div>
  );
}
