import { useCallback, useEffect, useState } from "react";
import { Button, Card, Empty, Popconfirm, Table, Tag, Typography, message } from "antd";
import { CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import type { TablePaginationConfig } from "antd";
import { cancelShare, listShares, type ShareItem } from "../api/share";
import { formatDateTime } from "../utils/format";

const shareUrl = (token: string) => `${location.origin}/s/${token}`;

/** 我的分享（M5.7/5.8）：列表（次数/最近访问）+ 取消 + 复制链接。 */
export default function MyShares() {
  const [items, setItems] = useState<ShareItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems((await listShares()).items);
    } catch {
      // 拦截器已提示
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onCancel = async (item: ShareItem) => {
    try {
      await cancelShare(item.id);
      message.success(`已取消「${item.file_name}」的分享，原链接与访问会话全部失效`);
      await load();
    } catch {
      // 拦截器已提示
    }
  };

  const onTableChange = (p: TablePaginationConfig) => void p;

  return (
    <Card
      title="我的分享"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>
          刷新
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        取消分享后，原链接立即失效，已发出的访问会话（含传输中的断点下载）也将全部拒绝。
      </Typography.Paragraph>
      <Table<ShareItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        onChange={onTableChange}
        locale={{ emptyText: <Empty description="还没有创建过分享" /> }}
        pagination={{ showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
        columns={[
          {
            title: "文件",
            dataIndex: "file_name",
            key: "file_name",
            ellipsis: true,
            render: (v: string, item) =>
              item.file_exists ? (
                <Typography.Text ellipsis style={{ maxWidth: 220 }}>{v}</Typography.Text>
              ) : (
                <Typography.Text type="secondary" delete>{v}（文件已删除）</Typography.Text>
              ),
          },
          {
            title: "状态",
            dataIndex: "status",
            key: "status",
            width: 90,
            render: (s: string) =>
              s === "ACTIVE" ? <Tag color="green">有效</Tag> : <Tag>已取消</Tag>,
          },
          {
            title: "链接",
            key: "url",
            render: (_, item) => (
              <Button
                size="small"
                icon={<CopyOutlined />}
                onClick={async () => {
                  await navigator.clipboard.writeText(shareUrl(item.token));
                  message.success("链接已复制");
                }}
              >
                复制链接
              </Button>
            ),
          },
          {
            title: "访问次数",
            key: "visits",
            width: 110,
            render: (_, item) => (
              <span>
                {item.visit_count}
                {item.max_visits !== null ? ` / ${item.max_visits}` : ""}
              </span>
            ),
          },
          {
            title: "最近访问",
            dataIndex: "last_visit_at",
            key: "last_visit",
            width: 160,
            render: (v: string | null) => (v ? formatDateTime(v) : "—"),
          },
          {
            title: "有效期至",
            dataIndex: "expire_at",
            key: "expire",
            width: 160,
            render: (v: string | null) => (v ? formatDateTime(v) : "永久"),
          },
          {
            title: "创建时间",
            dataIndex: "created_at",
            key: "created",
            width: 160,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "操作",
            key: "actions",
            width: 110,
            render: (_, item) =>
              item.status === "ACTIVE" ? (
                <Popconfirm
                  title="取消该分享？"
                  description="所有访问链接与会话将立即失效。"
                  okText="取消分享"
                  cancelText="返回"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => void onCancel(item)}
                >
                  <Button size="small" danger>取消分享</Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />
    </Card>
  );
}
