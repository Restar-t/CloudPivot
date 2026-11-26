import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Card, Empty, Input, Space, Table, Typography } from "antd";
import { DownloadOutlined, FolderOutlined, FileOutlined, SearchOutlined } from "@ant-design/icons";
import type { TablePaginationConfig } from "antd";
import { searchFiles, type FileItem } from "../api/files";
import { downloadFile } from "../api/transfer";
import { fileKind, formatBytes, formatDateTime } from "../utils/format";

/** 搜索结果页（M4）：文件名匹配（含目录），点击目录可跳转。 */
export default function Search() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const q = params.get("q") ?? "";

  const [input, setInput] = useState(q);
  const [items, setItems] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setInput(q);
  }, [q]);

  const run = useCallback(
    async (query: string, p: number, ps: number) => {
      if (!query.trim()) {
        setItems([]);
        setTotal(0);
        return;
      }
      setLoading(true);
      try {
        const data = await searchFiles(query.trim(), { page: p, page_size: ps });
        setItems(data.items);
        setTotal(data.total);
      } catch {
        // 拦截器已提示
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void run(q, page, pageSize);
  }, [run, q, page, pageSize]);

  const submit = () => setParams(input.trim() ? { q: input.trim() } : {});

  const onTableChange = (pagination: TablePaginationConfig) => {
    if (pagination.current) setPage(pagination.current);
    if (pagination.pageSize) setPageSize(pagination.pageSize);
  };

  return (
    <Card
      title={
        <Space>
          <SearchOutlined />
          {q ? `搜索结果：「${q}」` : "搜索文件"}
        </Space>
      }
    >
      <Space.Compact style={{ width: "100%", maxWidth: 560, marginBottom: 16 }}>
        <Input
          placeholder="输入文件名或目录名关键词"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={submit}
          allowClear
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={submit}>
          搜索
        </Button>
      </Space.Compact>

      <Table<FileItem>
        rowKey="id"
        loading={loading}
        dataSource={items}
        onChange={onTableChange}
        locale={{ emptyText: <Empty description={q ? "无匹配结果" : "输入关键词开始搜索"} /> }}
        pagination={{ current: page, pageSize, total, showSizeChanger: true, showTotal: (t) => `共 ${t} 项` }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            key: "name",
            render: (_, item) => (
              <Button
                type="text"
                icon={item.is_folder ? <FolderOutlined /> : <FileOutlined />}
                onClick={() => item.is_folder && navigate(`/folders/${item.id}`)}
                style={{ maxWidth: 420, justifyContent: "flex-start" }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.name}
                </span>
              </Button>
            ),
          },
          {
            title: "大小",
            dataIndex: "size",
            key: "size",
            width: 100,
            render: (v: number, item) => (item.is_folder ? "—" : formatBytes(v)),
          },
          { title: "类型", key: "kind", width: 110, render: (_, item) => fileKind(item.name, item.is_folder) },
          {
            title: "修改时间",
            dataIndex: "updated_at",
            key: "time",
            width: 160,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "操作",
            key: "actions",
            width: 90,
            render: (_, item) =>
              !item.is_folder ? (
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => void downloadFile(item.id, item.name)}
                >
                  下载
                </Button>
              ) : (
                <Typography.Text type="secondary">可进入</Typography.Text>
              ),
          },
        ]}
      />
    </Card>
  );
}
