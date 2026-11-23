import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Descriptions, Space, Spin, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { getHealth, type HealthData } from "../api/system";

const COMPONENT_LABELS: Record<string, string> = {
  mysql: "MySQL 元数据库",
  redis: "Redis 缓存",
  minio: "MinIO 对象存储",
};

/** 首页（M0）：展示后端 /health 状态，作为"前后端联通"的可视证据。 */
export default function Home() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await getHealth());
    } catch {
      message.error("无法连接后端服务");
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10000);
    return () => clearInterval(timer);
  }, [refresh]);

  const ok = health?.status === "ok";

  return (
    <Card
      title="系统健康状态"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>
          刷新
        </Button>
      }
    >
      <Spin spinning={loading && !health}>
        {health ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="整体状态">
                <Badge
                  status={ok ? "success" : "error"}
                  text={ok ? "正常（ok）" : "降级（degraded）"}
                />
              </Descriptions.Item>
              {Object.entries(health.components).map(([key, value]) => (
                <Descriptions.Item key={key} label={COMPONENT_LABELS[key] ?? key}>
                  {value === "up" ? (
                    <Tag color="green">正常</Tag>
                  ) : (
                    <Tag color="red" title={value}>
                      异常
                    </Tag>
                  )}
                </Descriptions.Item>
              ))}
              <Descriptions.Item label="后端版本">
                {health.app} {health.version}（{health.env}）
              </Descriptions.Item>
            </Descriptions>
            <Typography.Text type="secondary">
              每 10 秒自动刷新；此处直接读取后端 GET /api/health。
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="warning">
            尚未获取到后端状态，请确认后端已启动（默认 http://127.0.0.1:8000）。
          </Typography.Text>
        )}
      </Spin>
    </Card>
  );
}
