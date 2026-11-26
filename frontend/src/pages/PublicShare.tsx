import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Input,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import { DownloadOutlined, FileOutlined } from "@ant-design/icons";
import { authShare, shareDownloadUrl, sharePreviewUrl, type ShareAuthResult } from "../api/share";
import { formatBytes, formatDateTime } from "../utils/format";

/** 公开分享访问页（M5.7）：免登录，输提取码 → 文件信息 → 预览/下载。 */
export default function PublicShare() {
  const { token = "" } = useParams();
  const [code, setCode] = useState("");
  const [authed, setAuthed] = useState<ShareAuthResult | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!code.trim()) return;
    setLoading(true);
    try {
      setAuthed(await authShare(token, code.trim()));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const dlUrl = authed ? shareDownloadUrl(token, authed.session_token) : "";
  const pvUrl = authed ? sharePreviewUrl(token, authed.session_token) : "";

  const previewInline = () => {
    // 新标签打开预览（inline 响应带 CSP，浏览器按 Content-Type 渲染）
    window.open(pvUrl, "_blank");
    message.info("已在新标签页打开预览；若类型不支持预览，将自动转为下载");
  };

  return (
    <div style={{ minHeight: "100vh", background: "linear-gradient(135deg,#f5f7fa,#e4e9f2)", padding: "48px 16px" }}>
      <Card style={{ maxWidth: 560, margin: "0 auto" }} title={<Space><FileOutlined /> 文件分享</Space>}>
        {!authed ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              该文件通过链接分享，输入提取码即可预览或下载（无需登录）。
            </Typography.Text>
            <Input
              size="large"
              placeholder="提取码"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onPressEnter={() => void submit()}
              maxLength={16}
              style={{ letterSpacing: 4, textAlign: "center", fontWeight: 600 }}
            />
            <Button type="primary" size="large" block loading={loading} onClick={() => void submit()}>
              访问文件
            </Button>
          </Space>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert type="success" showIcon message="验证通过" />
            <div>
              <Typography.Title level={4} style={{ margin: 0, wordBreak: "break-all" }}>
                {authed.file.name}
              </Typography.Title>
              <Typography.Text type="secondary">
                {formatBytes(authed.file.size)}
                {authed.file.updated_at ? ` · ${formatDateTime(authed.file.updated_at)}` : ""}
              </Typography.Text>
            </div>
            <Space>
              <Button type="primary" icon={<DownloadOutlined />} href={dlUrl} target="_blank">
                下载
              </Button>
              <Button onClick={previewInline}>在线预览</Button>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              下载次数按访问会话计算；分享被取消或文件删除后，本页访问立即失效。
            </Typography.Text>
          </Space>
        )}
        {loading && <Spin style={{ marginTop: 16 }} />}
      </Card>
    </div>
  );
}
