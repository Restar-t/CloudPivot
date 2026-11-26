import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Empty,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ClearOutlined, ReloadOutlined, UndoOutlined, DeleteOutlined } from "@ant-design/icons";
import type { TablePaginationConfig } from "antd";
import {
  clearTrash,
  listTrash,
  purgeFromTrash,
  restoreFromTrash,
  type TrashItem,
} from "../api/trash";
import { fileKind, formatBytes, formatDateTime } from "../utils/format";

/** 回收站页（M4）：还原 / 彻底删除 / 清空 / 剩余天数 / 原位置。 */
export default function Trash() {
  const [items, setItems] = useState<TrashItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listTrash(page, pageSize);
      setItems(data.items);
      setTotal(data.total);
    } catch {
      // 拦截器已提示
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const onRestore = async (item: TrashItem) => {
    try {
      const r = await restoreFromTrash(item.id);
      message.success(
        r.restored_rows > 1
          ? `「${r.name}」及其 ${r.restored_rows - 1} 项内容已还原`
          : `「${r.name}」已还原`,
      );
      await load();
    } catch {
      // 409 重名冲突：拦截器提示"同名资源已存在"，data 里带建议名（前端 M4 从响应取）
    }
  };

  const onPurge = async (item: TrashItem) => {
    try {
      const r = await purgeFromTrash(item.id);
      message.success(`已彻底删除「${item.name}」${r.files > 1 ? `（共 ${r.files} 个文件）` : ""}`);
      await load();
    } catch {
      // 拦截器已提示
    }
  };

  const onClear = async () => {
    try {
      const r = await clearTrash();
      message.success(`回收站已清空（${r.roots} 项，释放 ${formatBytes(r.bytes_released)}）`);
      await load();
    } catch {
      // 拦截器已提示
    }
  };

  const onTableChange = (pagination: TablePaginationConfig) => {
    if (pagination.current) setPage(pagination.current);
    if (pagination.pageSize) setPageSize(pagination.pageSize);
  };

  return (
    <Card
      title="回收站"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} />
          <Popconfirm
            title="清空回收站？"
            description="全部回收项将被彻底删除并释放空间，不可恢复。"
            okText="清空"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void onClear()}
          >
            <Button danger icon={<ClearOutlined />} disabled={total === 0}>
              清空回收站
            </Button>
          </Popconfirm>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        回收站中的内容仍占用空间；保留 {30} 天后自动清理（自动清理任务将在后续里程碑接入）。
        彻底删除或清空后空间立即释放。
      </Typography.Paragraph>

      <Table<TrashItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        onChange={onTableChange}
        locale={{ emptyText: <Empty description="回收站为空" /> }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, showTotal: (t) => `共 ${t} 项` }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            key: "name",
            render: (_, item) => (
              <Typography.Text ellipsis style={{ maxWidth: 300 }}>
                {item.name}
              </Typography.Text>
            ),
          },
          { title: "类型", key: "kind", width: 110, render: (_, item) => fileKind(item.name, item.is_folder) },
          {
            title: "大小",
            dataIndex: "size",
            key: "size",
            width: 100,
            render: (v: number, item) => (item.is_folder ? "—" : formatBytes(v)),
          },
          {
            title: "原位置",
            dataIndex: "original_path",
            key: "path",
            ellipsis: true,
            render: (v: string) => (
              <Typography.Text type="secondary" ellipsis style={{ maxWidth: 220 }}>
                {v}
              </Typography.Text>
            ),
          },
          {
            title: "删除时间",
            dataIndex: "deleted_at",
            key: "deleted_at",
            width: 160,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "剩余天数",
            key: "remaining",
            width: 100,
            render: (_, item) => (
              <Tooltip title={`按保留 30 天计算`}>
                <Tag color={item.remaining_days <= 7 ? "red" : "default"}>{item.remaining_days} 天</Tag>
              </Tooltip>
            ),
          },
          {
            title: "操作",
            key: "actions",
            width: 180,
            render: (_, item) => (
              <Space>
                <Button size="small" icon={<UndoOutlined />} onClick={() => void onRestore(item)}>
                  还原
                </Button>
                <Popconfirm
                  title="彻底删除？"
                  description="文件将从对象存储物理删除并释放空间，不可恢复。"
                  okText="彻底删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => void onPurge(item)}
                >
                  <Button size="small" danger icon={<DeleteOutlined />}>
                    彻底删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
