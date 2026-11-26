import { useEffect, useState } from "react";
import { Modal, Spin, Typography, message } from "antd";
import axios from "axios";
import { useAuthStore } from "../stores/auth";

interface Props {
  open: boolean;
  file: { id: number; name: string } | null;
  onClose: () => void;
}

type Kind = "image" | "text" | "pdf" | "download" | "loading";

/** 预览弹窗（M5.6）：请求 /files/{id}/preview，按响应头渲染。
 *  文本由前端解码并转义渲染（TextDecoder 尝试 UTF-8/GBK），杜绝 XSS。 */
export default function PreviewModal({ open, file, onClose }: Props) {
  const [kind, setKind] = useState<Kind>("loading");
  const [imgUrl, setImgUrl] = useState("");
  const [pdfUrl, setPdfUrl] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");

  const load = async (fileId: number, name: string) => {
    setKind("loading");
    setReason("");
    try {
      const { accessToken } = useAuthStore.getState();
      const resp = await axios.get(`/api/files/${fileId}/preview`, {
        responseType: "blob",
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      });
      const ctype = String(resp.headers["content-type"] ?? "");
      const reasonHeader = resp.headers["x-preview-reason"] ?? "";
      setReason(reasonHeader);
      const blob = resp.data as Blob;

      if (ctype.startsWith("image/")) {
        setImgUrl(URL.createObjectURL(blob));
        setKind("image");
      } else if (ctype.startsWith("application/pdf")) {
        setPdfUrl(URL.createObjectURL(blob));
        setKind("pdf");
      } else if (ctype.startsWith("text/")) {
        const buf = await blob.arrayBuffer();
        try {
          setText(new TextDecoder("utf-8", { fatal: true }).decode(buf));
        } catch {
          setText(new TextDecoder("gbk").decode(buf));
        }
        setKind("text");
      } else {
        setKind("download");
        message.info(`「${name}」${reasonHeader || "该类型不支持预览"}，已按下载处理`);
        // 直接触发下载
        const disposition = String(resp.headers["content-disposition"] ?? "");
        const m = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = m ? decodeURIComponent(m[1]) : name;
        a.click();
      }
    } catch {
      message.error("预览加载失败");
      onClose();
    }
  };

  useEffect(() => {
    if (open && file) void load(file.id, file.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, file?.id]);

  const title = file ? `预览「${file.name}」` : "预览";

  return (
    <Modal title={title} open={open} onCancel={onClose} footer={null} width={760} destroyOnClose>
      {kind === "loading" && <Spin style={{ display: "block", margin: "48px auto" }} />}
      {kind === "image" && (
        <div style={{ textAlign: "center" }}>
          <img src={imgUrl} alt="preview" style={{ maxWidth: "100%", maxHeight: 480 }} />
        </div>
      )}
      {kind === "pdf" && (
        <iframe title="pdf" src={pdfUrl} style={{ width: "100%", height: 480, border: "1px solid #eee" }} />
      )}
      {kind === "text" && (
        <pre
          style={{
            maxHeight: 480, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all",
            background: "#fafafa", padding: 12, borderRadius: 8, margin: 0,
          }}
        >
          {text}
        </pre>
      )}
      {kind === "download" && (
        <Typography.Text type="secondary">
          此类型不支持在线预览（{reason}），已为您触发下载。
        </Typography.Text>
      )}
    </Modal>
  );
}
