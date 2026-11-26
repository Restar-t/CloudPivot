import { useEffect, useState } from "react";
import { Alert, Button, Form, Input, Modal, Radio, Select, Space, Typography, message } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { createShare } from "../api/share";

interface Props {
  open: boolean;
  file: { id: number; name: string; size: number } | null;
  onClose: () => void;
}

interface FormValues {
  code_mode: "auto" | "custom";
  custom_code?: string;
  expire_days: number | null;
  max_visits: number | null;
}

/** 分享对话框（M5.7）：提取码（自动/自定义）/ 有效期 / 次数 → 生成链接与提取码。 */
export default function ShareDialog({ open, file, onClose }: Props) {
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ url: string; code: string } | null>(null);

  useEffect(() => {
    if (open) {
      setResult(null);
      form.resetFields();
    }
  }, [open, form]);

  const onFinish = async (values: FormValues) => {
    if (!file) return;
    const code = values.code_mode === "custom" ? (values.custom_code ?? "").trim() : "";
    if (values.code_mode === "custom" && (code.length < 4 || code.length > 16)) {
      message.warning("自定义提取码需 4-16 位");
      return;
    }
    setSubmitting(true);
    try {
      const r = await createShare({
        file_id: file.id,
        ...(code ? { extract_code: code } : {}),
        ...(values.expire_days ? { expire_days: values.expire_days } : {}),
        ...(values.max_visits ? { max_visits: values.max_visits } : {}),
      });
      setResult({ url: `${location.origin}/s/${r.token}`, code: r.extract_code });
    } catch {
      // 拦截器已提示
    } finally {
      setSubmitting(false);
    }
  };

  const copy = async (text: string, label: string) => {
    await navigator.clipboard.writeText(text);
    message.success(`${label}已复制`);
  };

  return (
    <Modal
      title={`分享「${file?.name ?? ""}」`}
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      {result ? (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Alert type="success" showIcon message="分享创建成功，链接与提取码如下" />
          <div>
            <Typography.Text type="secondary">访问链接</Typography.Text>
            <div style={{ display: "flex", gap: 8 }}>
              <Input value={result.url} readOnly />
              <Button icon={<CopyOutlined />} onClick={() => void copy(result.url, "链接")}>
                复制
              </Button>
            </div>
          </div>
          <div>
            <Typography.Text type="secondary">提取码</Typography.Text>
            <div style={{ display: "flex", gap: 8 }}>
              <Input value={result.code} readOnly style={{ width: 160, letterSpacing: 4, fontWeight: 600 }} />
              <Button icon={<CopyOutlined />} onClick={() => void copy(result.code, "提取码")}>
                复制
              </Button>
            </div>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            访问者凭链接+提取码可预览/下载该文件；取消分享或删除文件后立即失效。
          </Typography.Text>
        </Space>
      ) : (
        <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ code_mode: "auto" }}>
          <Form.Item name="code_mode" label="提取码" rules={[{ required: true }]}>
            <Radio.Group
              options={[
                { label: "自动生成（4 位易读字符）", value: "auto" },
                { label: "自定义", value: "custom" },
              ]}
            />
          </Form.Item>
          <Form.Item shouldUpdate={(p, c) => p.code_mode !== c.code_mode} style={{ marginTop: -12 }}>
            {() =>
              form.getFieldValue("code_mode") === "custom" ? (
                <Form.Item
                  name="custom_code"
                  rules={[
                    { required: true, message: "请输入提取码" },
                    { min: 4, max: 16, message: "4-16 位" },
                  ]}
                >
                  <Input placeholder="4-16 位字母或数字" maxLength={16} />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item name="expire_days" label="有效期">
            <Select
              allowClear
              placeholder="永久有效"
              options={[
                { value: 1, label: "1 天" },
                { value: 7, label: "7 天" },
                { value: 30, label: "30 天" },
              ]}
            />
          </Form.Item>
          <Form.Item name="max_visits" label="可下载次数">
            <Select
              allowClear
              placeholder="不限次数"
              options={[
                { value: 1, label: "1 次" },
                { value: 5, label: "5 次" },
                { value: 20, label: "20 次" },
              ]}
            />
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 12 }}>
            访问次数按「下载会话」计：同一会话的预览与多次断点下载只算一次。
          </Typography.Text>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            创建分享链接
          </Button>
        </Form>
      )}
    </Modal>
  );
}
