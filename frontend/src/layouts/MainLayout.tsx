import { useState } from "react";
import { Avatar, Button, Dropdown, Form, Input, Layout, Modal, Typography, message } from "antd";
import {
  CloudServerOutlined,
  KeyOutlined,
  LogoutOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import type { MenuProps } from "antd";
import { apiChangePassword, apiLogout } from "../api/auth";
import { useAuthStore } from "../stores/auth";

const { Header, Content, Footer } = Layout;

interface PasswordForm {
  old_password: string;
  new_password: string;
  confirm: string;
}

/** 主布局：顶栏（品牌 + 导航 + 用户菜单）+ 内容区。 */
export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, refreshToken, setTokens, clear } = useAuthStore();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdLoading, setPwdLoading] = useState(false);
  const [form] = Form.useForm<PasswordForm>();

  const onLogout = async () => {
    try {
      await apiLogout(refreshToken);
    } catch {
      // 后端撤销失败也继续本地清理（token 已带上，尽力而为）
    }
    clear();
    message.success("已退出登录");
    navigate("/login", { replace: true });
  };

  const onChangePassword = async (values: PasswordForm) => {
    setPwdLoading(true);
    try {
      const pair = await apiChangePassword(values.old_password, values.new_password, refreshToken);
      setTokens(pair);
      message.success("密码已修改，其他设备会话已失效");
      setPwdOpen(false);
      form.resetFields();
    } catch {
      // 错误提示已由拦截器统一弹出
    } finally {
      setPwdLoading(false);
    }
  };

  const items: MenuProps["items"] = [
    { key: "username", label: <span style={{ fontWeight: 600 }}>{user?.username}</span>, disabled: true },
    { type: "divider" },
    { key: "password", icon: <KeyOutlined />, label: "修改密码", onClick: () => setPwdOpen(true) },
    { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: () => void onLogout() },
  ];

  return (
    <Layout style={{ minHeight: "100%" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "#001529",
          paddingInline: 24,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <CloudServerOutlined style={{ color: "#1677ff", fontSize: 22 }} />
          <Typography.Title level={4} style={{ color: "#fff", margin: 0 }}>
            网盘
          </Typography.Title>
          <nav style={{ marginLeft: 24, display: "flex", gap: 16 }}>
            {[
              { to: "/", label: "我的文件" },
              { to: "/system", label: "系统状态" },
            ].map((item) => {
              const active =
                item.to === "/"
                  ? location.pathname === "/" || location.pathname.startsWith("/folders")
                  : location.pathname.startsWith(item.to);
              return (
                <span
                  key={item.to}
                  onClick={() => navigate(item.to)}
                  style={{
                    cursor: "pointer",
                    color: active ? "#fff" : "rgba(255,255,255,0.65)",
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {item.label}
                </span>
              );
            })}
          </nav>
        </div>
        <Dropdown menu={{ items }} placement="bottomRight">
          <span style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <Avatar style={{ backgroundColor: "#1677ff" }} icon={<UserOutlined />} />
            <span style={{ color: "rgba(255,255,255,0.85)" }}>{user?.username}</span>
          </span>
        </Dropdown>
      </Header>
      <Content style={{ padding: 24, maxWidth: 1200, width: "100%", margin: "0 auto" }}>
        <Outlet />
      </Content>
      <Footer style={{ textAlign: "center", color: "rgba(0,0,0,0.45)" }}>
        netdisk · M1 认证阶段
      </Footer>

      <Modal
        title="修改密码"
        open={pwdOpen}
        onCancel={() => setPwdOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={onChangePassword}>
          <Form.Item
            name="old_password"
            label="原密码"
            rules={[{ required: true, message: "请输入原密码" }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 8, message: "密码至少 8 位" },
            ]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator: (_, value) =>
                  !value || getFieldValue("new_password") === value
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入的密码不一致")),
              }),
            ]}
          >
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={pwdLoading}>
            确认修改
          </Button>
        </Form>
      </Modal>
    </Layout>
  );
}
