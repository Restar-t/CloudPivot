import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Typography,
  message,
} from "antd";
import {
  FileOutlined,
  FolderOutlined,
  FolderAddOutlined,
  ReloadOutlined,
  EditOutlined,
} from "@ant-design/icons";
import type { TablePaginationConfig } from "antd";
import { createFolder, getPath, listChildren, renameFile, type FileItem } from "../api/files";
import { fileKind, formatBytes, formatDateTime } from "../utils/format";

type SortField = "name" | "size" | "uploaded_at";

/** 文件列表页（M2）：面包屑 + 目录浏览 + 新建文件夹 + 重命名；上传/下载 M3 接入。 */
export default function Files() {
  const { folderId } = useParams();
  const currentId = folderId ? Number(folderId) : 0;
  const navigate = useNavigate();

  const [items, setItems] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [path, setPath] = useState<{ id: number; name: string }[]>([{ id: 0, name: "根目录" }]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sort, setSort] = useState<SortField>("name");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  const [createOpen, setCreateOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [renameLoading, setRenameLoading] = useState(false);
  const [createForm] = Form.useForm<{ name: string }>();
  const [renameForm] = Form.useForm<{ new_name: string }>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listChildren(currentId, { page, page_size: pageSize, sort, order });
      setItems(data.items);
      setTotal(data.total);
      setPath(await getPath(currentId));
    } catch {
      // 拦截器已提示错误；保持旧数据
    } finally {
      setLoading(false);
    }
  }, [currentId, page, pageSize, sort, order]);

  useEffect(() => {
    setPage(1); // 切目录回第一页
  }, [currentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onCreate = async ({ name }: { name: string }) => {
    setCreateLoading(true);
    try {
      await createFolder(currentId, name.trim());
      message.success(`已创建文件夹「${name.trim()}」`);
      setCreateOpen(false);
      createForm.resetFields();
      await load();
    } catch {
      // 拦截器已提示（重名 2002 / 非法名 2003）
    } finally {
      setCreateLoading(false);
    }
  };

  const onRename = async ({ new_name }: { new_name: string }) => {
    if (!renameTarget) return;
    setRenameLoading(true);
    try {
      await renameFile(renameTarget.id, new_name.trim());
      message.success("已重命名");
      setRenameTarget(null);
      await load();
    } catch {
      // 拦截器已提示
    } finally {
      setRenameLoading(false);
    }
  };

  const onTableChange = (pagination: TablePaginationConfig, _f: unknown, sorter: unknown) => {
    const s = sorter as { field?: string; order?: "ascend" | "descend" };
    if (s?.field && s.order) {
      setSort(s.field as SortField);
      setOrder(s.order === "descend" ? "desc" : "asc");
    }
    if (pagination.current) setPage(pagination.current);
    if (pagination.pageSize) setPageSize(pagination.pageSize);
  };

  return (
    <Card
      title={
        <Space size="middle">
          <Breadcrumb
            items={path.map((p) => ({
              title:
                p.id === 0 ? (
                  <Link to="/">根目录</Link>
                ) : (
                  <Link to={`/folders/${p.id}`}>{p.name}</Link>
                ),
            }))}
          />
        </Space>
      }
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} />
          <Button type="primary" icon={<FolderAddOutlined />} onClick={() => setCreateOpen(true)}>
            新建文件夹
          </Button>
        </Space>
      }
    >
      <Table<FileItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        onChange={onTableChange}
        locale={{
          emptyText: (
            <Empty
              description={
                <span>
                  此目录为空
                  <br />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    文件上传功能将在下一阶段（M3）开放
                  </Typography.Text>
                </span>
              }
            />
          ),
        }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 项`,
        }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            key: "name",
            sorter: true,
            render: (_, item) => (
              <Button
                type="text"
                icon={item.is_folder ? <FolderOutlined /> : <FileOutlined />}
                onClick={() => item.is_folder && navigate(`/folders/${item.id}`)}
                style={{ maxWidth: 480, justifyContent: "flex-start" }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {item.name}
                </span>
              </Button>
            ),
          },
          {
            title: "大小",
            dataIndex: "size",
            key: "size",
            width: 110,
            sorter: true,
            render: (v: number, item) => (item.is_folder ? "—" : formatBytes(v)),
          },
          {
            title: "类型",
            key: "kind",
            width: 120,
            render: (_, item) => fileKind(item.name, item.is_folder),
          },
          {
            title: "修改时间",
            dataIndex: "updated_at",
            key: "uploaded_at",
            width: 170,
            sorter: true,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "操作",
            key: "actions",
            width: 100,
            render: (_, item) => (
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => {
                  setRenameTarget(item);
                  renameForm.setFieldsValue({ new_name: item.name });
                }}
              >
                重命名
              </Button>
            ),
          },
        ]}
      />

      <Modal
        title="新建文件夹"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" onFinish={onCreate}>
          <Form.Item
            name="name"
            label="文件夹名称"
            rules={[
              { required: true, message: "请输入名称" },
              { max: 255, message: "名称过长" },
              {
                pattern: /^[^\\\\/:*?"<>|]+$/,
                message: "名称不能包含 / \\ : * ? \" < > | 等字符",
              },
            ]}
          >
            <Input placeholder="例如：课程资料" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={createLoading}>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title={`重命名「${renameTarget?.name ?? ""}」`}
        open={renameTarget !== null}
        onCancel={() => setRenameTarget(null)}
        footer={null}
        destroyOnClose
      >
        <Form form={renameForm} layout="vertical" onFinish={onRename}>
          <Form.Item
            name="new_name"
            label="新名称"
            rules={[
              { required: true, message: "请输入新名称" },
              { max: 255, message: "名称过长" },
              {
                pattern: /^[^\\\\/:*?"<>|]+$/,
                message: "名称不能包含 / \\ : * ? \" < > | 等字符",
              },
            ]}
          >
            <Input autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={renameLoading}>
            确认
          </Button>
        </Form>
      </Modal>
    </Card>
  );
}
